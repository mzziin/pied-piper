"""Sliding window flow control and ACK management for Phase 8.

Implements the sender-side sliding window state machine, in-flight bounding,
backpressure signaling, and dynamic runtime window resizing (protocol_spec.md §5).
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class WindowError(Exception):
    """Exception raised on invalid window operations or protocol violations."""
    pass


class SlidingWindow:
    """Sender-side sliding window flow controller.

    Maintains:
    - base: index of the oldest unacknowledged chunk.
    - next_chunk_to_send: index of the next chunk to be transmitted from disk.
    - window_size: maximum number of unACKed chunks permitted in flight.
    - total_chunks: total number of chunks for the file being transferred.

    Invariant:
    - in_flight = (next_chunk_to_send - base) <= window_size at all times.
    """

    def __init__(self, total_chunks: int, window_size: int = 32) -> None:
        if total_chunks < 0:
            raise ValueError(f"total_chunks must be non-negative, got {total_chunks}")
        if window_size < 1:
            raise ValueError(f"window_size must be at least 1, got {window_size}")

        self.total_chunks: int = total_chunks
        self.window_size: int = window_size
        self.base: int = 0
        self.next_chunk_to_send: int = 0

        # Event set when there is room in the window (in_flight < window_size)
        self._space_available_event: asyncio.Event = asyncio.Event()
        self._space_available_event.set()

        # Event set when all chunks have been ACKed (base >= total_chunks)
        self._all_acked_event: asyncio.Event = asyncio.Event()
        if total_chunks == 0:
            self._all_acked_event.set()

        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def in_flight(self) -> int:
        """Number of chunks currently sent but unacknowledged."""
        return self.next_chunk_to_send - self.base

    @property
    def is_full(self) -> bool:
        """True if the window is currently at capacity or all chunks have been sent."""
        return self.in_flight >= self.window_size or self.next_chunk_to_send >= self.total_chunks

    @property
    def is_done(self) -> bool:
        """True if all chunks have been transmitted and acknowledged."""
        return self.base >= self.total_chunks

    def can_send(self) -> bool:
        """True if a new chunk can be read from disk and sent."""
        return self.in_flight < self.window_size and self.next_chunk_to_send < self.total_chunks

    async def wait_for_slot(self, timeout: Optional[float] = None) -> None:
        """Wait until window capacity is available to send the next chunk (backpressure)."""
        while not self.can_send():
            if self.is_done:
                return
            try:
                await asyncio.wait_for(self._space_available_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"Timed out waiting for sliding window slot (base={self.base}, "
                    f"next={self.next_chunk_to_send}, window_size={self.window_size})"
                )

    async def record_chunk_sent(self, chunk_index: int) -> None:
        """Record that chunk_index was transmitted and adjust backpressure availability."""
        async with self._lock:
            if chunk_index != self.next_chunk_to_send:
                raise WindowError(
                    f"Out-of-sequence send: expected {self.next_chunk_to_send}, got {chunk_index}"
                )
            if self.in_flight >= self.window_size:
                raise WindowError(
                    f"Window overflow: in_flight ({self.in_flight}) cannot exceed window_size ({self.window_size})"
                )

            self.next_chunk_to_send += 1

            if not self.can_send():
                self._space_available_event.clear()

    async def handle_ack(self, chunk_index: int) -> int:
        """Process chunk ACK from receiver.

        Advances base pointer, frees window slots, and signals waiting producers.
        Returns the number of newly acknowledged chunks.
        """
        async with self._lock:
            if chunk_index < self.base:
                logger.debug("Received duplicate/stale ACK for chunk %d (base is %d)", chunk_index, self.base)
                return 0

            if chunk_index >= self.next_chunk_to_send:
                logger.warning(
                    "Received premature ACK for chunk %d (next_chunk_to_send is %d)",
                    chunk_index,
                    self.next_chunk_to_send,
                )

            # Cumulative advance: all chunks up to chunk_index are now acknowledged
            newly_acked = (chunk_index - self.base) + 1
            self.base = chunk_index + 1

            if self.can_send():
                self._space_available_event.set()

            if self.is_done:
                self._all_acked_event.set()

            return newly_acked

    async def update_window_size(self, new_size: int) -> None:
        """Dynamically update window size at runtime (Phase 14 tuning / window_update message)."""
        if new_size < 1:
            raise ValueError(f"window_size must be at least 1, got {new_size}")

        async with self._lock:
            old_size = self.window_size
            self.window_size = new_size
            logger.info("Sliding window resized: %d -> %d", old_size, new_size)

            if self.can_send():
                self._space_available_event.set()
            else:
                self._space_available_event.clear()

    async def wait_all_acked(self, timeout: Optional[float] = None) -> None:
        """Wait until all chunks have been acknowledged by the receiver."""
        if self.is_done:
            return
        try:
            await asyncio.wait_for(self._all_acked_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Timed out waiting for all chunks to be ACKed (base={self.base}/{self.total_chunks})"
            )
