"""File transfer sender implementation with Phase 8 sliding window flow control."""

import asyncio
import logging
from pathlib import Path
import time
from typing import Callable, Optional
import uuid

from backend.config import get_settings
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
    WindowUpdateMessage,
    pack_data_frame,
    parse_control_message,
)
from backend.protocol.window import SlidingWindow
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
    """Orchestrates file streaming over WebRTC DataChannels using Phase 8 sliding window."""

    def __init__(
        self,
        channels: DataChannelManager,
        filepath: Path,
        chunk_size: int = 262144,
        window_size: Optional[int] = None,
        progress_callback: Optional[Callable[[float, int, int], None]] = None,
        transfer_id: Optional[str] = None,
    ) -> None:
        self.channels: DataChannelManager = channels
        self.filepath: Path = Path(filepath)
        self.chunk_size: int = chunk_size
        self.window_size: int = window_size or get_settings().sliding_window_size
        self.progress_callback: Optional[Callable[[float, int, int], None]] = progress_callback
        self.transfer_id: str = transfer_id or str(uuid.uuid4())
        self.window: Optional[SlidingWindow] = None

    async def send(self, timeout: float = 60.0) -> TransferSummary:
        """Execute the complete file transfer send workflow using sliding-window flow control."""
        if not self.filepath.is_file():
            raise FileNotFoundError(f"File not found: {self.filepath}")

        start_time = time.time()
        file_id = str(uuid.uuid4())

        # 1. Build FileManifestItem (includes per-chunk hashes for Phase 7/8 integrity)
        manifest = compute_file_manifest(
            self.filepath,
            chunk_size=self.chunk_size,
            file_id=file_id,
            include_chunk_hashes=True,
        )

        logger.info(
            "Initiating transfer %s for '%s' (%d bytes, %d chunks, window_size: %d, SHA-256: %s)",
            self.transfer_id,
            self.filepath.name,
            manifest.size,
            manifest.total_chunks,
            self.window_size,
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

        # 5. Stream chunks using sliding-window flow control
        window = SlidingWindow(total_chunks=manifest.total_chunks, window_size=self.window_size)
        self.window = window

        if manifest.total_chunks > 0:
            async def _stream_chunks() -> None:
                """Read chunks sequentially from disk, pausing when the window is full."""
                reader = FileChunkReader(self.filepath, self.chunk_size, file_id=file_id)
                for chunk in reader.iter_chunks():
                    await window.wait_for_slot(timeout=timeout)
                    frame = pack_data_frame(file_id, chunk.index, chunk.data)
                    self.channels.send_data(frame)
                    await window.record_chunk_sent(chunk.index)

            async def _receive_acks() -> None:
                """Concurrently read ACKs, NACKs, and window updates from the receiver."""
                while not window.is_done:
                    ack_str = await self.channels.receive_control(timeout=timeout)
                    ack_msg = parse_control_message(ack_str)

                    if isinstance(ack_msg, ChunkAckMessage):
                        newly_acked = await window.handle_ack(ack_msg.chunk_index)
                        if newly_acked > 0 and self.progress_callback:
                            percent = (window.base / manifest.total_chunks) * 100.0
                            self.progress_callback(percent, window.base, manifest.total_chunks)

                    elif isinstance(ack_msg, ChunkNackMessage):
                        raise IntegrityError(
                            f"Receiver reported NACK on chunk {ack_msg.chunk_index}: {ack_msg.reason}"
                        )

                    elif isinstance(ack_msg, WindowUpdateMessage):
                        await window.update_window_size(ack_msg.window_size)
                        logger.info("Sliding window dynamically updated to size %d", ack_msg.window_size)

                    elif isinstance(ack_msg, TransferFailedMessage):
                        raise TransferError(f"Receiver reported fatal failure: {ack_msg.reason}")

                    elif isinstance(ack_msg, (TransferRejectMessage,)):
                        raise TransferError(f"Receiver rejected transfer: {ack_msg.reason}")

            # Run producer and ACK consumer concurrently
            await asyncio.gather(_stream_chunks(), _receive_acks())
            await window.wait_all_acked(timeout=timeout)

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
