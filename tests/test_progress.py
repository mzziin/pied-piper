"""Tests for Phase 9 receiver-confirmed checkpointing and monotonic progress reporting."""

import asyncio
from pathlib import Path
import pytest

from backend.protocol.progress import ProgressEvent, ReceiverCheckpoint
from backend.transfer.receiver import FileReceiver
from backend.transfer.sender import FileSender
from backend.transport.peer_connection import PeerConnectionWrapper


async def create_connected_peer_pair() -> tuple[PeerConnectionWrapper, PeerConnectionWrapper]:
    """Helper to establish a direct connected WebRTC PeerConnection pair with open DataChannels."""
    pc1 = PeerConnectionWrapper(role="send")
    pc2 = PeerConnectionWrapper(role="receive")

    offer = await pc1.create_offer()
    answer = await pc2.handle_offer(offer)
    await pc1.handle_answer(answer)

    await asyncio.gather(
        pc1.wait_channels_open(timeout=5.0),
        pc2.wait_channels_open(timeout=5.0),
    )
    return pc1, pc2


def test_receiver_checkpoint_unit():
    """Verify ReceiverCheckpoint contiguous advancement and gap detection."""
    cp = ReceiverCheckpoint(total_chunks=10)
    assert cp.highest_verified_chunk == -1
    assert cp.chunks_confirmed == 0
    assert cp.percent == 0.0
    assert cp.is_complete is False

    # 1. Contiguous verification of chunk 0
    advanced = cp.record_chunk_verified(0, 1024)
    assert advanced is True
    assert cp.highest_verified_chunk == 0
    assert cp.chunks_confirmed == 1
    assert cp.percent == 10.0
    assert cp.bytes_written == 1024

    # 2. Contiguous verification of chunk 1
    advanced = cp.record_chunk_verified(1, 1024)
    assert advanced is True
    assert cp.highest_verified_chunk == 1
    assert cp.chunks_confirmed == 2
    assert cp.percent == 20.0
    assert cp.bytes_written == 2048

    # 3. Gap injection: chunk 3 arrives before chunk 2
    advanced = cp.record_chunk_verified(3, 1024)
    assert advanced is False
    # Pointer must NOT advance on gap
    assert cp.highest_verified_chunk == 1
    assert cp.chunks_confirmed == 2
    assert cp.percent == 20.0
    assert cp.bytes_written == 2048

    # 4. Fill gap with chunk 2
    advanced = cp.record_chunk_verified(2, 1024)
    assert advanced is True
    assert cp.highest_verified_chunk == 2
    assert cp.chunks_confirmed == 3
    assert cp.percent == 30.0


def test_progress_event_model():
    """Verify ProgressEvent dataclass instantiation and representation."""
    event = ProgressEvent(
        transfer_id="tx-123",
        file_id="fid-456",
        filename="archive.iso",
        bytes_transferred=5242880,
        total_bytes=10485760,
        chunks_confirmed=20,
        total_chunks=40,
        percent=50.0,
        speed_bps=20000000.0,
        eta_seconds=2.1,
    )
    assert event.percent == 50.0
    assert event.chunks_confirmed == 20
    assert event.total_chunks == 40
    assert "archive.iso" in repr(event)
    assert "50.0%" in repr(event)


