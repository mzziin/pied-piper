"""Message framing and binary wire format definitions for file transfer protocol.

Defines the full Phase 7 control message schemas (transfer_offer, file_start, chunk_ack/nack,
file_complete, transfer_complete, window_update, ping/pong, resume messages) and the 28-byte
binary data frame format [16B file_id + 8B chunk_index + 4B payload_length + payload].
"""

import hashlib
import json
import struct
import time
import uuid
from typing import Annotated, Any, Dict, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

# -----------------------------------------------------------------------------
# Binary Data Frame Constants
# -----------------------------------------------------------------------------
# Phase 7 wire format per protocol_spec.md §3.2:
# 16-byte file_id (UUID bytes) + 8-byte chunk_index (uint64) + 4-byte payload_length (uint32)
DATA_FRAME_HEADER_FORMAT = "!16sQI"
DATA_FRAME_HEADER_SIZE = struct.calcsize(DATA_FRAME_HEADER_FORMAT)  # 28 bytes

# Phase 5 foundational format (kept for backwards compatibility):
CHUNK_HEADER_FORMAT = "!I32s"
CHUNK_HEADER_SIZE = struct.calcsize(CHUNK_HEADER_FORMAT)  # 36 bytes


# -----------------------------------------------------------------------------
# Control Channel Messages (JSON) — Phase 7 Full Specification (§3.1)
# -----------------------------------------------------------------------------

class FileManifestItem(BaseModel):
    """Manifest entry for a single file within a batch-capable transfer offer."""
    model_config = ConfigDict(extra="ignore")
    file_id: str = Field(..., min_length=1, description="Unique file identifier (e.g. UUID)")
    filename: str = Field(..., min_length=1, description="Original filename")
    size: int = Field(..., ge=0, description="File size in bytes")
    sha256: str = Field(..., min_length=64, max_length=64, description="Whole-file SHA-256 hash")
    chunk_size: int = Field(default=262144, gt=0, description="Chunk size in bytes")
    total_chunks: int = Field(..., ge=0, description="Total number of chunks")
    chunk_hashes: Optional[List[str]] = Field(default=None, description="Optional per-chunk SHA-256 digests")


class TransferOfferMessage(BaseModel):
    """Sender offers a transfer session containing one or more files."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["transfer_offer"] = "transfer_offer"
    transfer_id: str = Field(..., min_length=1, description="Unique transfer session identifier")
    files: List[FileManifestItem] = Field(..., min_length=1, description="List of file manifests in offer")


class TransferAcceptMessage(BaseModel):
    """Receiver accepts the transfer offer."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["transfer_accept"] = "transfer_accept"
    transfer_id: str = Field(..., min_length=1)


class TransferRejectMessage(BaseModel):
    """Receiver rejects the transfer offer."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["transfer_reject"] = "transfer_reject"
    transfer_id: str = Field(..., min_length=1)
    reason: str = Field(default="Transfer rejected by user or policy")


class FileStartMessage(BaseModel):
    """Sender signals the beginning of streaming for a specific file."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["file_start"] = "file_start"
    transfer_id: str = Field(..., min_length=1)
    file_id: str = Field(..., min_length=1)
    start_chunk_index: int = Field(default=0, ge=0)


class ChunkAckMessage(BaseModel):
    """Receiver acknowledges receipt and integrity of a specific chunk."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["chunk_ack"] = "chunk_ack"
    chunk_index: int = Field(..., ge=0)
    transfer_id: Optional[str] = None
    file_id: Optional[str] = None


class ChunkNackMessage(BaseModel):
    """Receiver reports a chunk checksum mismatch or missing chunk."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["chunk_nack"] = "chunk_nack"
    chunk_index: int = Field(..., ge=0)
    transfer_id: Optional[str] = None
    file_id: Optional[str] = None
    reason: str = Field(default="Integrity verification failed")


