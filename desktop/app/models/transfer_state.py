"""
Transfer state models and data definitions for Pied Piper Desktop.
Defines the lifecycle states, transfer metadata structures, and code validation.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

# Transfer rendezvous room code specification (matching signaling specification)
TRANSFER_CODE_LENGTH = 6
TRANSFER_CODE_CHARS = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


class TransferState(Enum):
    """Documented transfer lifecycle states for Pied Piper."""
    IDLE = "IDLE"
    SELECTING_FILE = "SELECTING_FILE"
    FILE_SELECTED = "FILE_SELECTED"
    CREATING_SESSION = "CREATING_SESSION"
    WAITING_FOR_RECEIVER = "WAITING_FOR_RECEIVER"
    RECEIVER_CONNECTED = "RECEIVER_CONNECTED"
    AWAITING_ACCEPTANCE = "AWAITING_ACCEPTANCE"
    CONNECTING = "CONNECTING"
    TRANSFERRING = "TRANSFERRING"
    INTERRUPTED = "INTERRUPTED"
    RESUMING = "RESUMING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TRANSFER_STATE_LABELS: Dict[TransferState, str] = {
    TransferState.IDLE: "Ready",
    TransferState.SELECTING_FILE: "Selecting file...",
    TransferState.FILE_SELECTED: "File selected",
    TransferState.CREATING_SESSION: "Creating session...",
    TransferState.WAITING_FOR_RECEIVER: "Waiting for receiver...",
    TransferState.RECEIVER_CONNECTED: "Receiver connected",
    TransferState.AWAITING_ACCEPTANCE: "Waiting for acceptance...",
    TransferState.CONNECTING: "Connecting...",
    TransferState.TRANSFERRING: "Transferring...",
    TransferState.INTERRUPTED: "Transfer interrupted",
    TransferState.RESUMING: "Resuming...",
    TransferState.COMPLETED: "Completed",
    TransferState.FAILED: "Transfer failed",
    TransferState.CANCELLED: "Cancelled",
}


def format_transfer_state(state: TransferState) -> str:
    """Map TransferState enum values to concise user-facing status labels."""
    return TRANSFER_STATE_LABELS.get(state, state.value)


@dataclass
class FileInfo:
    """Metadata representing a selected file for transfer."""
    file_path: str
    file_name: str
    file_size: int = 0
    sha256: Optional[str] = None


@dataclass
class TransferProgress:
    """Transfer progress information."""
    bytes_transferred: int = 0
    total_bytes: int = 0
    speed_bps: float = 0.0
    percentage: float = 0.0
    eta_seconds: Optional[float] = None


@dataclass
class TransferSessionInfo:
    """State and metadata container for a transfer session."""
    state: TransferState = TransferState.IDLE
    role: Optional[str] = None  # "sender" | "receiver" | None
    session_code: Optional[str] = None
    file_info: Optional[FileInfo] = None
    progress: Optional[TransferProgress] = None
    error_message: Optional[str] = None
    connection_type: Optional[str] = None  # None | "P2P" | "Relay" | "Connecting" | "Disconnected"
    integrity_verified: Optional[bool] = None  # None | True | False


def format_file_size(size_bytes: int) -> str:
    """
    Format an integer byte count into a standard human-readable string.
    Examples: 0 B, 512 B, 1.2 KB, 4.7 MB, 1.8 GB
    """
    if size_bytes < 0:
        return "0 B"
    if size_bytes < 1024:
        return f"{size_bytes} B"

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(size_bytes)
    unit_index = 0
    while size >= 1024.0 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1

    formatted = f"{size:.1f}"
    if formatted.endswith(".0"):
        formatted = formatted[:-2]
    return f"{formatted} {units[unit_index]}"


def format_speed(speed_bps: float) -> str:
    """
    Format transfer speed (bytes per second) into a standard human-readable string.
    Examples: —, 512 B/s, 512 KB/s, 2.4 MB/s
    """
    if speed_bps <= 0.0:
        return "—"
    if speed_bps < 1024.0:
        return f"{int(speed_bps)} B/s"

    units = ["B/s", "KB/s", "MB/s", "GB/s", "TB/s"]
    speed = float(speed_bps)
    unit_index = 0
    while speed >= 1024.0 and unit_index < len(units) - 1:
        speed /= 1024.0
        unit_index += 1

    formatted = f"{speed:.1f}"
    if formatted.endswith(".0"):
        formatted = formatted[:-2]
    return f"{formatted} {units[unit_index]}"


def format_eta(eta_seconds: Optional[float]) -> str:
    """
    Format estimated time remaining into HH:MM:SS or MM:SS format.
    Examples: —, 00:05, 01:42:17
    """
    if eta_seconds is None or eta_seconds < 0:
        return "—"

    total_seconds = int(eta_seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def validate_transfer_code(code: str) -> Tuple[bool, str]:
    """
    Locally validate a transfer code format against the documented 6-character specification.
    Returns (is_valid, normalized_code_or_error_message).
    """
    if not code or not code.strip():
        return False, "Please enter the transfer code provided by the sender."

    normalized = code.strip().upper()
    if len(normalized) != TRANSFER_CODE_LENGTH:
        return False, "Invalid transfer code. Please check the code provided by the sender."

    invalid_chars = [c for c in normalized if c not in TRANSFER_CODE_CHARS]
    if invalid_chars:
        return False, "Invalid transfer code. Please check the code provided by the sender."

    return True, normalized


