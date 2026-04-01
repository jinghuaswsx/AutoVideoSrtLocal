"""Tests for desktop/main.py entry point."""
import sys
from unittest.mock import MagicMock, patch


def test_config_error_shows_dialog_and_exits():
    """When validate_runtime_config raises RuntimeError, show dialog and exit 1."""
    mock_app = MagicMock()
    mock_app_cls = MagicMock(return_value=mock_app)
    mock_window_cls = MagicMock()

    with patch.dict("sys.modules", {"desktop.window": MagicMock(MainWindow=mock_window_cls)}), \
         patch("desktop.main.QApplication", mock_app_cls, create=True), \
         patch("desktop.main.QMessageBox", create=True) as mock_msgbox, \
         patch("desktop.main.validate_runtime_config", side_effect=RuntimeError("bad config"), create=True), \
         patch("desktop.main.sys") as mock_sys:
        mock_sys.argv = []
        mock_sys.exit = MagicMock()

        import importlib
        import desktop.main as dm
        importlib.reload(dm)

        dm.QApplication = mock_app_cls
        dm.QMessageBox = mock_msgbox
        dm.validate_runtime_config = MagicMock(side_effect=RuntimeError("bad config"))
        dm.sys = mock_sys

        dm.main()

    mock_msgbox.critical.assert_called_once()
    args = mock_msgbox.critical.call_args[0]
    assert "bad config" in str(args[2])
    mock_sys.exit.assert_called_once_with(1)


def test_valid_config_creates_and_shows_window():
    """When config is valid, MainWindow is created, shown, and app.exec() called."""
    mock_app = MagicMock()
    mock_app_cls = MagicMock(return_value=mock_app)
    mock_window = MagicMock()
    mock_window_cls = MagicMock(return_value=mock_window)
    mock_msgbox = MagicMock()

    import importlib
    import desktop.main as dm
    importlib.reload(dm)

    dm.QApplication = mock_app_cls
    dm.QMessageBox = mock_msgbox
    dm.validate_runtime_config = MagicMock()
    dm.MainWindow = mock_window_cls

    mock_sys = MagicMock()
    mock_sys.argv = []
    dm.sys = mock_sys

    dm.main()

    mock_window_cls.assert_called_once()
    mock_window.show.assert_called_once()
    mock_app.exec.assert_called_once()
    mock_msgbox.critical.assert_not_called()
