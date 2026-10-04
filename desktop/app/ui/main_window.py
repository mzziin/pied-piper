"""
Main application window for Pied Piper Desktop.
Serves as the application shell providing navigation between Home, Send, and Receive views,
and manages the local file-selection and receiver code entry workflows.
"""

import os
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
from app.controllers.transfer_controller import TransferController
from app.models.transfer_state import (
    FileInfo,
    TransferState,
    format_file_size,
    format_transfer_state,
)
from app.ui.widgets.bevel_panel import BevelPanel, BevelStyle
from app.ui.widgets.transfer_panel import TransferPanel


class MainWindow(QMainWindow):
    """
    Main application shell for Pied Piper.
    Manages top-level menus, status bar, central view stack, and transfer workflows.
    """

    # View Stack Indices
    VIEW_HOME = 0
    VIEW_SEND = 1
    VIEW_RECEIVE = 2
    VIEW_TRANSFER = 3

    def __init__(
        self,
        controller: Optional[TransferController] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller or TransferController(self)

        self.setWindowTitle("Pied Piper")
        self.resize(540, 440)
        self.setMinimumSize(460, 380)

        self._create_menus()
        self._setup_ui()
        self._bind_controller()

    @property
    def controller(self) -> TransferController:
        """Return the transfer controller instance."""
        return self._controller

    @property
    def transfer_panel(self) -> TransferPanel:
        """Return the transfer status panel widget."""
        return self._transfer_panel

    def _create_menus(self) -> None:
        """Initialize the classic Windows 95 menu bar."""
        menu_bar = self.menuBar()

        # --- File Menu ---
        file_menu = menu_bar.addMenu("&File")

        home_action = QAction("&Home", self)
        home_action.setShortcut(QKeySequence("Ctrl+H"))
        home_action.triggered.connect(self.navigate_to_home)
        file_menu.addAction(home_action)

        file_menu.addSeparator()

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut(QKeySequence("Alt+F4"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # --- Transfer Menu ---
        transfer_menu = menu_bar.addMenu("&Transfer")

        send_action = QAction("&Send File...", self)
        send_action.setShortcut(QKeySequence("Ctrl+S"))
        send_action.triggered.connect(self.navigate_to_send)
        transfer_menu.addAction(send_action)

        receive_action = QAction("&Receive File...", self)
        receive_action.setShortcut(QKeySequence("Ctrl+R"))
        receive_action.triggered.connect(self.navigate_to_receive)
        transfer_menu.addAction(receive_action)

        transfer_menu.addSeparator()

        status_action = QAction("Transfer &Status", self)
        status_action.setShortcut(QKeySequence("Ctrl+T"))
        status_action.triggered.connect(self.navigate_to_transfer)
        transfer_menu.addAction(status_action)

        # --- Help Menu ---
        help_menu = menu_bar.addMenu("&Help")

        about_action = QAction("&About Pied Piper", self)
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

    def _setup_ui(self) -> None:
        """Construct the central window layout and view stack."""
        central_widget = QWidget(self)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(6, 6, 6, 6)
        root_layout.setSpacing(6)

        # 1. Top Navy Header Banner
        self._header_label = QLabel("Pied Piper — Peer-to-Peer File Transfer")
        self._header_label.setProperty("class", "Win95Header")
        root_layout.addWidget(self._header_label)

        # 2. Central View Stack
        self._transfer_panel = TransferPanel(controller=self._controller, parent=self)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._create_home_view())
        self._stack.addWidget(self._create_send_view())
        self._stack.addWidget(self._create_receive_view())
        self._stack.addWidget(self._create_transfer_view())
        root_layout.addWidget(self._stack, 1)

        self.setCentralWidget(central_widget)

        # 3. Status Bar
        status_bar = QStatusBar(self)
        status_bar.setSizeGripEnabled(True)
        self._status_label = QLabel("Ready")
        status_bar.addWidget(self._status_label, 1)
        self.setStatusBar(status_bar)


    def _create_home_view(self) -> QWidget:
        """Create the Home / Landing view."""
        panel = BevelPanel(bevel_style=BevelStyle.SUNKEN, parent=self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Title and Subtitle
        title_label = QLabel("Pied Piper")
        title_font = title_label.font()
        title_font.setPointSize(11)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)

        subtitle_label = QLabel("Secure peer-to-peer file transfer with zero-retention architecture.")
        layout.addWidget(subtitle_label)

        prompt_label = QLabel("What would you like to do?")
        layout.addWidget(prompt_label)

        # Primary Action Buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(12)

        send_btn = QPushButton("Send File")
        send_btn.setMinimumHeight(28)
        send_btn.setMinimumWidth(110)
        send_btn.clicked.connect(self.navigate_to_send)
        button_row.addWidget(send_btn)

        recv_btn = QPushButton("Receive File")
        recv_btn.setMinimumHeight(28)
        recv_btn.setMinimumWidth(110)
        recv_btn.clicked.connect(self.navigate_to_receive)
        button_row.addWidget(recv_btn)

        button_row.addStretch()
        layout.addLayout(button_row)

        # Architecture Highlights in Inner Raised Panel
        info_panel = BevelPanel(bevel_style=BevelStyle.RAISED, parent=panel)
        info_layout = QVBoxLayout(info_panel)
        info_layout.setContentsMargins(10, 8, 10, 8)
        info_layout.setSpacing(4)

        info_header = QLabel("System Architecture:")
        info_h_font = info_header.font()
        info_h_font.setBold(True)
        info_header.setFont(info_h_font)
        info_layout.addWidget(info_header)

        desc1 = QLabel("• Direct peer-to-peer WebRTC Data Channels")
        desc2 = QLabel("• Centralized signaling coordination without server file retention")
        desc3 = QLabel("• SHA-256 chunk integrity verification and resumable transfers")
        info_layout.addWidget(desc1)
        info_layout.addWidget(desc2)
        info_layout.addWidget(desc3)

        layout.addWidget(info_panel)
        layout.addStretch()
        return panel

    def _create_send_view(self) -> QWidget:
        """Create the functional Send File file-selection view."""
        panel = BevelPanel(bevel_style=BevelStyle.SUNKEN, parent=self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # 1. Back Navigation Row
        nav_row = QHBoxLayout()
        back_btn = QPushButton("← Back")
        back_btn.setFixedWidth(75)
        back_btn.clicked.connect(self.navigate_to_home)
        nav_row.addWidget(back_btn)

        title = QLabel("Send File")
        t_font = title.font()
        t_font.setBold(True)
        title.setFont(t_font)
        nav_row.addWidget(title)
        nav_row.addStretch()
        layout.addLayout(nav_row)

        # 2. File Selection Section in Inner Raised Panel
        body_panel = BevelPanel(bevel_style=BevelStyle.RAISED, parent=panel)
        body_layout = QVBoxLayout(body_panel)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(10)

        prompt_label = QLabel("Select a file to send:")
        body_layout.addWidget(prompt_label)

        # Browse Button Row
        browse_row = QHBoxLayout()
        browse_btn = QPushButton("Browse...")
        browse_btn.setMinimumWidth(85)
        browse_btn.setMinimumHeight(24)
        browse_btn.clicked.connect(self._on_browse_file)
        browse_row.addWidget(browse_btn)
        browse_row.addStretch()
        body_layout.addLayout(browse_row)

        # Metadata Display Section
        meta_grid = QGridLayout()
        meta_grid.setContentsMargins(4, 4, 4, 4)
        meta_grid.setHorizontalSpacing(10)
        meta_grid.setVerticalSpacing(6)

        fn_heading = QLabel("Filename:")
        fn_heading.setStyleSheet("font-weight: bold;")
        self._file_name_label = QLabel("No file selected")
        self._file_name_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        meta_grid.addWidget(fn_heading, 0, 0, Qt.AlignmentFlag.AlignTop)
        meta_grid.addWidget(self._file_name_label, 0, 1)

        sz_heading = QLabel("Size:")
        sz_heading.setStyleSheet("font-weight: bold;")
        self._file_size_label = QLabel("—")
        self._file_size_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        meta_grid.addWidget(sz_heading, 1, 0, Qt.AlignmentFlag.AlignTop)
        meta_grid.addWidget(self._file_size_label, 1, 1)

        meta_grid.setColumnStretch(1, 1)
        body_layout.addLayout(meta_grid)

        # Action Row (Clear Selection & Send File)
        action_row = QHBoxLayout()
        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setFixedWidth(75)
        self._clear_btn.setEnabled(False)
        self._clear_btn.clicked.connect(self._on_clear_file)
        action_row.addWidget(self._clear_btn)

        self._send_file_btn = QPushButton("Send File")
        self._send_file_btn.setMinimumWidth(85)
        self._send_file_btn.setEnabled(False)
        self._send_file_btn.clicked.connect(self._on_start_send)
        action_row.addWidget(self._send_file_btn)

        action_row.addStretch()
        body_layout.addLayout(action_row)

        body_layout.addStretch()
        layout.addWidget(body_panel, 1)
        return panel

    def _create_receive_view(self) -> QWidget:
        """Create the functional Receive File code entry view."""
        panel = BevelPanel(bevel_style=BevelStyle.SUNKEN, parent=self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # 1. Back Navigation Row
        nav_row = QHBoxLayout()
        back_btn = QPushButton("← Back")
        back_btn.setFixedWidth(75)
        back_btn.clicked.connect(self.navigate_to_home)
        nav_row.addWidget(back_btn)

        title = QLabel("Receive File")
        t_font = title.font()
        t_font.setBold(True)
        title.setFont(t_font)
        nav_row.addWidget(title)
        nav_row.addStretch()
        layout.addLayout(nav_row)

        # 2. Code Entry Section in Inner Raised Panel
        body_panel = BevelPanel(bevel_style=BevelStyle.RAISED, parent=panel)
        body_layout = QVBoxLayout(body_panel)
        body_layout.setContentsMargins(12, 12, 12, 12)
        body_layout.setSpacing(10)

        prompt_label = QLabel("Enter the transfer code provided by the sender:")
        body_layout.addWidget(prompt_label)

        # Input Row
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self._code_input = QLineEdit()
        self._code_input.setPlaceholderText("e.g. 4AF8B2")
        self._code_input.setMaxLength(6)
        self._code_input.setMinimumWidth(120)
        self._code_input.returnPressed.connect(self._on_submit_receive_code)
        input_row.addWidget(self._code_input)

        self._continue_code_btn = QPushButton("Continue")
        self._continue_code_btn.setFixedWidth(75)
        self._continue_code_btn.clicked.connect(self._on_submit_receive_code)
        input_row.addWidget(self._continue_code_btn)

        self._clear_code_btn = QPushButton("Clear")
        self._clear_code_btn.setFixedWidth(75)
        self._clear_code_btn.clicked.connect(self._on_clear_receive_code)
        input_row.addWidget(self._clear_code_btn)

        input_row.addStretch()
        body_layout.addLayout(input_row)

        # Status Grid
        status_grid = QGridLayout()
        status_grid.setContentsMargins(4, 4, 4, 4)
        status_grid.setHorizontalSpacing(10)

        status_heading = QLabel("Status:")
        status_heading.setStyleSheet("font-weight: bold;")
        self._receive_status_label = QLabel("Waiting for transfer code")
        self._receive_status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_grid.addWidget(status_heading, 0, 0, Qt.AlignmentFlag.AlignTop)
        status_grid.addWidget(self._receive_status_label, 0, 1)
        status_grid.setColumnStretch(1, 1)
        body_layout.addLayout(status_grid)

        body_layout.addStretch()
        layout.addWidget(body_panel, 1)
        return panel

    def _create_transfer_view(self) -> QWidget:
        """Create the Transfer Status view wrapping the existing TransferPanel."""
        return self._transfer_panel

    def _on_browse_file(self) -> None:
        """Open native file dialog to select a real local file."""
        self._controller.set_state(TransferState.SELECTING_FILE)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select File to Send",
            "",
            "All Files (*.*)",
        )

        if not file_path:
            if self._controller.file_info is not None:
                self._controller.set_state(TransferState.FILE_SELECTED)
            else:
                self._controller.set_state(TransferState.IDLE)
            return

        if not os.path.isfile(file_path):
            QMessageBox.warning(
                self,
                "File Error",
                f"The selected path is not a valid regular file:\n{file_path}",
            )
            if self._controller.file_info is not None:
                self._controller.set_state(TransferState.FILE_SELECTED)
            else:
                self._controller.set_state(TransferState.IDLE)
            return

        try:
            file_size = os.path.getsize(file_path)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "File Error",
                f"Unable to read file metadata:\n{exc}",
            )
            if self._controller.file_info is not None:
                self._controller.set_state(TransferState.FILE_SELECTED)
            else:
                self._controller.set_state(TransferState.IDLE)
            return

        self._controller.select_file(file_path=file_path, file_size=file_size)

    def _on_clear_file(self) -> None:
        """Clear currently selected file from controller and reset display."""
        self._controller.clear_file()

    def _on_start_send(self) -> None:
        """Start the real send transfer operation and switch view."""
        if self._controller.start_send():
            self.navigate_to_transfer()

    def _on_submit_receive_code(self) -> None:
        """Validate entered transfer code locally and start real receive operation."""
        raw_code = self._code_input.text()
        is_valid, msg = self._controller.set_receiver_code(raw_code)

        if not is_valid:
            QMessageBox.warning(
                self,
                "Invalid Transfer Code",
                msg,
            )
            self._receive_status_label.setText("Invalid transfer code")
            self._code_input.setFocus()
            return

        # Code is valid format: update input with normalized string
        self._code_input.setText(msg)
        self._receive_status_label.setText("Code accepted.")
        if self._controller.start_receive(msg):
            self.navigate_to_transfer()

    def _on_clear_receive_code(self) -> None:
        """Clear the entered transfer code and reset receiver status."""
        self._code_input.clear()
        self._controller.clear_session_code()
        self._receive_status_label.setText("Waiting for transfer code")
        self._code_input.setFocus()


    def navigate_to_home(self) -> None:
        """Switch central view to Home."""
        self._stack.setCurrentIndex(self.VIEW_HOME)

    def navigate_to_send(self) -> None:
        """Switch central view to Send File."""
        self._stack.setCurrentIndex(self.VIEW_SEND)

    def navigate_to_receive(self) -> None:
        """Switch central view to Receive File."""
        self._stack.setCurrentIndex(self.VIEW_RECEIVE)

    def navigate_to_transfer(self) -> None:
        """Switch central view to Transfer Status."""
        self._stack.setCurrentIndex(self.VIEW_TRANSFER)

    def _show_about_dialog(self) -> None:
        """Display native About dialog with accurate technical description."""
        QMessageBox.about(
            self,
            "About Pied Piper",
            "Pied Piper v1.0\n\n"
            "A secure peer-to-peer file transfer system with zero-retention architecture.\n\n"
            "• Direct peer-to-peer WebRTC Data Channels\n"
            "• Centralized signaling without server file storage\n"
            "• SHA-256 chunk integrity verification\n"
            "• Interrupted transfer recovery and resume\n",
        )

    def _bind_controller(self) -> None:
        """Bind controller signals to window UI elements."""
        self._controller.state_changed.connect(self._on_state_changed)
        self._controller.file_selected.connect(self._on_file_selected)
        self._update_status_display(self._controller.state)

    def _on_file_selected(self, info: FileInfo) -> None:
        """Update Send View UI when a file is selected."""
        self._file_name_label.setText(info.file_name)
        self._file_size_label.setText(format_file_size(info.file_size))
        self._clear_btn.setEnabled(True)
        self._send_file_btn.setEnabled(True)

    def _on_state_changed(self, state: TransferState) -> None:
        """Handle controller state updates."""
        self._update_status_display(state)
        if state in (
            TransferState.CREATING_SESSION,
            TransferState.WAITING_FOR_RECEIVER,
            TransferState.CONNECTING,
            TransferState.TRANSFERRING,
        ):
            self.navigate_to_transfer()

        if state == TransferState.IDLE and self._controller.file_info is None:
            self._file_name_label.setText("No file selected")
            self._file_size_label.setText("—")
            self._clear_btn.setEnabled(False)
            self._send_file_btn.setEnabled(False)

    def _update_status_display(self, state: TransferState) -> None:
        """Update status bar label according to state."""
        if state == TransferState.IDLE:
            self._status_label.setText("Ready")
        elif state == TransferState.FILE_SELECTED:
            self._status_label.setText("File Selected")
        elif state == TransferState.SELECTING_FILE:
            self._status_label.setText("Selecting File...")
        else:
            self._status_label.setText(state.value)
