"""File transfer sender implementation for Phase 7 full protocol."""

import asyncio
import logging
from pathlib import Path
import time
from typing import Callable, Optional
import uuid

from backend.protocol.chunking import FileChunkReader, compute_file_manifest
from backend.protocol.framing import (
    ChunkAckMessage,
    ChunkNackMessage,
    FileCompleteMessage,
    FileStartMessage,
    TransferAcceptMessage,
    TransferCompleteMessage,
    TransferFailedMessage,
    TransferOfferMessage,
    TransferRejectMessage,
    pack_data_frame,
    parse_control_message,
)
from backend.transport.data_channels import DataChannelManager

logger = logging.getLogger(__name__)


class TransferError(Exception):
    """Exception raised when a file transfer protocol error occurs."""
    pass


class IntegrityError(TransferError):
    """Exception raised when a chunk or whole-file checksum mismatch is detected."""
    pass


class TransferSummary:
    """Summary metrics of a completed file transfer."""

    def __init__(
        self,
        filename: str,
        size_bytes: int,
        total_chunks: int,
        sha256: str,
        duration_seconds: float,
        throughput_mbps: float,
        filepath: Optional[Path] = None,
        transfer_id: Optional[str] = None,
    ) -> None:
        self.filename: str = filename
        self.size_bytes: int = size_bytes
        self.total_chunks: int = total_chunks
        self.sha256: str = sha256
        self.duration_seconds: float = duration_seconds
        self.throughput_mbps: float = throughput_mbps
        self.filepath: Optional[Path] = filepath
        self.transfer_id: Optional[str] = transfer_id

    def __repr__(self) -> str:
        return (
            f"TransferSummary(filename='{self.filename}', size={self.size_bytes}, "
            f"chunks={self.total_chunks}, duration={self.duration_seconds:.2f}s, "
            f"speed={self.throughput_mbps:.2f} Mbps)"
        )


class FileSender:
    """Orchestrates file streaming over WebRTC DataChannels using Phase 7 protocol."""

    def __init__(
        self,
        channels: DataChannelManager,
        filepath: Path,
        chunk_size: int = 262144,
        progress_callback: Optional[Callable[[float, int, int], None]] = None,
        transfer_id: Optional[str] = None,
    ) -> None:
        self.channels: DataChannelManager = channels
        self.filepath: Path = Path(filepath)
        self.chunk_size: int = chunk_size
        self.progress_callback: Optional[Callable[[float, int, int], None]] = progress_callback
        self.transfer_id: str = transfer_id or str(uuid.uuid4())

    async def send(self, timeout: float = 60.0) -> TransferSummary:
        """Execute the complete file transfer send workflow."""
        if not self.filepath.is_file():
            raise FileNotFoundError(f"File not found: {self.filepath}")

        start_time = time.time()
        file_id = str(uuid.uuid4())

        # 1. Build FileManifestItem (includes per-chunk hashes for Phase 7 integrity)
        manifest = compute_file_manifest(
            self.filepath,
            chunk_size=self.chunk_size,
            file_id=file_id,
            include_chunk_hashes=True,
        )

        logger.info(
            "Initiating transfer %s for '%s' (%d bytes, %d chunks, SHA-256: %s)",
            self.transfer_id,
            self.filepath.name,
            manifest.size,
            manifest.total_chunks,
            manifest.sha256,
        )

        # 2. Dispatch TransferOfferMessage on control channel
        offer = TransferOfferMessage(
            transfer_id=self.transfer_id,
            files=[manifest],
        )
        self.channels.send_control(offer.model_dump())

        # 3. Await receiver acceptance on control channel
        resp_str = await self.channels.receive_control(timeout=timeout)
        resp_msg = parse_control_message(resp_str)

        if isinstance(resp_msg, (TransferRejectMessage,)):
            raise TransferError(f"Receiver rejected transfer offer: {resp_msg.reason}")
        if isinstance(resp_msg, (TransferFailedMessage,)):
            raise TransferError(f"Transfer error from receiver: {resp_msg.reason}")
        if not isinstance(resp_msg, (TransferAcceptMessage,)):
            # Check legacy FileAcceptMessage
            if resp_msg.type == "file_accept":
                pass
            else:
                raise TransferError(f"Unexpected response to transfer offer: {resp_msg}")

        logger.info("Transfer offer accepted by receiver. Emitting file_start...")

        # 4. Dispatch FileStartMessage on control channel
        file_start = FileStartMessage(
            transfer_id=self.transfer_id,
            file_id=file_id,
            start_chunk_index=0,
        )
        self.channels.send_control(file_start.model_dump())

        # 5. Stream sequential chunks using Phase 7 28-byte data framing
        chunks_sent = 0
        if manifest.total_chunks > 0:
            reader = FileChunkReader(self.filepath, self.chunk_size, file_id=file_id)
            for chunk in reader.iter_chunks():
                # Pack binary frame [16B file_id + 8B index + 4B payload_len + payload]
                frame = pack_data_frame(file_id, chunk.index, chunk.data)
                self.channels.send_data(frame)

                # Await ACK on control channel (stop-and-wait baseline for Phase 7)
                ack_str = await self.channels.receive_control(timeout=timeout)
                ack_msg = parse_control_message(ack_str)

                if isinstance(ack_msg, ChunkNackMessage):
                    raise IntegrityError(f"Receiver reported NACK on chunk {chunk.index}: {ack_msg.reason}")
                if isinstance(ack_msg, TransferFailedMessage):
                    raise TransferError(f"Receiver reported fatal failure on chunk {chunk.index}: {ack_msg.reason}")
                if not isinstance(ack_msg, ChunkAckMessage):
                    raise TransferError(f"Expected ChunkAckMessage for chunk {chunk.index}, got: {ack_msg}")
                if ack_msg.chunk_index != chunk.index:
                    raise TransferError(
                        f"Chunk index mismatch in ACK: expected {chunk.index}, got {ack_msg.chunk_index}"
                    )

                chunks_sent += 1
                percent = (chunks_sent / manifest.total_chunks) * 100.0

                if self.progress_callback:
                    self.progress_callback(percent, chunks_sent, manifest.total_chunks)

        # 6. Dispatch FileCompleteMessage
        file_complete = FileCompleteMessage(
            transfer_id=self.transfer_id,
            file_id=file_id,
            sha256=manifest.sha256,
        )
        self.channels.send_control(file_complete.model_dump())

        # 7. Dispatch TransferCompleteMessage
        complete_msg = TransferCompleteMessage(
            transfer_id=self.transfer_id,
            sha256=manifest.sha256,
        )
        self.channels.send_control(complete_msg.model_dump())

        # Allow final control frames to flush across SCTP transport
        await asyncio.sleep(0.2)

        duration = max(time.time() - start_time, 0.001)
        throughput_mbps = (manifest.size * 8) / (duration * 1_000_000)

        logger.info(
            "Transfer %s of '%s' complete: %d bytes in %.2fs (%.2f Mbps)",
            self.transfer_id,
            self.filepath.name,
            manifest.size,
            duration,
            throughput_mbps,
        )

        return TransferSummary(
            filename=self.filepath.name,
            size_bytes=manifest.size,
            total_chunks=manifest.total_chunks,
            sha256=manifest.sha256,
            duration_seconds=duration,
            throughput_mbps=throughput_mbps,
            filepath=self.filepath,
            transfer_id=self.transfer_id,
        )
