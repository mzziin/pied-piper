"""Tests for file transfer sender, receiver, integrity verification, and corruption detection."""

import asyncio
from pathlib import Path
import pytest

from backend.protocol.framing import pack_chunk_frame
from backend.transfer.receiver import FileReceiver
from backend.transfer.sender import FileSender, IntegrityError, TransferError
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


@pytest.mark.asyncio
async def test_end_to_end_file_transfer(tmp_path: Path):
    """Verify that a binary file transferred between peers is received and byte-identical."""
    sender_dir = tmp_path / "sender"
    receiver_dir = tmp_path / "receiver"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    # Create a 64 KB binary test file with random bytes
    source_file = sender_dir / "sample_binary.dat"
    content = b"PiedPiperFileTransferTestBinaryData_1234567890\x00\xff\xfe\xab\xcd" * 1200  # ~64 KB
    source_file.write_bytes(content)

    pc1, pc2 = await create_connected_peer_pair()

    try:
        sender_progress = []
        receiver_progress = []

        def on_sender_progress(pct, sent, total):
            sender_progress.append((pct, sent, total))

        def on_receiver_progress(pct, recv, total):
            receiver_progress.append((pct, recv, total))

        sender = FileSender(
            channels=pc1.channels,
            filepath=source_file,
            chunk_size=16384,
            progress_callback=on_sender_progress,
        )
        receiver = FileReceiver(
            channels=pc2.channels,
            output_dir=receiver_dir,
            progress_callback=on_receiver_progress,
        )

        sender_summary, receiver_summary = await asyncio.gather(
            sender.send(timeout=10.0),
            receiver.receive(timeout=10.0),
        )

        # Verify summary metrics
        assert sender_summary.filename == "sample_binary.dat"
        assert receiver_summary.filename == "sample_binary.dat"
        assert sender_summary.size_bytes == len(content)
        assert receiver_summary.size_bytes == len(content)
        assert sender_summary.sha256 == receiver_summary.sha256

        # Independently verify received file exists and matches byte-for-byte
        received_file = receiver_dir / "sample_binary.dat"
        assert received_file.is_file()
        assert received_file.read_bytes() == content

        # Verify no leftover temporary part files exist
        part_files = list(receiver_dir.glob(".*.part"))
        assert len(part_files) == 0

        # Verify progress callbacks reached 100%
        assert len(sender_progress) > 0
        assert sender_progress[-1][0] == 100.0
        assert len(receiver_progress) > 0
        assert receiver_progress[-1][0] == 100.0

    finally:
        await pc1.close()
        await pc2.close()


