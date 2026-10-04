"""Tests for Phase 10 persistent transfer state with SQLite."""

import asyncio
import hashlib
from pathlib import Path
import pytest

from backend.transfer.receiver import FileReceiver
from backend.transfer.sender import FileSender
from backend.transfer.state_store import TransferStateStore
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
async def test_schema_idempotence(tmp_path: Path):
    """Verify that TransferStateStore initialization is idempotent and creates valid schema."""
    db_file = tmp_path / "idempotent.db"
    store = TransferStateStore(db_file)

    # Initialize first time
    await store.initialize()
    assert db_file.is_file()

    # Initialize second time (should be a no-op without error)
    await store.initialize()

    # Create transfer record to confirm schema is writable
    await store.record_transfer("tx-test-id", role="sender", status="in_progress")
    t = await store.get_transfer("tx-test-id")
    assert t is not None
    assert t["role"] == "sender"
    assert t["status"] == "in_progress"


@pytest.mark.asyncio
async def test_file_and_chunk_hashes_persistence(tmp_path: Path):
    """Verify file records, chunk hashes, and checkpoint updating in SQLite."""
    db_file = tmp_path / "state.db"
    store = TransferStateStore(db_file)

    transfer_id = "tx-persistence-1"
    file_id = "fid-persistence-1"
    filename = "document.pdf"
    file_path = tmp_path / filename
    sha256 = "b" * 64

    await store.record_transfer(transfer_id, role="receiver", status="in_progress")
    await store.record_file(
        file_id=file_id,
        transfer_id=transfer_id,
        filename=filename,
        file_path=file_path,
        size_bytes=40960,
        total_chunks=10,
        chunk_size_bytes=4096,
        sha256=sha256,
        highest_verified_chunk=-1,
        status="in_progress",
    )

    # Insert batch chunk hashes
    sample_hashes = [(i, hashlib.sha256(f"chunk_{i}".encode()).hexdigest(), 0) for i in range(10)]
    await store.record_chunk_hashes_batch(file_id, sample_hashes)

    # Advance checkpoint
    await store.update_file_checkpoint(file_id, 3)
    await store.record_chunk_hash(file_id, 3, sample_hashes[3][1], verified=1)

    f = await store.get_file(file_id)
    assert f is not None
    assert f["highest_verified_chunk"] == 3
    assert f["status"] == "in_progress"

    hashes = await store.get_chunk_hashes(file_id)
    assert len(hashes) == 10
    assert hashes[3]["verified"] == 1
    assert hashes[4]["verified"] == 0


@pytest.mark.asyncio
async def test_completed_transfer_persistence_on_both_sides(tmp_path: Path):
    """Verify that completed transfers show status='completed' in SQLite on both sender and receiver."""
    sender_db = tmp_path / "sender_state.db"
    receiver_db = tmp_path / "receiver_state.db"

    sender_store = TransferStateStore(sender_db)
    receiver_store = TransferStateStore(receiver_db)

    sender_dir = tmp_path / "sender_dir"
    receiver_dir = tmp_path / "receiver_dir"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    source_file = sender_dir / "complete_sample.dat"
    content = b"PERSISTENT_STATE_COMPLETED_TEST_DATA_" * 1000  # ~37 KB
    source_file.write_bytes(content)

    pc1, pc2 = await create_connected_peer_pair()

    try:
        sender = FileSender(
            channels=pc1.channels,
            filepath=source_file,
            chunk_size=8192,
            state_store=sender_store,
        )
        receiver = FileReceiver(
            channels=pc2.channels,
            output_dir=receiver_dir,
            state_store=receiver_store,
        )

        sender_summary, receiver_summary = await asyncio.gather(
            sender.send(timeout=10.0),
            receiver.receive(timeout=10.0),
        )

        assert sender_summary.sha256 == receiver_summary.sha256

        # Check sender SQLite database
        s_transfer = await sender_store.get_transfer(sender.transfer_id)
        assert s_transfer is not None
        assert s_transfer["status"] == "completed"
        assert s_transfer["role"] == "sender"

        s_file = await sender_store.get_file_by_transfer(sender.transfer_id)
        assert s_file is not None
        assert s_file["status"] == "completed"

        # Check receiver SQLite database
        r_transfer = await receiver_store.get_transfer(sender.transfer_id)
        assert r_transfer is not None
        assert r_transfer["status"] == "completed"
        assert r_transfer["role"] == "receiver"

        r_file = await receiver_store.get_file_by_transfer(sender.transfer_id)
        assert r_file is not None
        assert r_file["status"] == "completed"
        assert r_file["highest_verified_chunk"] == receiver_summary.total_chunks - 1

    finally:
        await pc1.close()
        await pc2.close()


@pytest.mark.asyncio
async def test_abrupt_interruption_kill9_checkpoint_consistency(tmp_path: Path):
    """Verify kill -9 crash consistency: SQLite highest_verified_chunk matches on-disk partial file."""
    receiver_db = tmp_path / "crash_receiver_state.db"
    receiver_store = TransferStateStore(receiver_db)

    sender_dir = tmp_path / "sender_dir"
    receiver_dir = tmp_path / "receiver_dir"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    # Create a 10-chunk file (80 KB with 8 KB chunks)
    chunk_size = 8192
    total_chunks = 10
    chunks_data = [b"C" * chunk_size for _ in range(total_chunks)]
    source_file = sender_dir / "crash_test.dat"
    source_file.write_bytes(b"".join(chunks_data))

    pc1, pc2 = await create_connected_peer_pair()

    try:
        sender = FileSender(channels=pc1.channels, filepath=source_file, chunk_size=chunk_size)
        receiver = FileReceiver(
            channels=pc2.channels,
            output_dir=receiver_dir,
            state_store=receiver_store,
        )

        # Simulate abrupt kill -9 after chunk 2 has been written, flushed, and checkpointed
        # by raising a simulated crash exception inside progress callback when chunk 2 is verified
        class SimulatedCrash(BaseException):
            pass

        def simulate_kill_9(pct, chunks_confirmed, total):
            if chunks_confirmed == 3:  # chunks 0, 1, 2 verified
                raise SimulatedCrash("Process killed abruptly (SIGKILL / kill -9 equivalent)")

        receiver.progress_callback = simulate_kill_9

        with pytest.raises(SimulatedCrash):
            await asyncio.gather(
                sender.send(timeout=5.0),
                receiver.receive(timeout=5.0),
            )

        # 1. Inspect SQLite database after simulated crash
        r_file = await receiver_store.get_file_by_transfer(sender.transfer_id)
        assert r_file is not None
        # Must show exactly chunk 2 as highest verified chunk (0, 1, 2)
        assert r_file["highest_verified_chunk"] == 2

        # 2. Inspect partial file on disk
        part_file = receiver_dir / ".crash_test.dat.part"
        assert part_file.is_file()
        partial_bytes = part_file.read_bytes()
        assert len(partial_bytes) == 3 * chunk_size

        # 3. Independently verify the bytes on disk against the chunk hashes in SQLite
        chunk_hashes = await receiver_store.get_chunk_hashes(r_file["file_id"])
        verified_hashes = {h["chunk_index"]: h["sha256"] for h in chunk_hashes if h["verified"] == 1}
        assert set(verified_hashes.keys()) == {0, 1, 2}

        for idx in range(3):
            chunk_slice = partial_bytes[idx * chunk_size : (idx + 1) * chunk_size]
            computed_digest = hashlib.sha256(chunk_slice).hexdigest()
            assert computed_digest == verified_hashes[idx]

    finally:
        await pc1.close()
        await pc2.close()
