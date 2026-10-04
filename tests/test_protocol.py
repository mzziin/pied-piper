"""Tests for protocol message framing, binary chunk formatting, and chunk hashing."""

import hashlib
from pathlib import Path
import uuid
import pytest
from pydantic import ValidationError

from backend.protocol.chunking import (
    FileChunkReader,
    compute_file_manifest,
    compute_file_metadata,
    sanitize_filename,
    verify_chunk_integrity,
)
from backend.protocol.framing import (
    ChunkAckMessage,
    ChunkNackMessage,
    DATA_FRAME_HEADER_SIZE,
    FileAcceptMessage,
    FileCompleteMessage,
    FileManifestItem,
    FileOfferMessage,
    FileRejectMessage,
    FileStartMessage,
    PingMessage,
    PongMessage,
    ResumeOffsetMessage,
    ResumeRequestMessage,
    TransferAcceptMessage,
    TransferCompleteMessage,
    TransferErrorMessage,
    TransferFailedMessage,
    TransferOfferMessage,
    TransferRejectMessage,
    WindowUpdateMessage,
    pack_chunk_frame,
    pack_data_frame,
    parse_control_message,
    unpack_chunk_frame,
    unpack_data_frame,
)


def test_control_messages_parsing_legacy():
    """Verify parsing and validation of Phase 5 legacy control channel message types."""
    offer_json = (
        '{"type": "file_offer", "filename": "test.txt", "size": 1024, '
        '"sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", '
        '"chunk_size": 16384, "total_chunks": 1}'
    )
    msg = parse_control_message(offer_json)
    assert isinstance(msg, FileOfferMessage)
    assert msg.filename == "test.txt"
    assert msg.size == 1024
    assert msg.total_chunks == 1

    msg = parse_control_message('{"type": "file_accept"}')
    assert isinstance(msg, FileAcceptMessage)

    msg = parse_control_message('{"type": "file_reject", "reason": "Disk full"}')
    assert isinstance(msg, FileRejectMessage)
    assert msg.reason == "Disk full"

    msg = parse_control_message('{"type": "chunk_ack", "chunk_index": 42}')
    assert isinstance(msg, ChunkAckMessage)
    assert msg.chunk_index == 42

    msg = parse_control_message(
        '{"type": "transfer_complete", '
        '"sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}'
    )
    assert isinstance(msg, TransferCompleteMessage)

    msg = parse_control_message('{"type": "transfer_error", "reason": "Checksum mismatch"}')
    assert isinstance(msg, TransferErrorMessage)
    assert msg.reason == "Checksum mismatch"


