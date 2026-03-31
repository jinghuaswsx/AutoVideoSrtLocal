"""Desktop application entry point."""
import sys
from dotenv import load_dotenv

load_dotenv()

from PySide6.QtWidgets import QApplication, QMessageBox
from config import validate_runtime_config
from desktop.window import MainWindow


def main():
    app = QApplication(sys.argv)
    try:
        validate_runtime_config()
    except RuntimeError as e:
        QMessageBox.critical(None, "Configuration Error", str(e))
        sys.exit(1)
        return
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