@pytest.mark.asyncio
async def test_multimegabyte_file_transfer(tmp_path: Path):
    """Verify non-trivial multi-megabyte (5 MB) file transfer across DataChannels."""
    sender_dir = tmp_path / "sender_large"
    receiver_dir = tmp_path / "receiver_large"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    # 5 MB file (5 * 1024 * 1024 bytes) = 320 chunks of 16 KB
    large_file = sender_dir / "large_5mb_dataset.bin"
    chunk_pattern = b"PIED_PIPER_5MB_TRANSFER_BLOCK_INDEX_1234567890abcdefghijklmnopqrstuvwxyz" * 256  # 18,432 bytes
    total_target_bytes = 5 * 1024 * 1024
    content = (chunk_pattern * (total_target_bytes // len(chunk_pattern) + 1))[:total_target_bytes]
    large_file.write_bytes(content)
    assert len(content) == total_target_bytes

    pc1, pc2 = await create_connected_peer_pair()

    try:
        sender = FileSender(channels=pc1.channels, filepath=large_file, chunk_size=16384)
        receiver = FileReceiver(channels=pc2.channels, output_dir=receiver_dir)

        sender_summary, receiver_summary = await asyncio.gather(
            sender.send(timeout=60.0),
            receiver.receive(timeout=60.0),
        )

        assert sender_summary.size_bytes == total_target_bytes
        assert receiver_summary.size_bytes == total_target_bytes
        assert sender_summary.sha256 == receiver_summary.sha256
        assert sender_summary.total_chunks == 320
        assert sender_summary.duration_seconds > 0
        assert sender_summary.throughput_mbps > 0

        # Independent byte-for-byte verification
        received_large_file = receiver_dir / "large_5mb_dataset.bin"
        assert received_large_file.is_file()
        assert received_large_file.stat().st_size == total_target_bytes
        assert received_large_file.read_bytes() == content

    finally:
        await pc1.close()
        await pc2.close()


@pytest.mark.asyncio
async def test_corrupted_chunk_detection(tmp_path: Path):
    """Verify that a corrupted chunk is detected by receiver and causes clean transfer failure."""
    sender_dir = tmp_path / "sender"
    receiver_dir = tmp_path / "receiver"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    source_file = sender_dir / "corrupted_test.dat"
    content = b"CHUNK_DATA_ABCDEFG_" * 500  # Multi-chunk file
    source_file.write_bytes(content)

    pc1, pc2 = await create_connected_peer_pair()

    try:
        # Simulate corrupting data channel frames by injecting corrupted bytes in sender
        sender = FileSender(channels=pc1.channels, filepath=source_file, chunk_size=4096)
        receiver = FileReceiver(channels=pc2.channels, output_dir=receiver_dir)

        # Intercept send_data on pc1 to corrupt the second chunk
        original_send_data = pc1.channels.send_data
        call_count = 0

        def corrupted_send_data(frame: bytes):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Corrupt the payload bytes but keep original 28-byte header
                corrupted_frame = frame[:28] + b"CORRUPTED_INJECTED_DATA" + frame[28 + 23:]
                original_send_data(corrupted_frame)
            else:
                original_send_data(frame)

        pc1.channels.send_data = corrupted_send_data

        with pytest.raises((IntegrityError, TransferError)):
            await asyncio.gather(
                sender.send(timeout=5.0),
                receiver.receive(timeout=5.0),
            )

        # Verify part file was cleaned up and final file was NOT created
        assert not (receiver_dir / "corrupted_test.dat").exists()
        part_files = list(receiver_dir.glob(".*.part"))
        assert len(part_files) == 0

    finally:
        await pc1.close()
        await pc2.close()


@pytest.mark.asyncio
async def test_path_traversal_filename_rejection(tmp_path: Path):
    """Verify that path-traversal filenames in file offer are sanitized safely."""
    sender_dir = tmp_path / "sender"
    receiver_dir = tmp_path / "receiver"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    source_file = sender_dir / "malicious.txt"
    content = b"Safe content inside malicious name test"
    source_file.write_bytes(content)

    pc1, pc2 = await create_connected_peer_pair()

    try:
        sender = FileSender(channels=pc1.channels, filepath=source_file)
        receiver = FileReceiver(channels=pc2.channels, output_dir=receiver_dir)

        # Manually overwrite the offer filename to a path traversal attempt
        original_send_control = pc1.channels.send_control

        def malicious_send_control(msg):
            if isinstance(msg, dict):
                if msg.get("type") == "file_offer":
                    msg["filename"] = "../../malicious_traversal.txt"
                elif msg.get("type") == "transfer_offer" and "files" in msg and msg["files"]:
                    msg["files"][0]["filename"] = "../../malicious_traversal.txt"
            original_send_control(msg)

        pc1.channels.send_control = malicious_send_control

        sender_summary, receiver_summary = await asyncio.gather(
            sender.send(timeout=5.0),
            receiver.receive(timeout=5.0),
        )

        # Sanitized filename should be 'malicious_traversal.txt' stored strictly in receiver_dir
        expected_dest = receiver_dir / "malicious_traversal.txt"
        assert expected_dest.is_file()
        assert expected_dest.read_bytes() == content

        # Ensure no file was written outside receiver_dir
        assert not (tmp_path / "malicious_traversal.txt").exists()

    finally:
        await pc1.close()
        await pc2.close()


@pytest.mark.asyncio
async def test_empty_file_transfer(tmp_path: Path):
    """Verify 0-byte empty file transfers cleanly."""
    sender_dir = tmp_path / "sender"
    receiver_dir = tmp_path / "receiver"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    empty_file = sender_dir / "empty.txt"
    empty_file.write_bytes(b"")

    pc1, pc2 = await create_connected_peer_pair()

    try:
        sender = FileSender(channels=pc1.channels, filepath=empty_file)
        receiver = FileReceiver(channels=pc2.channels, output_dir=receiver_dir)

        sender_summary, receiver_summary = await asyncio.gather(
            sender.send(timeout=5.0),
            receiver.receive(timeout=5.0),
        )

        assert sender_summary.size_bytes == 0
        assert receiver_summary.size_bytes == 0
        received_file = receiver_dir / "empty.txt"
        assert received_file.is_file()
        assert received_file.stat().st_size == 0
    finally:
        await pc1.close()
        await pc2.close()