def test_phase7_full_control_messages_roundtrip():
    """Verify encode, decode, and schema validation of all Phase 7 control messages (§3.1)."""
    transfer_id = str(uuid.uuid4())
    file_id = str(uuid.uuid4())
    dummy_sha256 = "a" * 64

    # 1. transfer_offer
    offer = TransferOfferMessage(
        transfer_id=transfer_id,
        files=[
            FileManifestItem(
                file_id=file_id,
                filename="dataset.tar.gz",
                size=1048576,
                sha256=dummy_sha256,
                chunk_size=262144,
                total_chunks=4,
                chunk_hashes=["1" * 64, "2" * 64, "3" * 64, "4" * 64],
            )
        ],
    )
    parsed = parse_control_message(offer.model_dump())
    assert isinstance(parsed, TransferOfferMessage)
    assert parsed.transfer_id == transfer_id
    assert len(parsed.files) == 1
    assert parsed.files[0].filename == "dataset.tar.gz"
    assert parsed.files[0].chunk_hashes[0] == "1" * 64

    # 2. transfer_accept
    accept = TransferAcceptMessage(transfer_id=transfer_id)
    assert isinstance(parse_control_message(accept.model_dump()), TransferAcceptMessage)

    # 3. transfer_reject
    reject = TransferRejectMessage(transfer_id=transfer_id, reason="User declined transfer")
    parsed_reject = parse_control_message(reject.model_dump())
    assert isinstance(parsed_reject, TransferRejectMessage)
    assert parsed_reject.reason == "User declined transfer"

    # 4. file_start
    start = FileStartMessage(transfer_id=transfer_id, file_id=file_id, start_chunk_index=0)
    parsed_start = parse_control_message(start.model_dump())
    assert isinstance(parsed_start, FileStartMessage)
    assert parsed_start.start_chunk_index == 0

    # 5. chunk_ack
    ack = ChunkAckMessage(transfer_id=transfer_id, file_id=file_id, chunk_index=3)
    parsed_ack = parse_control_message(ack.model_dump())
    assert isinstance(parsed_ack, ChunkAckMessage)
    assert parsed_ack.chunk_index == 3

    # 6. chunk_nack
    nack = ChunkNackMessage(transfer_id=transfer_id, file_id=file_id, chunk_index=2, reason="Corrupt payload")
    parsed_nack = parse_control_message(nack.model_dump())
    assert isinstance(parsed_nack, ChunkNackMessage)
    assert parsed_nack.chunk_index == 2
    assert parsed_nack.reason == "Corrupt payload"

    # 7. file_complete
    file_comp = FileCompleteMessage(transfer_id=transfer_id, file_id=file_id, sha256=dummy_sha256)
    parsed_fc = parse_control_message(file_comp.model_dump())
    assert isinstance(parsed_fc, FileCompleteMessage)
    assert parsed_fc.sha256 == dummy_sha256

    # 8. transfer_complete
    tc = TransferCompleteMessage(transfer_id=transfer_id)
    assert isinstance(parse_control_message(tc.model_dump()), TransferCompleteMessage)

    # 9. transfer_failed
    tf = TransferFailedMessage(transfer_id=transfer_id, reason="Network dropped unrecoverably")
    parsed_tf = parse_control_message(tf.model_dump())
    assert isinstance(parsed_tf, TransferFailedMessage)
    assert parsed_tf.reason == "Network dropped unrecoverably"

    # 10. window_update
    wu = WindowUpdateMessage(transfer_id=transfer_id, window_size=64)
    parsed_wu = parse_control_message(wu.model_dump())
    assert isinstance(parsed_wu, WindowUpdateMessage)
    assert parsed_wu.window_size == 64

    # 11. ping / pong
    ping = PingMessage(timestamp=12345.67)
    pong = PongMessage(timestamp=12345.67)
    assert isinstance(parse_control_message(ping.model_dump()), PingMessage)
    assert isinstance(parse_control_message(pong.model_dump()), PongMessage)

    # 12. resume_request / resume_offset
    rr = ResumeRequestMessage(transfer_id=transfer_id)
    ro = ResumeOffsetMessage(transfer_id=transfer_id, file_id=file_id, resume_from_chunk=15)
    assert isinstance(parse_control_message(rr.model_dump()), ResumeRequestMessage)
    parsed_ro = parse_control_message(ro.model_dump())
    assert isinstance(parsed_ro, ResumeOffsetMessage)
    assert parsed_ro.resume_from_chunk == 15


def test_malformed_control_messages_rejected():
    """Verify that unknown or malformed control messages raise ValidationError or ValueError."""
    # Unknown type
    with pytest.raises((ValueError, ValidationError)):
        parse_control_message('{"type": "unknown_action_type"}')

    # Missing required field in transfer_offer
    with pytest.raises((ValueError, ValidationError)):
        parse_control_message('{"type": "transfer_offer"}')

    # Negative chunk index
    with pytest.raises((ValueError, ValidationError)):
        parse_control_message('{"type": "chunk_ack", "chunk_index": -1}')

    # Invalid SHA-256 length
    with pytest.raises((ValueError, ValidationError)):
        parse_control_message('{"type": "file_complete", "transfer_id": "t1", "file_id": "f1", "sha256": "bad"}')


