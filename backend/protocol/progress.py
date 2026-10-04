"""Progress and checkpointing models for Phase 9.

Implements receiver-confirmed checkpoint tracking and structured progress event models
(protocol_spec.md §6 and §11).
"""

from dataclasses import dataclass
import logging
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProgressEvent:
    """Structured progress update emitted during file transfer.

    Represents verified, confirmed progress derived from receiver-confirmed data.
    """
    transfer_id: str
    file_id: str
    filename: str
    bytes_transferred: int
    total_bytes: int
    chunks_confirmed: int
    total_chunks: int
    percent: float
    speed_bps: float
    eta_seconds: Optional[float] = None

    def __repr__(self) -> str:
        return (
            f"ProgressEvent(file='{self.filename}', chunks={self.chunks_confirmed}/{self.total_chunks}, "
            f"percent={self.percent:.1f}%, speed={self.speed_bps / 1_000_000:.2f} Mbps)"
        )


class ReceiverCheckpoint:
    """Tracks verified, gap-free on-disk chunks on the receiver.

    Implements the invariant from protocol_spec.md §6:
    - highest_verified_chunk starts at -1 (meaning 0 chunks verified).
    - Can ONLY advance sequentially: highest_verified_chunk + 1.
    - Write-then-confirm ordering: disk write + flush MUST complete before the pointer advances.
    - Any gaps or unverified chunks prevent pointer advancement.
    """

    def __init__(self, total_chunks: int) -> None:
        self.total_chunks: int = total_chunks
        self.highest_verified_chunk: int = -1
        self.bytes_written: int = 0

    @property
    def chunks_confirmed(self) -> int:
        """Number of contiguous, verified chunks confirmed on disk."""
        return self.highest_verified_chunk + 1

    @property
    def is_complete(self) -> bool:
        """True if all expected chunks are verified and gap-free."""
        if self.total_chunks == 0:
            return True
        return self.chunks_confirmed >= self.total_chunks

    @property
    def percent(self) -> float:
        """Contiguous confirmed progress percentage (0.0 to 100.0)."""
        if self.total_chunks == 0:
            return 100.0
        return min((self.chunks_confirmed / self.total_chunks) * 100.0, 100.0)

    def record_chunk_verified(self, chunk_index: int, chunk_len: int) -> bool:
        """Attempt to advance checkpoint pointer for chunk_index.

        Returns True if pointer advanced (in-sequence, contiguous).
        Returns False if gap detected or out-of-order.
        """
        if chunk_index == self.highest_verified_chunk + 1:
            self.highest_verified_chunk = chunk_index
            self.bytes_written += chunk_len
            return True

        logger.warning(
            "Checkpoint gap detected: expected %d, got %d. Checkpoint held at %d",
            self.highest_verified_chunk + 1,
            chunk_index,
            self.highest_verified_chunk,
        )
        return False
