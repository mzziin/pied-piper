"""
Transfer controller skeleton for Pied Piper Desktop.

Acts as the central coordination layer between the PySide6 UI and the future
underlying transfer engine / signaling backend client.
"""

import asyncio
import os
import threading
from typing import Optional, Set, Dict, Tuple, Any
from PySide6.QtCore import QObject, Signal

from backend.api.transfer_api import TransferCallbacks, start_receive_session, start_send_session
from app.models.transfer_state import (
    FileInfo,
    TransferProgress,
    TransferSessionInfo,
    TransferState,
    validate_transfer_code,
)


class TransferController(QObject):
    """
    Central controller managing transfer state transitions, selected file metadata,
    and progress notifications for the desktop application.
    """

    # Qt Signals for UI bindings
    state_changed = Signal(object)      # Emits TransferState
    file_selected = Signal(object)      # Emits FileInfo
    progress_updated = Signal(object)   # Emits TransferProgress
    error_occurred = Signal(str)        # Emits error message
    session_reset = Signal()            # Emits on session reset
    code_validated = Signal(str)        # Emits validated transfer code
    connection_updated = Signal(str)    # Emits connection type string
    integrity_updated = Signal(bool)    # Emits integrity verification status


    # Explicit allowed state transitions
    _VALID_TRANSITIONS: Dict[TransferState, Set[TransferState]] = {
        TransferState.IDLE: {
            TransferState.SELECTING_FILE,
            TransferState.FILE_SELECTED,
            TransferState.CREATING_SESSION,
            TransferState.CONNECTING,
            TransferState.AWAITING_ACCEPTANCE,
            TransferState.FAILED,
        },
        TransferState.SELECTING_FILE: {
            TransferState.IDLE,
            TransferState.FILE_SELECTED,
            TransferState.FAILED,
            TransferState.CANCELLED,
        },
        TransferState.FILE_SELECTED: {
            TransferState.IDLE,
            TransferState.SELECTING_FILE,
            TransferState.CREATING_SESSION,
            TransferState.FAILED,
            TransferState.CANCELLED,
        },
        TransferState.CREATING_SESSION: {
            TransferState.WAITING_FOR_RECEIVER,
            TransferState.CONNECTING,
            TransferState.FAILED,
            TransferState.CANCELLED,
            TransferState.IDLE,
        },
        TransferState.WAITING_FOR_RECEIVER: {
            TransferState.RECEIVER_CONNECTED,
            TransferState.CONNECTING,
            TransferState.FAILED,
            TransferState.CANCELLED,
            TransferState.IDLE,
        },
        TransferState.RECEIVER_CONNECTED: {
            TransferState.AWAITING_ACCEPTANCE,
            TransferState.CONNECTING,
            TransferState.TRANSFERRING,
            TransferState.FAILED,
            TransferState.CANCELLED,
            TransferState.IDLE,
        },
        TransferState.AWAITING_ACCEPTANCE: {
            TransferState.CONNECTING,
            TransferState.TRANSFERRING,
            TransferState.FAILED,
            TransferState.CANCELLED,
            TransferState.IDLE,
        },
        TransferState.CONNECTING: {
            TransferState.TRANSFERRING,
            TransferState.FAILED,
            TransferState.CANCELLED,
            TransferState.IDLE,
        },
        TransferState.TRANSFERRING: {
            TransferState.COMPLETED,
            TransferState.INTERRUPTED,
            TransferState.FAILED,
            TransferState.CANCELLED,
        },
        TransferState.INTERRUPTED: {
            TransferState.RESUMING,
            TransferState.FAILED,
            TransferState.CANCELLED,
            TransferState.IDLE,
        },
        TransferState.RESUMING: {
            TransferState.TRANSFERRING,
            TransferState.INTERRUPTED,
            TransferState.FAILED,
            TransferState.CANCELLED,
        },
        TransferState.COMPLETED: {
            TransferState.IDLE,
        },
        TransferState.FAILED: {
            TransferState.IDLE,
        },
        TransferState.CANCELLED: {
            TransferState.IDLE,
        },
    }

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._session_info = TransferSessionInfo(state=TransferState.IDLE)
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None
        self._async_task: Optional[asyncio.Task] = None
        self._worker_thread: Optional[threading.Thread] = None

    @property
    def state(self) -> TransferState:
        """Current transfer state."""
        return self._session_info.state

    @property
    def session_info(self) -> TransferSessionInfo:
        """Current session metadata container."""
        return self._session_info

    @property
    def session_code(self) -> Optional[str]:
        """Current session / transfer code, if any."""
        return self._session_info.session_code

    @property
    def file_info(self) -> Optional[FileInfo]:
        """Selected file metadata, if any."""
        return self._session_info.file_info

    @property
    def progress(self) -> Optional[TransferProgress]:
        """Current transfer progress, if any."""
        return self._session_info.progress

    @property
    def error_message(self) -> Optional[str]:
        """Current error message, if any."""
        return self._session_info.error_message

    def can_transition_to(self, target_state: TransferState) -> bool:
        """Check if transition to target_state is allowed from current state."""
        if target_state == self._session_info.state:
            return True
        # IDLE and FAILED are always accessible for error recovery/reset
        if target_state in (TransferState.IDLE, TransferState.FAILED):
            return True
        allowed = self._VALID_TRANSITIONS.get(self._session_info.state, set())
        return target_state in allowed

    def set_state(self, new_state: TransferState) -> bool:
        """
        Transition to a new state if valid.
        Returns True if transition occurred, False otherwise.
        """
        if new_state == self._session_info.state:
            return True

        if not self.can_transition_to(new_state):
            return False

        self._session_info.state = new_state
        self.state_changed.emit(new_state)
        return True

    def select_file(
        self,
        file_path: str,
        file_size: int = 0,
        file_name: Optional[str] = None,
        sha256: Optional[str] = None,
    ) -> bool:
        """
        Set selected file information and transition to FILE_SELECTED state.
        """
        resolved_name = file_name or os.path.basename(file_path)
        info = FileInfo(
            file_path=file_path,
            file_name=resolved_name,
            file_size=file_size,
            sha256=sha256,
        )
        self._session_info.file_info = info
        self.file_selected.emit(info)
        return self.set_state(TransferState.FILE_SELECTED)

    def clear_file(self) -> None:
        """Clear the currently selected file and return to IDLE."""
        self._session_info.file_info = None
        self.set_state(TransferState.IDLE)

    def set_session_code(self, session_code: str, role: Optional[str] = None) -> None:
        """Record session coordination parameters."""
        self._session_info.session_code = session_code
        if role is not None:
            self._session_info.role = role
        self.code_validated.emit(session_code)

    def set_receiver_code(self, code: str) -> Tuple[bool, str]:
        """
        Locally validate and store the entered receiver transfer code.
        Does not transition to network states (e.g. RECEIVER_CONNECTED).
        Returns (is_valid, normalized_code_or_error_message).
        """
        is_valid, result = validate_transfer_code(code)
        if not is_valid:
            return False, result

        self.set_session_code(result, role="receiver")
        return True, result

    def clear_session_code(self) -> None:
        """Clear the registered session/transfer code and receiver role."""
        self._session_info.session_code = None
        self._session_info.role = None

    def update_progress(
        self,
        bytes_transferred: int,
        total_bytes: int,
        speed_bps: float = 0.0,
        eta_seconds: Optional[float] = None,
    ) -> None:
        """Update transfer progress metrics and emit notification."""
        percentage = (
            min(100.0, max(0.0, (bytes_transferred / total_bytes * 100.0)))
            if total_bytes > 0
            else 0.0
        )
        progress = TransferProgress(
            bytes_transferred=bytes_transferred,
            total_bytes=total_bytes,
            speed_bps=speed_bps,
            percentage=percentage,
            eta_seconds=eta_seconds,
        )
        self._session_info.progress = progress
        self.progress_updated.emit(progress)

    def set_connection_type(self, connection_type: Optional[str]) -> None:
        """Record connection type (e.g. P2P, Relay, Connecting, Disconnected)."""
        self._session_info.connection_type = connection_type
        if connection_type is not None:
            self.connection_updated.emit(connection_type)

    def set_integrity_verified(self, verified: Optional[bool]) -> None:
        """Record SHA-256 integrity verification outcome."""
        self._session_info.integrity_verified = verified
        if verified is not None:
            self.integrity_updated.emit(verified)

    def set_error(self, message: str) -> None:
        """Record error message and transition state to FAILED."""
        self._session_info.error_message = message
        self.error_occurred.emit(message)
        self.set_state(TransferState.FAILED)

    def start_send(self, filepath: Optional[str] = None) -> bool:
        """Initiate real file transfer send operation using backend transfer API."""
        target_path = filepath or (self.file_info.file_path if self.file_info else None)
        if not target_path or not os.path.isfile(target_path):
            self.set_error("No valid file selected for sending.")
            return False

        if not self.set_state(TransferState.CREATING_SESSION):
            return False

        def _worker_run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._async_loop = loop

            async def _async_runner() -> None:
                callbacks = TransferCallbacks(
                    on_room_created=lambda code: self._handle_room_created(code),
                    on_peer_joined=lambda: self.set_state(TransferState.CONNECTING),
                    on_connected=lambda conn_type: self._handle_connected(conn_type),
                    on_progress=lambda b_tx, b_tot, spd, eta: self.update_progress(b_tx, b_tot, spd, eta),
                    on_completed=lambda summary: self._handle_completed(summary),
                    on_error=lambda err: self.set_error(err),
                )
                await start_send_session(filepath=target_path, callbacks=callbacks)

            task = loop.create_task(_async_runner())
            self._async_task = task
            try:
                loop.run_until_complete(task)
            except (asyncio.CancelledError, Exception) as exc:
                if not isinstance(exc, asyncio.CancelledError) and self.state not in (
                    TransferState.CANCELLED,
                    TransferState.FAILED,
                ):
                    self.set_error(str(exc))
            finally:
                loop.close()
                self._async_loop = None
                self._async_task = None

        thread = threading.Thread(target=_worker_run, daemon=True)
        self._worker_thread = thread
        thread.start()
        return True

    def start_receive(self, code: Optional[str] = None, output_dir: Optional[str] = None) -> bool:
        """Initiate real file transfer receive operation using backend transfer API."""
        target_code = code or self.session_code
        if not target_code:
            self.set_error("No transfer code provided.")
            return False

        is_valid, norm_code = validate_transfer_code(target_code)
        if not is_valid:
            self.set_error(norm_code)
            return False

        self.set_session_code(norm_code, role="receiver")
        if not self.set_state(TransferState.CONNECTING):
            return False

        out_path = output_dir or os.path.join(os.getcwd(), "received_files")

        def _worker_run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._async_loop = loop

            async def _async_runner() -> None:
                callbacks = TransferCallbacks(
                    on_connected=lambda conn_type: self._handle_connected(conn_type),
                    on_progress=lambda b_tx, b_tot, spd, eta: self.update_progress(b_tx, b_tot, spd, eta),
                    on_completed=lambda summary: self._handle_completed(summary),
                    on_error=lambda err: self.set_error(err),
                )
                await start_receive_session(code=norm_code, output_dir=out_path, callbacks=callbacks)

            task = loop.create_task(_async_runner())
            self._async_task = task
            try:
                loop.run_until_complete(task)
            except (asyncio.CancelledError, Exception) as exc:
                if not isinstance(exc, asyncio.CancelledError) and self.state not in (
                    TransferState.CANCELLED,
                    TransferState.FAILED,
                ):
                    self.set_error(str(exc))
            finally:
                loop.close()
                self._async_loop = None
                self._async_task = None

        thread = threading.Thread(target=_worker_run, daemon=True)
        self._worker_thread = thread
        thread.start()
        return True

    def _handle_room_created(self, code: str) -> None:
        self.set_session_code(code, role="sender")
        self.set_state(TransferState.WAITING_FOR_RECEIVER)

    def _handle_connected(self, connection_type: str) -> None:
        self.set_connection_type(connection_type)
        self.set_state(TransferState.TRANSFERRING)

    def _handle_completed(self, summary: Any) -> None:
        if hasattr(summary, "filename") and hasattr(summary, "size_bytes"):
            if self.file_info is None:
                self.select_file(
                    file_path=str(getattr(summary, "filepath", summary.filename)),
                    file_size=summary.size_bytes,
                    file_name=summary.filename,
                    sha256=getattr(summary, "sha256", None),
                )
        self.set_integrity_verified(True)
        self.set_state(TransferState.COMPLETED)

    def cancel(self) -> None:
        """Cancel the current transfer session and cleanly terminate backend tasks."""
        if self._session_info.state not in (
            TransferState.IDLE,
            TransferState.COMPLETED,
            TransferState.FAILED,
            TransferState.CANCELLED,
        ):
            self.set_state(TransferState.CANCELLED)
        else:
            self.set_state(TransferState.IDLE)

        if self._async_loop and self._async_task and not self._async_task.done():
            self._async_loop.call_soon_threadsafe(self._async_task.cancel)

    def reset(self) -> None:
        """Reset the controller to initial IDLE state and clear session data."""
        self.cancel()
        self._session_info = TransferSessionInfo(state=TransferState.IDLE)
        self.state_changed.emit(TransferState.IDLE)
        self.session_reset.emit()
