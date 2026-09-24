"""
Integration tests for backend.api.transfer_api and TransferController integration.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import sys

desktop_dir = Path(__file__).resolve().parent.parent / "desktop"
if str(desktop_dir) not in sys.path:
    sys.path.insert(0, str(desktop_dir))

from PySide6.QtWidgets import QApplication

from app.controllers.transfer_controller import TransferController
from app.models.transfer_state import TransferState
from backend.api.transfer_api import (
    TransferCallbacks,
    start_receive_session,
    start_send_session,
)
from backend.transfer.sender import TransferSummary


class TestTransferAPIIntegration(unittest.TestCase):
    """Test suite for transfer API boundary and controller integration."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_callbacks_dataclass(self) -> None:
        """Verify TransferCallbacks instantiation and defaults."""
        cb = TransferCallbacks()
        self.assertIsNone(cb.on_room_created)
        self.assertIsNone(cb.on_peer_joined)
        self.assertIsNone(cb.on_connected)
        self.assertIsNone(cb.on_progress)
        self.assertIsNone(cb.on_completed)
        self.assertIsNone(cb.on_error)

    @patch("backend.api.transfer_api.SignalingClient")
    @patch("backend.api.transfer_api.establish_webrtc_connection")
    @patch("backend.api.transfer_api.FileSender")
    def test_start_send_session_lifecycle(
        self, mock_file_sender_cls, mock_establish, mock_signaling_cls
    ) -> None:
        """Verify async start_send_session lifecycle callbacks."""
        mock_signaling = AsyncMock()
        mock_signaling.create_room.return_value = "4AF8B2"
        mock_signaling_cls.return_value = mock_signaling

        mock_pc = AsyncMock()
        mock_pc.channels = MagicMock()
        mock_establish.return_value = mock_pc

        mock_summary = TransferSummary(
            filename="sample.txt",
            size_bytes=100,
            total_chunks=1,
            sha256="abc",
            duration_seconds=0.1,
            throughput_mbps=1.0,
        )
        mock_sender = AsyncMock()
        mock_sender.send.return_value = mock_summary
        mock_file_sender_cls.return_value = mock_sender

        room_codes = []
        peer_joined_calls = []
        connected_types = []
        completed_summaries = []

        callbacks = TransferCallbacks(
            on_room_created=room_codes.append,
            on_peer_joined=lambda: peer_joined_calls.append(True),
            on_connected=connected_types.append,
            on_completed=completed_summaries.append,
        )

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"Hello World")
            tmp_path = tmp.name

        try:
            summary = asyncio.run(start_send_session(filepath=tmp_path, callbacks=callbacks))
            self.assertEqual(summary, mock_summary)
            self.assertEqual(room_codes, ["4AF8B2"])
            self.assertEqual(len(peer_joined_calls), 1)
            self.assertEqual(connected_types, ["P2P"])
            self.assertEqual(completed_summaries, [mock_summary])
            mock_pc.close.assert_called()
            mock_signaling.close.assert_called()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_controller_start_send_and_cancel(self) -> None:
        """Verify TransferController start_send and cancellation."""
        controller = TransferController()

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"Test Data")
            tmp_path = tmp.name

        try:
            controller.select_file(tmp_path, 9)
            self.assertEqual(controller.state, TransferState.FILE_SELECTED)

            with patch("backend.api.transfer_api.SignalingClient"):
                res = controller.start_send()
                self.assertTrue(res)
                self.assertEqual(controller.state, TransferState.CREATING_SESSION)

                # Cancel session
                controller.cancel()
                self.assertEqual(controller.state, TransferState.CANCELLED)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_controller_start_receive_invalid_code(self) -> None:
        """Verify controller error handling on invalid receive code."""
        controller = TransferController()
        res = controller.start_receive("INVALID_CODE")
        self.assertFalse(res)
        self.assertEqual(controller.state, TransferState.FAILED)
        self.assertIsNotNone(controller.error_message)


if __name__ == "__main__":
    unittest.main()
