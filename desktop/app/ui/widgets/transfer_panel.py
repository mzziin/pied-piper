"""
Windows 95 Transfer Status & Progress presentation widget for Pied Piper Desktop.
Displays file metadata, transfer lifecycle state, progress bar, speed, ETA, connection,
and integrity verification strictly driven by controller signals without fake values or timers.
"""

from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.controllers.transfer_controller import TransferController
from app.models.transfer_state import (
    FileInfo,
    TransferProgress,
    TransferState,
    format_eta,
    format_file_size,
    format_speed,
    format_transfer_state,
)
from app.ui.widgets.bevel_panel import BevelPanel, BevelStyle


class TransferPanel(BevelPanel):
    """
    Classic Windows 95 Transfer Status and Progress panel.
    Presents real-time transfer metrics and file metadata provided by TransferController.
    """

    # Active states in which a transfer can be cancelled
    CANCELLABLE_STATES = {
        TransferState.CREATING_SESSION,
        TransferState.WAITING_FOR_RECEIVER,
        TransferState.RECEIVER_CONNECTED,
        TransferState.AWAITING_ACCEPTANCE,
        TransferState.CONNECTING,
        TransferState.TRANSFERRING,
        TransferState.INTERRUPTED,
        TransferState.RESUMING,
    }

    def __init__(
        self,
        controller: Optional[TransferController] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(bevel_style=BevelStyle.RAISED, parent=parent)
        self._controller: Optional[TransferController] = None

        self._setup_ui()
        self.reset_display()

        if controller is not None:
            self.bind_controller(controller)

    def _setup_ui(self) -> None:
        """Construct the Windows 95 transfer panel layout and label controls."""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(6)

        # 1. Section Title
        title_label = QLabel("Transfer Status")
        title_font = title_label.font()
        title_font.setBold(True)
        title_label.setFont(title_font)
        root_layout.addWidget(title_label)

        # 2. File Metadata & State Grid
        info_grid = QGridLayout()
        info_grid.setContentsMargins(0, 0, 0, 0)
        info_grid.setHorizontalSpacing(10)
        info_grid.setVerticalSpacing(4)

        fn_lbl = QLabel("File:")
        fn_lbl.setStyleSheet("font-weight: bold;")
        self._filename_label = QLabel("No file selected")
        self._filename_label.setWordWrap(True)
        self._filename_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_grid.addWidget(fn_lbl, 0, 0, Qt.AlignmentFlag.AlignTop)
        info_grid.addWidget(self._filename_label, 0, 1)

        sz_lbl = QLabel("Size:")
        sz_lbl.setStyleSheet("font-weight: bold;")
        self._filesize_label = QLabel("—")
        self._filesize_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_grid.addWidget(sz_lbl, 1, 0, Qt.AlignmentFlag.AlignTop)
        info_grid.addWidget(self._filesize_label, 1, 1)

        cd_lbl = QLabel("Code:")
        cd_lbl.setStyleSheet("font-weight: bold;")
        self._code_label = QLabel("—")
        self._code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_grid.addWidget(cd_lbl, 2, 0, Qt.AlignmentFlag.AlignTop)
        info_grid.addWidget(self._code_label, 2, 1)

        st_lbl = QLabel("Status:")
        st_lbl.setStyleSheet("font-weight: bold;")
        self._status_label = QLabel("Ready")
        self._status_label.setWordWrap(True)
        self._status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info_grid.addWidget(st_lbl, 3, 0, Qt.AlignmentFlag.AlignTop)
        info_grid.addWidget(self._status_label, 3, 1)

        info_grid.setColumnStretch(1, 1)
        root_layout.addLayout(info_grid)

        # 3. Windows 95 Progress Bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        root_layout.addWidget(self._progress_bar)

        # 4. Metrics Grid (Progress details, Speed, ETA, Connection, Integrity)
        metrics_grid = QGridLayout()
        metrics_grid.setContentsMargins(0, 0, 0, 0)
        metrics_grid.setHorizontalSpacing(10)
        metrics_grid.setVerticalSpacing(4)

        prog_hdr = QLabel("Progress:")
        self._progress_label = QLabel("—")
        self._progress_label.setWordWrap(True)
        self._progress_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        metrics_grid.addWidget(prog_hdr, 0, 0, Qt.AlignmentFlag.AlignTop)
        metrics_grid.addWidget(self._progress_label, 0, 1)

        spd_hdr = QLabel("Speed:")
        self._speed_label = QLabel("—")
        self._speed_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        metrics_grid.addWidget(spd_hdr, 1, 0, Qt.AlignmentFlag.AlignTop)
        metrics_grid.addWidget(self._speed_label, 1, 1)

        eta_hdr = QLabel("ETA:")
        self._eta_label = QLabel("—")
        self._eta_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        metrics_grid.addWidget(eta_hdr, 2, 0, Qt.AlignmentFlag.AlignTop)
        metrics_grid.addWidget(self._eta_label, 2, 1)

        conn_hdr = QLabel("Connection:")
        self._connection_label = QLabel("—")
        self._connection_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        metrics_grid.addWidget(conn_hdr, 3, 0, Qt.AlignmentFlag.AlignTop)
        metrics_grid.addWidget(self._connection_label, 3, 1)

        integ_hdr = QLabel("Integrity:")
        self._integrity_label = QLabel("—")
        self._integrity_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        metrics_grid.addWidget(integ_hdr, 4, 0, Qt.AlignmentFlag.AlignTop)
        metrics_grid.addWidget(self._integrity_label, 4, 1)

        metrics_grid.setColumnStretch(1, 1)
        root_layout.addLayout(metrics_grid)

        # 5. Action Row (Cancel Button, initially disabled)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 2, 0, 0)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedWidth(75)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        root_layout.addLayout(btn_row)

        root_layout.addStretch()

    def bind_controller(self, controller: TransferController) -> None:
        """Bind controller Qt signals to update transfer panel fields dynamically."""
        self._controller = controller
        controller.file_selected.connect(self.update_file_info)
        controller.state_changed.connect(self.update_state)
        controller.progress_updated.connect(self.update_progress)
        controller.error_occurred.connect(self.update_error)
        controller.session_reset.connect(self.reset_display)
        controller.connection_updated.connect(self.update_connection)
        controller.integrity_updated.connect(self.update_integrity)

        controller.code_validated.connect(self.update_code)

        # Initialize from existing controller snapshot if available
        if controller.session_info is not None:
            self.update_file_info(controller.file_info)
            self.update_state(controller.state)
            if controller.session_code is not None:
                self.update_code(controller.session_code)
            if controller.progress is not None:
                self.update_progress(controller.progress)
            if controller.session_info.connection_type is not None:
                self.update_connection(controller.session_info.connection_type)
            if controller.session_info.integrity_verified is not None:
                self.update_integrity(controller.session_info.integrity_verified)

    def update_file_info(self, info: Optional[FileInfo]) -> None:
        """Update file name and size labels based on selected FileInfo."""
        if info is not None:
            self._filename_label.setText(info.file_name)
            self._filesize_label.setText(format_file_size(info.file_size))
        else:
            self._filename_label.setText("No file selected")
            self._filesize_label.setText("—")

    def update_code(self, code: Optional[str]) -> None:
        """Update transfer code display."""
        self._code_label.setText(code if code else "—")

    def update_state(self, state: TransferState) -> None:
        """Update transfer state text and cancel button availability."""
        self._status_label.setText(format_transfer_state(state))
        if self._controller and self._controller.session_code:
            self.update_code(self._controller.session_code)
        self._cancel_btn.setEnabled(state in self.CANCELLABLE_STATES)

    def update_progress(self, progress: Optional[TransferProgress]) -> None:
        """Update progress bar percentage and metrics labels from real transfer data."""
        if progress is None or progress.total_bytes <= 0:
            self._progress_bar.setValue(0)
            self._progress_label.setText("—")
            self._speed_label.setText("—")
            self._eta_label.setText("—")
            return

        # Calculate percentage bounded [0, 100]
        pct = min(100, max(0, int((progress.bytes_transferred / progress.total_bytes) * 100)))
        self._progress_bar.setValue(pct)

        # Detail representation: e.g. "512 KB of 1.2 MB (42%)"
        transferred_str = format_file_size(progress.bytes_transferred)
        total_str = format_file_size(progress.total_bytes)
        self._progress_label.setText(f"{transferred_str} of {total_str} ({pct}%)")

        self._speed_label.setText(format_speed(progress.speed_bps))
        self._eta_label.setText(format_eta(progress.eta_seconds))

    def update_connection(self, connection_type: Optional[str]) -> None:
        """Update connection type indicator (P2P, Relay, Connecting, Disconnected)."""
        self._connection_label.setText(connection_type if connection_type else "—")

    def update_integrity(self, verified: Optional[bool]) -> None:
        """Update integrity verification result (Verified, Failed, or —)."""
        if verified is True:
            self._integrity_label.setText("Verified")
        elif verified is False:
            self._integrity_label.setText("Failed")
        else:
            self._integrity_label.setText("—")

    def update_error(self, message: str) -> None:
        """Display error condition in status label."""
        self._status_label.setText(f"Transfer failed: {message}")
        self._cancel_btn.setEnabled(False)

    def reset_display(self) -> None:
        """Reset all panel labels and progress bar to initial neutral state."""
        self._filename_label.setText("No file selected")
        self._filesize_label.setText("—")
        self._code_label.setText("—")
        self._status_label.setText("Ready")
        self._progress_bar.setValue(0)
        self._progress_label.setText("—")
        self._speed_label.setText("—")
        self._eta_label.setText("—")
        self._connection_label.setText("—")
        self._integrity_label.setText("—")
        self._cancel_btn.setEnabled(False)

    def _on_cancel_clicked(self) -> None:
        """Invoke controller cancel when Cancel button is clicked."""
        if self._controller is not None:
            self._controller.cancel()
