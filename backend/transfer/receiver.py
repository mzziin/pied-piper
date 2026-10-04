"""File transfer receiver implementation with Phase 9 receiver-confirmed checkpointing."""

import asyncio
import hashlib
import logging
from pathlib import Path
import time
from typing import Callable, Optional

from backend.protocol.chunking import sanitize_filename, verify_chunk_integrity
from backend.protocol.framing import (
    ChunkAckMessage,
    ChunkNackMessage,
    FileAcceptMessage,
    FileCompleteMessage,
    FileOfferMessage,
    FileRejectMessage,
    FileStartMessage,
    TransferAcceptMessage,
    TransferCompleteMessage,
    TransferErrorMessage,
    TransferFailedMessage,
    TransferOfferMessage,
    TransferRejectMessage,
    parse_control_message,
    unpack_chunk_frame,
    unpack_data_frame,
)
from backend.protocol.progress import ReceiverCheckpoint
from backend.transfer.sender import IntegrityError, TransferError, TransferSummary
from backend.transport.data_channels import DataChannelManager

logger = logging.getLogger(__name__)


class FileReceiver:
    """Orchestrates receiving, verifying, and checkpointing files streamed over WebRTC DataChannels."""

    def __init__(
        self,
        channels: DataChannelManager,
        output_dir: Path,
        progress_callback: Optional[Callable[[float, int, int], None]] = None,
    ) -> None:
        self.channels: DataChannelManager = channels
        self.output_dir: Path = Path(output_dir)
        self.progress_callback: Optional[Callable[[float, int, int], None]] = progress_callback
        self.checkpoint: Optional[ReceiverCheckpoint] = None

    @property
    def highest_verified_chunk(self) -> int:
        """Index of the highest contiguous verified chunk written to disk (-1 if none)."""
        return self.checkpoint.highest_verified_chunk if self.checkpoint else -1

    async def receive(self, timeout: float = 60.0) -> TransferSummary:
        """Execute the complete file receive and verification workflow."""
        start_time = time.time()

        # 1. Await offer message on control channel
        offer_str = await self.channels.receive_control(timeout=timeout)
        offer_msg = parse_control_message(offer_str)

        if isinstance(offer_msg, TransferOfferMessage):
            return await self._receive_phase7(offer_msg, start_time, timeout)
        elif isinstance(offer_msg, FileOfferMessage):
            return await self._receive_phase5_legacy(offer_msg, start_time, timeout)
        else:
            error_msg = f"Expected TransferOfferMessage, received: {offer_msg}"
            self.channels.send_control(TransferFailedMessage(reason=error_msg).model_dump())
            raise TransferError(error_msg)

    async def _receive_phase7(
        self, offer_msg: TransferOfferMessage, start_time: float, timeout: float
    ) -> TransferSummary:
        """Handle incoming Phase 7/8/9 transfer offer and receiver-confirmed checkpointing."""
        transfer_id = offer_msg.transfer_id
        if not offer_msg.files:
            raise TransferError("TransferOfferMessage contains an empty files list")

        manifest = offer_msg.files[0]

        # 2. Sanitize filename to prevent path traversal
        try:
            sanitized_name = sanitize_filename(manifest.filename)
        except ValueError as err:
            logger.error("Path traversal or invalid filename rejected: %s (%s)", manifest.filename, err)
            self.channels.send_control(
                TransferRejectMessage(
                    transfer_id=transfer_id,
                    reason=f"Rejected unsafe filename: {err}",
                ).model_dump()
            )
            raise TransferError(f"Rejected unsafe filename: {manifest.filename}") from err

        self.output_dir.mkdir(parents=True, exist_ok=True)
        final_path = self.output_dir / sanitized_name
        part_path = self.output_dir / f".{sanitized_name}.part"

        logger.info(
            "Accepting transfer %s: file '%s' -> '%s' (%d bytes, %d chunks, SHA-256: %s)",
            transfer_id,
            manifest.filename,
            final_path,
            manifest.size,
            manifest.total_chunks,
            manifest.sha256,
        )

        # Initialize receiver checkpoint tracker
        self.checkpoint = ReceiverCheckpoint(total_chunks=manifest.total_chunks)

        # 3. Accept transfer offer
        accept_msg = TransferAcceptMessage(transfer_id=transfer_id)
        self.channels.send_control(accept_msg.model_dump())

        # 4. Await FileStartMessage
        start_str = await self.channels.receive_control(timeout=timeout)
        start_msg = parse_control_message(start_str)
        if not isinstance(start_msg, FileStartMessage):
            raise TransferError(f"Expected FileStartMessage, got: {start_msg}")
        if start_msg.file_id != manifest.file_id:
            raise TransferError(
                f"File ID mismatch in FileStartMessage: expected {manifest.file_id}, got {start_msg.file_id}"
            )

        # 5. Stream, verify, and checkpoint chunks
        running_hasher = hashlib.sha256()
        chunk_hashes = manifest.chunk_hashes or []

        try:
            with part_path.open("wb") as part_file:
                for expected_index in range(manifest.total_chunks):
                    # Receive binary frame from data channel
                    frame_bytes = await self.channels.receive_data(timeout=timeout)
                    fid, chunk_index, payload = unpack_data_frame(frame_bytes)

                    # Verify file_id
                    if fid != manifest.file_id:
                        err_reason = f"File ID mismatch: expected {manifest.file_id}, got {fid}"
                        self.channels.send_control(
                            TransferFailedMessage(transfer_id=transfer_id, reason=err_reason).model_dump()
                        )
                        raise TransferError(err_reason)

                    # Verify chunk index continuity
                    if chunk_index != expected_index:
                        err_reason = f"Chunk sequence error: expected {expected_index}, got {chunk_index}"
                        self.channels.send_control(
                            TransferFailedMessage(transfer_id=transfer_id, reason=err_reason).model_dump()
                        )
                        raise TransferError(err_reason)

                    # Verify per-chunk SHA-256 integrity if manifest provided chunk hashes
                    if expected_index < len(chunk_hashes):
                        expected_digest = chunk_hashes[expected_index]
                        if not verify_chunk_integrity(payload, expected_digest):
                            err_reason = f"Per-chunk integrity verification failed on chunk {chunk_index}"
                            logger.error(err_reason)
                            self.channels.send_control(
                                ChunkNackMessage(
                                    transfer_id=transfer_id,
                                    file_id=manifest.file_id,
                                    chunk_index=chunk_index,
                                    reason=err_reason,
                                ).model_dump()
                            )
                            raise IntegrityError(err_reason)

                    # Write-then-confirm ordering (§6.2): disk write + flush MUST complete
                    # before checkpoint pointer advances or ACK is transmitted.
                    part_file.write(payload)
                    part_file.flush()

                    # Advance checkpoint pointer
                    checkpoint_advanced = self.checkpoint.record_chunk_verified(chunk_index, len(payload))
                    if not checkpoint_advanced:
                        err_reason = (
                            f"Checkpoint gap detected: expected {self.checkpoint.highest_verified_chunk + 1}, "
                            f"got {chunk_index}"
                        )
                        self.channels.send_control(
                            TransferFailedMessage(transfer_id=transfer_id, reason=err_reason).model_dump()
                        )
                        raise TransferError(err_reason)

                    running_hasher.update(payload)

                    # Send ChunkAckMessage on control channel
                    ack = ChunkAckMessage(
                        transfer_id=transfer_id,
                        file_id=manifest.file_id,
                        chunk_index=chunk_index,
                    )
                    self.channels.send_control(ack.model_dump())

                    # Progress derived strictly from confirmed contiguous checkpoint (§11)
                    if self.progress_callback:
                        self.progress_callback(
                            self.checkpoint.percent,
                            self.checkpoint.chunks_confirmed,
                            manifest.total_chunks,
                        )

            # 6. Await FileCompleteMessage on control channel
            file_comp_str = await self.channels.receive_control(timeout=timeout)
            file_comp_msg = parse_control_message(file_comp_str)
            if not isinstance(file_comp_msg, FileCompleteMessage):
                raise TransferError(f"Expected FileCompleteMessage, got: {file_comp_msg}")

            computed_sha256 = running_hasher.hexdigest()

            # Verify whole-file integrity against manifest and FileCompleteMessage
            if computed_sha256.lower() != manifest.sha256.lower():
                raise IntegrityError(
                    f"Whole-file SHA-256 mismatch with manifest: expected {manifest.sha256}, computed {computed_sha256}"
                )
            if computed_sha256.lower() != file_comp_msg.sha256.lower():
                raise IntegrityError(
                    f"Whole-file SHA-256 mismatch with file_complete: expected {file_comp_msg.sha256}, computed {computed_sha256}"
                )

            # 7. Atomically promote temporary part file to final file
            part_path.replace(final_path)
            logger.info("Transfer verified! Promoted '%s' to '%s'", part_path.name, final_path)

            # 8. Await TransferCompleteMessage on control channel
            trans_comp_str = await self.channels.receive_control(timeout=timeout)
            trans_comp_msg = parse_control_message(trans_comp_str)
            if not isinstance(trans_comp_msg, TransferCompleteMessage):
                raise TransferError(f"Expected TransferCompleteMessage, got: {trans_comp_msg}")

        except Exception:
            if part_path.exists():
                part_path.unlink(missing_ok=True)
            raise

        duration = max(time.time() - start_time, 0.001)
        bytes_total = self.checkpoint.bytes_written
        throughput_mbps = (bytes_total * 8) / (duration * 1_000_000)

        return TransferSummary(
            filename=sanitized_name,
            size_bytes=bytes_total,
            total_chunks=self.checkpoint.chunks_confirmed,
            sha256=computed_sha256,
            duration_seconds=duration,
            throughput_mbps=throughput_mbps,
            filepath=final_path,
            transfer_id=transfer_id,
        )

    async def _receive_phase5_legacy(
        self, offer_msg: FileOfferMessage, start_time: float, timeout: float
    ) -> TransferSummary:
        """Handle Phase 5 legacy file offer and 36-byte chunk frames."""
        try:
            sanitized_name = sanitize_filename(offer_msg.filename)
        except ValueError as err:
            self.channels.send_control(
                FileRejectMessage(reason=f"Rejected unsafe filename: {err}").model_dump()
            )
            raise TransferError(f"Rejected unsafe filename: {offer_msg.filename}") from err

        self.output_dir.mkdir(parents=True, exist_ok=True)
        final_path = self.output_dir / sanitized_name
        part_path = self.output_dir / f".{sanitized_name}.part"

        self.channels.send_control(FileAcceptMessage().model_dump())

        running_hasher = hashlib.sha256()
        bytes_received = 0
        chunks_received = 0

        try:
            with part_path.open("wb") as part_file:
                for expected_index in range(offer_msg.total_chunks):
                    frame_bytes = await self.channels.receive_data(timeout=timeout)
                    chunk_index, expected_digest, payload = unpack_chunk_frame(frame_bytes)

                    if chunk_index != expected_index:
                        err_reason = f"Chunk sequence error: expected {expected_index}, got {chunk_index}"
                        self.channels.send_control(TransferErrorMessage(reason=err_reason).model_dump())
                        raise TransferError(err_reason)

                    if not verify_chunk_integrity(payload, expected_digest):
                        err_reason = f"Per-chunk integrity verification failed on chunk {chunk_index}"
                        self.channels.send_control(TransferErrorMessage(reason=err_reason).model_dump())
                        raise IntegrityError(err_reason)

                    part_file.write(payload)
                    part_file.flush()
                    running_hasher.update(payload)
                    bytes_received += len(payload)
                    chunks_received += 1

                    self.channels.send_control(ChunkAckMessage(chunk_index=chunk_index).model_dump())

                    percent = (chunks_received / offer_msg.total_chunks) * 100.0
                    if self.progress_callback:
                        self.progress_callback(percent, chunks_received, offer_msg.total_chunks)

            complete_str = await self.channels.receive_control(timeout=timeout)
            complete_msg = parse_control_message(complete_str)
            if not isinstance(complete_msg, TransferCompleteMessage):
                raise TransferError(f"Expected TransferCompleteMessage, got: {complete_msg}")

            computed_sha256 = running_hasher.hexdigest()
            if computed_sha256.lower() != offer_msg.sha256.lower():
                raise IntegrityError(
                    f"Whole-file SHA-256 mismatch: expected {offer_msg.sha256}, computed {computed_sha256}"
                )

            part_path.replace(final_path)

        except Exception:
            if part_path.exists():
                part_path.unlink(missing_ok=True)
            raise

        duration = max(time.time() - start_time, 0.001)
        throughput_mbps = (bytes_received * 8) / (duration * 1_000_000)

        return TransferSummary(
            filename=sanitized_name,
            size_bytes=bytes_received,
            total_chunks=chunks_received,
            sha256=computed_sha256,
            duration_seconds=duration,
            throughput_mbps=throughput_mbps,
            filepath=final_path,
        )
