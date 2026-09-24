"""High-level async transfer API boundary for desktop integration."""

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from backend.config import Settings, get_settings
from backend.signaling.client import SignalingClient
from backend.transfer.receiver import FileReceiver
from backend.transfer.sender import FileSender, TransferSummary
from backend.transport.peer_connection import PeerConnectionWrapper, establish_webrtc_connection

logger = logging.getLogger(__name__)


@dataclass
class TransferCallbacks:
    """Callbacks for transfer lifecycle events emitted during async transfer execution."""

    on_room_created: Optional[Callable[[str], None]] = None
    on_peer_joined: Optional[Callable[[], None]] = None
    on_connected: Optional[Callable[[str], None]] = None
    on_progress: Optional[Callable[[int, int, float, Optional[float]], None]] = None  # bytes_tx, total_bytes, speed_bps, eta
    on_completed: Optional[Callable[[TransferSummary], None]] = None
    on_error: Optional[Callable[[str], None]] = None


async def start_send_session(
    filepath: Union[str, Path],
    callbacks: Optional[TransferCallbacks] = None,
    settings: Optional[Settings] = None,
    timeout: float = 60.0,
) -> TransferSummary:
    """
    Execute the sender async transfer lifecycle:
    1. Connect to signaling server
    2. Create room & emit code via callback
    3. Wait for peer to join
    4. Establish WebRTC connection
    5. Perform file send via FileSender
    6. Return TransferSummary & clean up resources
    """
    settings = settings or get_settings()
    filepath = Path(filepath)
    callbacks = callbacks or TransferCallbacks()

    signaling: Optional[SignalingClient] = None
    pc_wrapper: Optional[PeerConnectionWrapper] = None

    try:
        signaling = SignalingClient(url=settings.signaling_url)
        await signaling.connect()

        room_code = await signaling.create_room()
        if callbacks.on_room_created:
            callbacks.on_room_created(room_code)

        await signaling.wait_for_peer(timeout=settings.room_ttl_seconds)
        if callbacks.on_peer_joined:
            callbacks.on_peer_joined()

        pc_wrapper = await establish_webrtc_connection(
            role="send",
            signaling_client=signaling,
            settings=settings,
            timeout=timeout,
        )

        if callbacks.on_connected:
            callbacks.on_connected("P2P")

        start_time = time.time()
        file_size = filepath.stat().st_size if filepath.exists() else 0

        def progress_adapter(percent: float, chunks_sent: int, total_chunks: int) -> None:
            if not callbacks.on_progress:
                return
            elapsed = max(time.time() - start_time, 0.001)
            bytes_transferred = min(chunks_sent * settings.chunk_size_bytes, file_size) if chunks_sent < total_chunks else file_size
            speed_bps = (bytes_transferred * 8.0) / elapsed
            remaining_bytes = max(0, file_size - bytes_transferred)
            eta_seconds = (remaining_bytes * 8.0) / speed_bps if speed_bps > 0 and remaining_bytes > 0 else (0.0 if remaining_bytes == 0 else None)
            callbacks.on_progress(bytes_transferred, file_size, speed_bps, eta_seconds)

        sender = FileSender(
            channels=pc_wrapper.channels,
            filepath=filepath,
            chunk_size=settings.chunk_size_bytes,
            progress_callback=progress_adapter,
        )

        summary = await sender.send(timeout=timeout)

        if callbacks.on_completed:
            callbacks.on_completed(summary)

        return summary

    except Exception as exc:
        err_msg = str(exc)
        if callbacks.on_error:
            callbacks.on_error(err_msg)
        raise
    finally:
        if pc_wrapper:
            await pc_wrapper.close()
        if signaling:
            await signaling.close()


async def start_receive_session(
    code: str,
    output_dir: Union[str, Path],
    callbacks: Optional[TransferCallbacks] = None,
    settings: Optional[Settings] = None,
    timeout: float = 60.0,
) -> TransferSummary:
    """
    Execute the receiver async transfer lifecycle:
    1. Connect to signaling server
    2. Join room with room code
    3. Establish WebRTC connection
    4. Receive & verify file via FileReceiver
    5. Return TransferSummary & clean up resources
    """
    settings = settings or get_settings()
    output_dir = Path(output_dir)
    callbacks = callbacks or TransferCallbacks()

    signaling: Optional[SignalingClient] = None
    pc_wrapper: Optional[PeerConnectionWrapper] = None

    try:
        signaling = SignalingClient(url=settings.signaling_url)
        await signaling.connect()

        await signaling.join_room(code)

        pc_wrapper = await establish_webrtc_connection(
            role="receive",
            signaling_client=signaling,
            settings=settings,
            timeout=timeout,
        )

        if callbacks.on_connected:
            callbacks.on_connected("P2P")

        start_time = time.time()

        def progress_adapter(percent: float, chunks_received: int, total_chunks: int) -> None:
            if not callbacks.on_progress:
                return
            elapsed = max(time.time() - start_time, 0.001)
            approx_total_bytes = total_chunks * settings.chunk_size_bytes
            bytes_transferred = min(chunks_received * settings.chunk_size_bytes, approx_total_bytes) if chunks_received < total_chunks else approx_total_bytes
            speed_bps = (bytes_transferred * 8.0) / elapsed
            remaining_bytes = max(0, approx_total_bytes - bytes_transferred)
            eta_seconds = (remaining_bytes * 8.0) / speed_bps if speed_bps > 0 and remaining_bytes > 0 else (0.0 if remaining_bytes == 0 else None)
            callbacks.on_progress(bytes_transferred, approx_total_bytes, speed_bps, eta_seconds)

        receiver = FileReceiver(
            channels=pc_wrapper.channels,
            output_dir=output_dir,
            progress_callback=progress_adapter,
        )

        summary = await receiver.receive(timeout=timeout)

        if callbacks.on_completed:
            callbacks.on_completed(summary)

        return summary

    except Exception as exc:
        err_msg = str(exc)
        if callbacks.on_error:
            callbacks.on_error(err_msg)
        raise
    finally:
        if pc_wrapper:
            await pc_wrapper.close()
        if signaling:
            await signaling.close()