def test_phase7_binary_data_frame_pack_and_unpack():
    """Verify Phase 7 28-byte data framing with standard and short payloads."""
    file_id = str(uuid.uuid4())
    payload = b"Phase 7 binary frame content with 28-byte header [16B file_id + 8B index + 4B len]"
    chunk_index = 42

    packed = pack_data_frame(file_id, chunk_index, payload)
    assert len(packed) == DATA_FRAME_HEADER_SIZE + len(payload)

    unpacked_fid, unpacked_index, unpacked_payload = unpack_data_frame(packed)
    assert unpacked_fid == file_id
    assert unpacked_index == chunk_index
    assert unpacked_payload == payload

    # Test short / final chunk
    short_payload = b"short"
    packed_short = pack_data_frame(file_id, 99, short_payload)
    assert len(packed_short) == DATA_FRAME_HEADER_SIZE + len(short_payload)
    u_fid, u_idx, u_payload = unpack_data_frame(packed_short)
    assert u_idx == 99
    assert u_payload == short_payload


def test_binary_data_frame_error_handling():
    """Verify unpack_data_frame rejects truncated headers and payload length mismatches."""
    file_id = str(uuid.uuid4())
    payload = b"test payload"
    packed = pack_data_frame(file_id, 0, payload)

    # Truncated header
    with pytest.raises(ValueError) as exc:
        unpack_data_frame(packed[:20])
    assert "smaller than required header" in str(exc.value)

    # Truncated payload
    with pytest.raises(ValueError) as exc:
        unpack_data_frame(packed[:-2])
    assert "payload length mismatch" in str(exc.value)


def test_binary_chunk_frame_pack_and_unpack_legacy():
    """Verify Phase 5 legacy 36-byte framing still functions."""
    payload = b"Hello, legacy chunk payload!"
    packed = pack_chunk_frame(7, payload)
    assert len(packed) == 36 + len(payload)

    unpacked_index, expected_digest, unpacked_payload = unpack_chunk_frame(packed)
    assert unpacked_index == 7
    assert unpacked_payload == payload
    assert verify_chunk_integrity(unpacked_payload, expected_digest) is True


def test_filename_sanitization():
    """Verify filename sanitization prevents directory traversal and forbidden characters."""
    assert sanitize_filename("document.pdf") == "document.pdf"
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\Windows\\System32\\cmd.exe") == "cmd.exe"
    assert sanitize_filename("/usr/local/bin/python") == "python"
    assert sanitize_filename("file:bad*name?.txt") == "file_bad_name_.txt"
    assert sanitize_filename(".hidden_file") == "hidden_file"

    with pytest.raises(ValueError):
        sanitize_filename("")
    with pytest.raises(ValueError):
        sanitize_filename("..")
    with pytest.raises(ValueError):
        sanitize_filename("/")


def test_chunk_reader_and_file_manifest(tmp_path: Path):
    """Verify compute_file_manifest and FileChunkReader generate matching chunk digests."""
    test_file = tmp_path / "test_sample.bin"
    content = b"0123456789ABCDEF" * 1024  # 16 KB file
    test_file.write_bytes(content)

    expected_sha256 = hashlib.sha256(content).hexdigest()
    manifest = compute_file_manifest(test_file, chunk_size=4096, include_chunk_hashes=True)

    assert manifest.filename == "test_sample.bin"
    assert manifest.size == len(content)
    assert manifest.total_chunks == 4
    assert manifest.sha256 == expected_sha256
    assert manifest.chunk_hashes is not None
    assert len(manifest.chunk_hashes) == 4

    # Verify each chunk read by FileChunkReader matches manifest chunk_hashes
    reader = FileChunkReader(test_file, chunk_size=4096, file_id=manifest.file_id)
    chunks = list(reader.iter_chunks())
    assert len(chunks) == 4
    for i, chunk in enumerate(chunks):
        assert chunk.index == i
        assert chunk.sha256 == manifest.chunk_hashes[i]
    assert reader.whole_file_sha256 == expected_sha256
