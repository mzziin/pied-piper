"""
Windows 95 visual style system for Pied Piper Desktop.

Provides centralized color tokens, typography constants, QPalette configuration,
and comprehensive Qt Style Sheets (QSS) to achieve a genuine Windows 95 aesthetic.
"""

from PySide6.QtGui import QColor, QPalette, QFont
from PySide6.QtWidgets import QApplication

# Color Tokens
COLOR_BG = "#C0C0C0"          # Classic system grey (ButtonFace / Window background)
COLOR_LIGHT = "#FFFFFF"       # 3D Highlight (top/left bevel edge)
COLOR_MIDLIGHT = "#DFDFDF"    # 3D Midlight
COLOR_SHADOW = "#808080"      # 3D Shadow (bottom/right inner bevel)
COLOR_DARK_SHADOW = "#000000" # 3D Dark Shadow / Frame border
COLOR_NAVY = "#000080"        # Active Title bar / Header banner navy
COLOR_NAVY_TEXT = "#FFFFFF"   # Title bar text color
COLOR_TEXT = "#000000"        # Primary window text
COLOR_TEXT_DISABLED = "#808080" # Disabled text
COLOR_FIELD_BG = "#FFFFFF"    # Text box / list / input background
COLOR_SELECTION = "#000080"   # Selected item background
COLOR_SELECTION_TEXT = "#FFFFFF" # Selected item text

# Typography
FONT_FAMILY = '"Segoe UI", "Tahoma", "MS Sans Serif", "Arial", sans-serif'
FONT_SIZE_BASE = 9
FONT_SIZE_TITLE = 9

WIN95_STYLESHEET = f"""
/* Global Reset & Base */
QWidget {{
    background-color: {COLOR_BG};
    color: {COLOR_TEXT};
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE_BASE}pt;
}}

/* Main Window */
QMainWindow {{
    background-color: {COLOR_BG};
}}

/* Menu Bar & Menus */
QMenuBar {{
    background-color: {COLOR_BG};
    color: {COLOR_TEXT};
    border-bottom: 1px solid {COLOR_SHADOW};
}}

QMenuBar::item {{
    background-color: transparent;
    padding: 3px 8px;
}}

QMenuBar::item:selected {{
    background-color: {COLOR_SELECTION};
    color: {COLOR_SELECTION_TEXT};
}}

QMenuBar::item:pressed {{
    background-color: {COLOR_SELECTION};
    color: {COLOR_SELECTION_TEXT};
}}

QMenu {{
    background-color: {COLOR_BG};
    color: {COLOR_TEXT};
    border-top: 1px solid {COLOR_LIGHT};
    border-left: 1px solid {COLOR_LIGHT};
    border-right: 2px solid {COLOR_DARK_SHADOW};
    border-bottom: 2px solid {COLOR_DARK_SHADOW};
    padding: 2px;
}}

QMenu::item {{
    padding: 3px 20px 3px 15px;
}}

QMenu::item:selected {{
    background-color: {COLOR_SELECTION};
    color: {COLOR_SELECTION_TEXT};
}}

QMenu::separator {{
    height: 1px;
    background-color: {COLOR_SHADOW};
    margin: 3px 2px;
}}

/* Push Buttons */
QPushButton {{
    background-color: {COLOR_BG};
    color: {COLOR_TEXT};
    border-top: 1px solid {COLOR_LIGHT};
    border-left: 1px solid {COLOR_LIGHT};
    border-right: 2px solid {COLOR_DARK_SHADOW};
    border-bottom: 2px solid {COLOR_DARK_SHADOW};
    padding: 3px 12px;
    min-height: 18px;
    min-width: 60px;
}}

QPushButton:hover {{
    background-color: {COLOR_BG};
}}

QPushButton:pressed {{
    border-top: 2px solid {COLOR_DARK_SHADOW};
    border-left: 2px solid {COLOR_DARK_SHADOW};
    border-right: 1px solid {COLOR_LIGHT};
    border-bottom: 1px solid {COLOR_LIGHT};
    padding: 4px 11px 2px 13px;
}}

QPushButton:focus {{
    outline: 1px dotted {COLOR_DARK_SHADOW};
}}

QPushButton:disabled {{
    color: {COLOR_TEXT_DISABLED};
    border-top: 1px solid {COLOR_LIGHT};
    border-left: 1px solid {COLOR_LIGHT};
    border-right: 1px solid {COLOR_SHADOW};
    border-bottom: 1px solid {COLOR_SHADOW};
}}

/* Input Fields & Text Areas */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox {{
    background-color: {COLOR_FIELD_BG};
    color: {COLOR_TEXT};
    border-top: 2px solid {COLOR_SHADOW};
    border-left: 2px solid {COLOR_SHADOW};
    border-right: 1px solid {COLOR_LIGHT};
    border-bottom: 1px solid {COLOR_LIGHT};
    padding: 2px 4px;
    selection-background-color: {COLOR_SELECTION};
    selection-color: {COLOR_SELECTION_TEXT};
}}

QLineEdit:read-only, QTextEdit:read-only {{
    background-color: {COLOR_BG};
}}

/* Labels */
QLabel {{
    background-color: transparent;
    color: {COLOR_TEXT};
}}

/* Title / Header Banner */
.Win95Header {{
    background-color: {COLOR_NAVY};
    color: {COLOR_NAVY_TEXT};
    font-weight: bold;
    padding: 4px 8px;
    min-height: 16px;
}}

/* Group Boxes */
QGroupBox {{
    background-color: {COLOR_BG};
    border: 2px groove {COLOR_LIGHT};
    margin-top: 8px;
    padding-top: 8px;
    font-weight: bold;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 8px;
    padding: 0 4px;
    background-color: {COLOR_BG};
}}

/* Progress Bar */
QProgressBar {{
    background-color: {COLOR_FIELD_BG};
    border-top: 2px solid {COLOR_SHADOW};
    border-left: 2px solid {COLOR_SHADOW};
    border-right: 1px solid {COLOR_LIGHT};
    border-bottom: 1px solid {COLOR_LIGHT};
    text-align: center;
    color: {COLOR_TEXT};
    min-height: 16px;
    max-height: 20px;
}}

QProgressBar::chunk {{
    background-color: {COLOR_NAVY};
    width: 8px;
    margin: 1px;
}}


/* Status Bar */
QStatusBar {{
    background-color: {COLOR_BG};
}}

QStatusBar QLabel, QStatusBar::item {{
    border-top: 1px solid {COLOR_SHADOW};
    border-left: 1px solid {COLOR_SHADOW};
    border-right: 1px solid {COLOR_LIGHT};
    border-bottom: 1px solid {COLOR_LIGHT};
    padding: 1px 4px;
}}

/* ScrollBars */
QScrollBar:vertical {{
    background-color: {COLOR_BG};
    width: 16px;
    border: 1px solid {COLOR_SHADOW};
}}

QScrollBar::handle:vertical {{
    background-color: {COLOR_BG};
    border-top: 1px solid {COLOR_LIGHT};
    border-left: 1px solid {COLOR_LIGHT};
    border-right: 1px solid {COLOR_DARK_SHADOW};
    border-bottom: 1px solid {COLOR_DARK_SHADOW};
    min-height: 16px;
}}

QScrollBar:horizontal {{
    background-color: {COLOR_BG};
    height: 16px;
    border: 1px solid {COLOR_SHADOW};
}}

QScrollBar::handle:horizontal {{
    background-color: {COLOR_BG};
    border-top: 1px solid {COLOR_LIGHT};
    border-left: 1px solid {COLOR_LIGHT};
    border-right: 1px solid {COLOR_DARK_SHADOW};
    border-bottom: 1px solid {COLOR_DARK_SHADOW};
    min-width: 16px;
}}

/* Bevel Panel QSS Fallbacks */
.BevelPanel-sunken {{
    border-top: 2px solid {COLOR_SHADOW};
    border-left: 2px solid {COLOR_SHADOW};
    border-right: 1px solid {COLOR_LIGHT};
    border-bottom: 1px solid {COLOR_LIGHT};
    background-color: {COLOR_BG};
}}

.BevelPanel-raised {{
    border-top: 1px solid {COLOR_LIGHT};
    border-left: 1px solid {COLOR_LIGHT};
    border-right: 2px solid {COLOR_DARK_SHADOW};
    border-bottom: 2px solid {COLOR_DARK_SHADOW};
    background-color: {COLOR_BG};
}}

.BevelPanel-etched {{
    border: 2px groove {COLOR_LIGHT};
    background-color: {COLOR_BG};
}}
"""