class FileCompleteMessage(BaseModel):
    """Sender indicates all chunks for a file have been sent with its final SHA-256."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["file_complete"] = "file_complete"
    transfer_id: str = Field(..., min_length=1)
    file_id: str = Field(..., min_length=1)
    sha256: str = Field(..., min_length=64, max_length=64)


class TransferCompleteMessage(BaseModel):
    """Sender indicates the entire transfer session is successfully complete."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["transfer_complete"] = "transfer_complete"
    transfer_id: Optional[str] = None
    sha256: Optional[str] = None  # Optional for backwards compatibility


class TransferFailedMessage(BaseModel):
    """Notification of a fatal transfer failure or non-recoverable error."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["transfer_failed"] = "transfer_failed"
    reason: str = Field(..., min_length=1)
    transfer_id: Optional[str] = None


class WindowUpdateMessage(BaseModel):
    """Dynamic update to the sliding window size for flow control."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["window_update"] = "window_update"
    window_size: int = Field(..., gt=0)
    transfer_id: Optional[str] = None


class PingMessage(BaseModel):
    """Heartbeat ping over control channel."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["ping"] = "ping"
    timestamp: float = Field(default_factory=time.time)


class PongMessage(BaseModel):
    """Heartbeat pong response over control channel."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["pong"] = "pong"
    timestamp: float = Field(default_factory=time.time)


class ResumeRequestMessage(BaseModel):
    """Peer requests resumption of an interrupted transfer."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["resume_request"] = "resume_request"
    transfer_id: str = Field(..., min_length=1)


class ResumeOffsetMessage(BaseModel):
    """Receiver states verified checkpoint offset to resume from."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["resume_offset"] = "resume_offset"
    transfer_id: str = Field(..., min_length=1)
    file_id: str = Field(..., min_length=1)
    resume_from_chunk: int = Field(..., ge=0)


# -----------------------------------------------------------------------------
# Legacy Phase 5 Messages (Maintained for backward compatibility)
# -----------------------------------------------------------------------------

class FileOfferMessage(BaseModel):
    """Phase 5 single-file offer message."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["file_offer"] = "file_offer"
    filename: str = Field(..., min_length=1)
    size: int = Field(..., ge=0)
    sha256: str = Field(..., min_length=64, max_length=64)
    chunk_size: int = Field(default=262144, gt=0)
    total_chunks: int = Field(..., ge=0)


class FileAcceptMessage(BaseModel):
    """Phase 5 single-file accept message."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["file_accept"] = "file_accept"


