import sys
from pathlib import Path

# Ensure both desktop and repository packages are importable when launched directly.
desktop_dir = Path(__file__).resolve().parent
project_dir = desktop_dir.parent
if str(desktop_dir) not in sys.path:
    sys.path.insert(0, str(desktop_dir))
if str(project_dir) not in sys.path:
    sys.path.insert(0, str(project_dir))

from PySide6.QtWidgets import QApplication
from app.ui.main_window import MainWindow
from app.ui.style import apply_win95_theme


def main() -> int:
    import logging
    from backend.config import get_settings
    from backend.signaling.server import stop_embedded_signaling_server

    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = QApplication(sys.argv)
    app.aboutToQuit.connect(stop_embedded_signaling_server)
    apply_win95_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