def get_win95_palette() -> QPalette:
    """Create a QPalette configured with standard Windows 95 3D system colors."""
    palette = QPalette()

    bg = QColor(COLOR_BG)
    light = QColor(COLOR_LIGHT)
    midlight = QColor(COLOR_MIDLIGHT)
    shadow = QColor(COLOR_SHADOW)
    dark_shadow = QColor(COLOR_DARK_SHADOW)
    navy = QColor(COLOR_NAVY)
    navy_text = QColor(COLOR_NAVY_TEXT)
    text = QColor(COLOR_TEXT)
    field_bg = QColor(COLOR_FIELD_BG)
    disabled_text = QColor(COLOR_TEXT_DISABLED)

    # General Surfaces
    palette.setColor(QPalette.ColorRole.Window, bg)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, field_bg)
    palette.setColor(QPalette.ColorRole.AlternateBase, midlight)
    palette.setColor(QPalette.ColorRole.ToolTipBase, light)
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, bg)
    palette.setColor(QPalette.ColorRole.ButtonText, text)

    # 3D Bevel Roles
    palette.setColor(QPalette.ColorRole.Light, light)
    palette.setColor(QPalette.ColorRole.Midlight, midlight)
    palette.setColor(QPalette.ColorRole.Dark, shadow)
    palette.setColor(QPalette.ColorRole.Mid, shadow)
    palette.setColor(QPalette.ColorRole.Shadow, dark_shadow)

    # Selection Roles
    palette.setColor(QPalette.ColorRole.Highlight, navy)
    palette.setColor(QPalette.ColorRole.HighlightedText, navy_text)

    # Disabled State
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled_text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled_text)
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled_text)

    return palette


def get_win95_font() -> QFont:
    """Return the primary Windows 95 system UI font."""
    font = QFont()
    font.setFamilies(["Segoe UI", "Tahoma", "MS Sans Serif", "Arial"])
    font.setPointSize(FONT_SIZE_BASE)
    return font


def apply_win95_theme(app: QApplication) -> None:
    """
    Apply the complete Windows 95 theme to a QApplication instance.
    Sets the palette, system font, and style sheet.
    """
    app.setPalette(get_win95_palette())
    app.setFont(get_win95_font())
    app.setStyleSheet(WIN95_STYLESHEET)
