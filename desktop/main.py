"""Desktop application entry point."""
import os
import sys

# When running as PyInstaller onefile, fix Qt plugin path so multimedia works
if getattr(sys, "frozen", False):
    _meipass = sys._MEIPASS  # type: ignore[attr-defined]
    _plugin_path = os.path.join(_meipass, "PySide6", "plugins")
    os.environ["QT_PLUGIN_PATH"] = _plugin_path
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_plugin_path, "platforms")

from dotenv import load_dotenv

load_dotenv()

from PySide6.QtWidgets import QApplication, QMessageBox
from config import validate_runtime_config
from desktop.window import MainWindow


def main():
    app = QApplication(sys.argv)
    if getattr(sys, "frozen", False):
        from PySide6.QtCore import QCoreApplication
        _plugin_path = os.path.join(sys._MEIPASS, "PySide6", "plugins")  # type: ignore[attr-defined]
        QCoreApplication.addLibraryPath(_plugin_path)

    # Single-instance lock
    from PySide6.QtCore import QLockFile, QStandardPaths
    _lock_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    _lock = QLockFile(os.path.join(_lock_dir, "AutoVideoSrt.lock"))
    _lock.setStaleLockTime(0)
    if not _lock.tryLock(0):
        QMessageBox.warning(None, "启动失败", "AutoVideoSrt 已经在运行中，请勿重复启动。")
        sys.exit(1)
        return

    try:
        validate_runtime_config()
    except RuntimeError as e:
        QMessageBox.critical(None, "Configuration Error", str(e))
        _lock.unlock()
        sys.exit(1)
        return
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
