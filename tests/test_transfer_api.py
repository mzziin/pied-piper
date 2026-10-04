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

    @patch("app.controllers.transfer_controller.start_send_session")
    def test_controller_start_send_and_cancel(self, mock_start_send) -> None:
        """Verify TransferController start_send and cancellation."""
        async def fake_send(**kwargs):
            await asyncio.sleep(10.0)

        mock_start_send.side_effect = fake_send
        controller = TransferController()

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"Test Data")
            tmp_path = tmp.name

        try:
            controller.select_file(tmp_path, 9)
            self.assertEqual(controller.state, TransferState.FILE_SELECTED)

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

    def test_offline_signaling_server_raises_clean_error(self) -> None:
        """Verify that an unreachable standalone signaling server raises SignalingError and does not auto-start."""
        from backend.config import Settings
        from backend.signaling.client import SignalingError

        dead_settings = Settings(
            signaling_url="ws://127.0.0.1:59998/ws",
            _env_file=None,
        )

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(b"Test Offline")
            tmp_path = tmp.name

        try:
            with self.assertRaises(SignalingError) as ctx:
                asyncio.run(start_send_session(filepath=tmp_path, settings=dead_settings))
            self.assertIn("Cannot connect to signaling server", str(ctx.exception))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_live_transfer_api_end_to_end(self) -> None:
        """Verify full live transfer between start_send_session and start_receive_session using Phase 7 protocol."""
        import threading
        import time
        import uvicorn
        from backend.config import Settings
        from backend.signaling.server import app as sig_app

        port = 8899
        config = uvicorn.Config(app=sig_app, host="127.0.0.1", port=port, log_level="error")
        server = uvicorn.Server(config=config)
        server_thread = threading.Thread(target=server.run, daemon=True)
        server_thread.start()
        time.sleep(0.4)

        sig_url = f"ws://127.0.0.1:{port}/ws"
        custom_settings = Settings(
            signaling_url=sig_url,
            chunk_size_bytes=8192,
            _env_file=None,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source_file = temp_path / "desktop_payload.bin"
            output_dir = temp_path / "downloads"
            output_dir.mkdir()

            test_payload = b"PIED_PIPER_DESKTOP_API_PHASE7_INTEGRATION_TEST_DATA_" * 300  # ~15 KB
            source_file.write_bytes(test_payload)

            room_code_holder = []
            progress_events = []
            completed_events = []
            room_ready_event = asyncio.Event()

            async def run_live_test():
                sender_callbacks = TransferCallbacks(
                    on_room_created=lambda code: (room_code_holder.append(code), room_ready_event.set()),
                    on_progress=lambda b, tot, spd, eta: progress_events.append((b, tot)),
                    on_completed=lambda summary: completed_events.append(summary),
                )

                async def sender_task():
                    return await start_send_session(
                        filepath=source_file,
                        callbacks=sender_callbacks,
                        settings=custom_settings,
                        timeout=10.0,
                    )

                async def receiver_task():
                    await room_ready_event.wait()
                    code = room_code_holder[0]
                    return await start_receive_session(
                        code=code,
                        output_dir=output_dir,
                        settings=custom_settings,
                        timeout=10.0,
                    )

                return await asyncio.gather(sender_task(), receiver_task())

            try:
                sender_summary, receiver_summary = asyncio.run(run_live_test())
                self.assertEqual(sender_summary.size_bytes, len(test_payload))
                self.assertEqual(receiver_summary.size_bytes, len(test_payload))
                self.assertEqual(sender_summary.sha256, receiver_summary.sha256)

                received_file = output_dir / "desktop_payload.bin"
                self.assertTrue(received_file.is_file())
                self.assertEqual(received_file.read_bytes(), test_payload)
                self.assertTrue(len(progress_events) > 0)
                self.assertEqual(len(completed_events), 1)
            finally:
                server.should_exit = True
                server_thread.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
