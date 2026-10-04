"""Tests for Phase 8 sliding window flow control, backpressure, and bounded streaming."""

import asyncio
from pathlib import Path
import pytest

from backend.protocol.framing import WindowUpdateMessage
from backend.protocol.window import SlidingWindow, WindowError
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


def test_sliding_window_initialization():
    """Verify default properties and parameter validation of SlidingWindow."""
    w = SlidingWindow(total_chunks=100, window_size=16)
    assert w.total_chunks == 100
    assert w.window_size == 16
    assert w.base == 0
    assert w.next_chunk_to_send == 0
    assert w.in_flight == 0
    assert w.can_send() is True
    assert w.is_full is False
    assert w.is_done is False

    # 0-chunk file completes immediately
    empty_w = SlidingWindow(total_chunks=0, window_size=10)
    assert empty_w.is_done is True
    assert empty_w.can_send() is False

    # Invalid arguments
    with pytest.raises(ValueError):
        SlidingWindow(total_chunks=-1)
    with pytest.raises(ValueError):
        SlidingWindow(total_chunks=10, window_size=0)


@pytest.mark.asyncio
async def test_sliding_window_in_flight_bounding():
    """Verify that in_flight chunks are strictly bounded by window_size."""
    w = SlidingWindow(total_chunks=20, window_size=4)

    # Send 4 chunks up to window_size
    for i in range(4):
        assert w.can_send() is True
        await w.record_chunk_sent(i)

    assert w.in_flight == 4
    assert w.is_full is True
    assert w.can_send() is False

    # Attempting to send a 5th chunk while window is full raises WindowError
    with pytest.raises(WindowError) as exc:
        await w.record_chunk_sent(4)
    assert "Window overflow" in str(exc.value)

    # Attempting out-of-order chunk index raises WindowError
    with pytest.raises(WindowError):
        await w.record_chunk_sent(99)


@pytest.mark.asyncio
async def test_sliding_window_ack_advancement():
    """Verify that incoming ACKs slide base pointer and free window slots."""
    w = SlidingWindow(total_chunks=10, window_size=3)

    # Send 3 chunks (0, 1, 2)
    for i in range(3):
        await w.record_chunk_sent(i)

    assert w.in_flight == 3
    assert w.can_send() is False

    # ACK chunk 0
    newly_acked = await w.handle_ack(0)
    assert newly_acked == 1
    assert w.base == 1
    assert w.in_flight == 2
    assert w.can_send() is True

    # Send chunk 3
    await w.record_chunk_sent(3)
    assert w.in_flight == 3

    # Cumulative ACK: ACK chunk 2 (acknowledges chunks 1 and 2)
    newly_acked = await w.handle_ack(2)
    assert newly_acked == 2
    assert w.base == 3
    assert w.in_flight == 1  # only chunk 3 is unACKed
    assert w.can_send() is True

    # Duplicate / stale ACK (chunk 1 when base is 3)
    assert await w.handle_ack(1) == 0
    assert w.base == 3


@pytest.mark.asyncio
async def test_sliding_window_backpressure_producer_consumer():
    """Verify that producer pauses when window is full and resumes as consumer ACKs."""
    total_chunks = 25
    window_size = 5
    w = SlidingWindow(total_chunks=total_chunks, window_size=window_size)

    max_observed_in_flight = 0
    sent_chunks = []
    acked_chunks = []

    async def producer():
        nonlocal max_observed_in_flight
        for i in range(total_chunks):
            await w.wait_for_slot(timeout=5.0)
            max_observed_in_flight = max(max_observed_in_flight, w.in_flight + 1)
            await w.record_chunk_sent(i)
            sent_chunks.append(i)
            # Small simulated read/transmission time
            await asyncio.sleep(0.005)

    async def consumer():
        while not w.is_done:
            await asyncio.sleep(0.01)
            # Acknowledge the oldest unacked chunk
            if w.next_chunk_to_send > w.base:
                chunk_to_ack = w.base
                await w.handle_ack(chunk_to_ack)
                acked_chunks.append(chunk_to_ack)

    await asyncio.gather(producer(), consumer())

    assert len(sent_chunks) == total_chunks
    assert len(acked_chunks) == total_chunks
    assert w.is_done is True
    # In-flight invariant: at no point did in_flight exceed window_size
    assert max_observed_in_flight <= window_size


@pytest.mark.asyncio
async def test_sliding_window_dynamic_resize():
    """Verify dynamic runtime window resizing (window_update)."""
    w = SlidingWindow(total_chunks=20, window_size=2)

    await w.record_chunk_sent(0)
    await w.record_chunk_sent(1)
    assert w.is_full is True
    assert w.can_send() is False

    # Dynamically expand window to 5
    await w.update_window_size(5)
    assert w.window_size == 5
    assert w.can_send() is True
    assert w.in_flight == 2

    # Send 3 more chunks
    await w.record_chunk_sent(2)
    await w.record_chunk_sent(3)
    await w.record_chunk_sent(4)
    assert w.in_flight == 5
    assert w.can_send() is False

    # ACK chunk 4 (all 5 sent chunks)
    await w.handle_ack(4)
    assert w.base == 5
    assert w.in_flight == 0
    assert w.can_send() is True

    # Shrink window
    await w.update_window_size(1)
    assert w.window_size == 1

    # Invalid resize
    with pytest.raises(ValueError):
        await w.update_window_size(0)


@pytest.mark.asyncio
async def test_sender_flow_control_window_update_during_transfer(tmp_path: Path):
    """Verify that sending a WindowUpdateMessage during a live transfer resizes the window cleanly."""
    sender_dir = tmp_path / "sender"
    receiver_dir = tmp_path / "receiver"
    sender_dir.mkdir()
    receiver_dir.mkdir()

    # Create a 256 KB file (16 chunks of 16 KB)
    test_file = sender_dir / "flow_control_test.bin"
    content = b"FLOW_CONTROL_WINDOW_UPDATE_TEST_DATA_" * 7000
    test_file.write_bytes(content)

    pc1, pc2 = await create_connected_peer_pair()

    try:
        sender = FileSender(
            channels=pc1.channels,
            filepath=test_file,
            chunk_size=16384,
            window_size=2,  # Start with small window of 2
        )
        receiver = FileReceiver(channels=pc2.channels, output_dir=receiver_dir)

        async def send_task():
            return await sender.send(timeout=10.0)

        async def receive_and_resize_task():
            # Intercept receive to send a WindowUpdateMessage mid-transfer
            original_send_control = pc2.channels.send_control
            call_count = 0

            def intercept_control(msg):
                nonlocal call_count
                call_count += 1
                original_send_control(msg)
                # After 4 ACKs, send a WindowUpdateMessage expanding window to 8
                if call_count == 4:
                    pc2.channels.send_control(WindowUpdateMessage(window_size=8).model_dump())

            pc2.channels.send_control = intercept_control
            return await receiver.receive(timeout=10.0)

        sender_summary, receiver_summary = await asyncio.gather(
            send_task(),
            receive_and_resize_task(),
        )

        assert sender_summary.size_bytes == len(content)
        assert receiver_summary.size_bytes == len(content)
        assert sender_summary.sha256 == receiver_summary.sha256

        # Confirm the window was resized to 8
        assert sender.window is not None
        assert sender.window.window_size == 8
        assert sender.window.is_done is True

        received_file = receiver_dir / "flow_control_test.bin"
        assert received_file.is_file()
        assert received_file.read_bytes() == content

    finally:
        await pc1.close()
        await pc2.close()