@pytest.mark.asyncio
async def test_progress_monotonicity_during_transfer(tmp_path: Path):
    """Verify that progress updates on both sender and receiver are strictly monotonic."""
    sender_dir = tmp_path / "sender"
    receiver_dir = tmp_path / "receiver"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    # Create a 512 KB file (32 chunks of 16 KB)
    test_file = sender_dir / "monotonic_progress_sample.bin"
    content = b"MONOTONIC_PROGRESS_VERIFICATION_PAYLOAD_" * 13000
    test_file.write_bytes(content)

    pc1, pc2 = await create_connected_peer_pair()

    try:
        sender_progress = []
        receiver_progress = []

        def on_sender_progress(pct: float, current: int, total: int):
            sender_progress.append((pct, current, total))

        def on_receiver_progress(pct: float, current: int, total: int):
            receiver_progress.append((pct, current, total))

        sender = FileSender(
            channels=pc1.channels,
            filepath=test_file,
            chunk_size=16384,
            window_size=8,
            progress_callback=on_sender_progress,
        )
        receiver = FileReceiver(
            channels=pc2.channels,
            output_dir=receiver_dir,
            progress_callback=on_receiver_progress,
        )

        sender_summary, receiver_summary = await asyncio.gather(
            sender.send(timeout=15.0),
            receiver.receive(timeout=15.0),
        )

        assert sender_summary.size_bytes == len(content)
        assert receiver_summary.size_bytes == len(content)

        # 1. Receiver checkpoint reached final chunk
        assert receiver.highest_verified_chunk == receiver_summary.total_chunks - 1
        assert receiver.checkpoint is not None
        assert receiver.checkpoint.is_complete is True

        # 2. Check monotonic progression on sender
        assert len(sender_progress) > 0
        for i in range(len(sender_progress) - 1):
            curr_pct, curr_chunks, _ = sender_progress[i]
            next_pct, next_chunks, _ = sender_progress[i + 1]
            assert curr_pct <= next_pct, f"Sender percent non-monotonic: {curr_pct} > {next_pct}"
            assert curr_chunks <= next_chunks, f"Sender chunks non-monotonic: {curr_chunks} > {next_chunks}"

        assert sender_progress[-1][0] == 100.0
        assert sender_progress[-1][1] == receiver_summary.total_chunks

        # 3. Check monotonic progression on receiver
        assert len(receiver_progress) > 0
        for i in range(len(receiver_progress) - 1):
            curr_pct, curr_chunks, _ = receiver_progress[i]
            next_pct, next_chunks, _ = receiver_progress[i + 1]
            assert curr_pct <= next_pct, f"Receiver percent non-monotonic: {curr_pct} > {next_pct}"
            assert curr_chunks <= next_chunks, f"Receiver chunks non-monotonic: {curr_chunks} > {next_chunks}"

        assert receiver_progress[-1][0] == 100.0
        assert receiver_progress[-1][1] == receiver_summary.total_chunks

    finally:
        await pc1.close()
        await pc2.close()


@pytest.mark.asyncio
async def test_write_then_confirm_ordering(tmp_path: Path):
    """Verify that chunk writes are flushed to disk before checkpoint and ACK advance."""
    sender_dir = tmp_path / "sender"
    receiver_dir = tmp_path / "receiver"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    test_file = sender_dir / "write_order_test.bin"
    chunk_size = 8192
    content = b"FLUSH_ORDER_VERIFICATION_PAYLOAD_" * 600  # ~20 KB
    test_file.write_bytes(content)

    pc1, pc2 = await create_connected_peer_pair()

    try:
        receiver = FileReceiver(channels=pc2.channels, output_dir=receiver_dir)
        sender = FileSender(channels=pc1.channels, filepath=test_file, chunk_size=chunk_size)

        part_file_path = receiver_dir / f".{test_file.name}.part"
        observed_sizes_on_progress = []

        def check_disk_size_on_ack(pct, current, total):
            if part_file_path.exists():
                observed_sizes_on_progress.append(part_file_path.stat().st_size)

        receiver.progress_callback = check_disk_size_on_ack

        await asyncio.gather(
            sender.send(timeout=10.0),
            receiver.receive(timeout=10.0),
        )

        # Every checkpoint progress update must find the on-disk file size matching verified writes
        assert len(observed_sizes_on_progress) > 0
        for i, disk_size in enumerate(observed_sizes_on_progress):
            expected_min = (i + 1) * chunk_size if (i + 1) * chunk_size <= len(content) else len(content)
            assert disk_size >= expected_min, f"Disk write lagged behind checkpoint at step {i}: {disk_size} < {expected_min}"

    finally:
        await pc1.close()
        await pc2.close()