class FileRejectMessage(BaseModel):
    """Phase 5 single-file reject message."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["file_reject"] = "file_reject"
    reason: str


class TransferErrorMessage(BaseModel):
    """Phase 5 transfer error message."""
    model_config = ConfigDict(extra="ignore")
    type: Literal["transfer_error"] = "transfer_error"
    reason: str


# -----------------------------------------------------------------------------
# Discriminated Union & Parser
# -----------------------------------------------------------------------------

ControlMessage = Annotated[
    Union[
        TransferOfferMessage,
        TransferAcceptMessage,
        TransferRejectMessage,
        FileStartMessage,
        ChunkAckMessage,
        ChunkNackMessage,
        FileCompleteMessage,
        TransferCompleteMessage,
        TransferFailedMessage,
        WindowUpdateMessage,
        PingMessage,
        PongMessage,
        ResumeRequestMessage,
        ResumeOffsetMessage,
        # Legacy types
        FileOfferMessage,
        FileAcceptMessage,
        FileRejectMessage,
        TransferErrorMessage,
    ],
    Field(discriminator="type"),
]

_control_message_adapter = TypeAdapter(ControlMessage)


def parse_control_message(data: Union[str, Dict[str, Any]]) -> ControlMessage:
    """Parse raw JSON string or dictionary into a validated ControlMessage.

    Raises ValueError / ValidationError on malformed or unrecognized types.
    """
    if isinstance(data, str):
        parsed = json.loads(data)
    else:
        parsed = data
    return _control_message_adapter.validate_python(parsed)


# -----------------------------------------------------------------------------
# Phase 7 Binary Data Channel Framing (§3.2)
# -----------------------------------------------------------------------------

def _normalize_file_id_bytes(file_id: Union[str, bytes, uuid.UUID]) -> bytes:
    """Convert string, bytes, or UUID into a fixed 16-byte raw representation."""
    if isinstance(file_id, uuid.UUID):
        return file_id.bytes
    if isinstance(file_id, bytes):
        if len(file_id) == 16:
            return file_id
        if len(file_id) < 16:
            return file_id.ljust(16, b"\0")
        return file_id[:16]
    if isinstance(file_id, str):
        try:
            return uuid.UUID(file_id).bytes
        except ValueError:
            return file_id.encode("utf-8")[:16].ljust(16, b"\0")
    raise TypeError(f"file_id must be str, bytes, or UUID, got {type(file_id)}")


def pack_data_frame(file_id: Union[str, bytes, uuid.UUID], chunk_index: int, payload: bytes) -> bytes:
    """Pack a chunk into the Phase 7 binary wire frame.

    Wire layout (28-byte header):
    [ 16 bytes: file_id UUID bytes ][ 8 bytes: uint64 chunk_index ][ 4 bytes: uint32 payload_length ][ N bytes: payload ]
    """
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if chunk_index < 0:
        raise ValueError("chunk_index must be non-negative")

    file_id_bytes = _normalize_file_id_bytes(file_id)
    payload_length = len(payload)
    header = struct.pack(DATA_FRAME_HEADER_FORMAT, file_id_bytes, chunk_index, payload_length)
    return header + payload


def unpack_data_frame(raw_frame: bytes) -> Tuple[str, int, bytes]:
    """Unpack a Phase 7 binary data frame into (file_id_str, chunk_index, payload).

    Raises ValueError if frame is shorter than 28-byte header or if payload_length mismatches.
    """
    if len(raw_frame) < DATA_FRAME_HEADER_SIZE:
        raise ValueError(
            f"Frame size ({len(raw_frame)} bytes) is smaller than required header ({DATA_FRAME_HEADER_SIZE} bytes)"
        )

    header_bytes = raw_frame[:DATA_FRAME_HEADER_SIZE]
    payload = raw_frame[DATA_FRAME_HEADER_SIZE:]
    file_id_bytes, chunk_index, payload_length = struct.unpack(DATA_FRAME_HEADER_FORMAT, header_bytes)

    if len(payload) != payload_length:
        raise ValueError(
            f"Frame payload length mismatch: header claims {payload_length} bytes, received {len(payload)} bytes"
        )

    try:
        file_id_str = str(uuid.UUID(bytes=file_id_bytes))
    except ValueError:
        file_id_str = file_id_bytes.rstrip(b"\0").decode("utf-8", errors="replace")

    return file_id_str, chunk_index, payload


# -----------------------------------------------------------------------------
# Phase 5 Legacy Chunk Frame (Maintained for backward compatibility)
# -----------------------------------------------------------------------------

def pack_chunk_frame(chunk_index: int, payload: bytes) -> bytes:
    """Phase 5 legacy pack: [ uint32 chunk_index ][ 32B sha256 ][ payload ]."""
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if chunk_index < 0:
        raise ValueError("chunk_index must be non-negative")

    digest = hashlib.sha256(payload).digest()
    header = struct.pack(CHUNK_HEADER_FORMAT, chunk_index, digest)
    return header + payload


def unpack_chunk_frame(raw_frame: bytes) -> Tuple[int, bytes, bytes]:
    """Phase 5 legacy unpack: (chunk_index, expected_digest, payload)."""
    if len(raw_frame) < CHUNK_HEADER_SIZE:
        raise ValueError(
            f"Frame size ({len(raw_frame)} bytes) is smaller than required header ({CHUNK_HEADER_SIZE} bytes)"
        )

    header_bytes = raw_frame[:CHUNK_HEADER_SIZE]
    payload = raw_frame[CHUNK_HEADER_SIZE:]
    chunk_index, expected_digest = struct.unpack(CHUNK_HEADER_FORMAT, header_bytes)
    return chunk_index, expected_digest, payload
