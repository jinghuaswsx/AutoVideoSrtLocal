"""Tests for CapcutExportWidget."""
import os
import pytest
from unittest.mock import MagicMock, patch
from PySide6.QtWidgets import QApplication
import sys


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    return app


def test_buttons_disabled_on_init(qapp):
    from desktop.widgets.capcut_export import CapcutExportWidget
    w = CapcutExportWidget()
    assert not w._normal_btn.isEnabled()
    assert not w._hook_btn.isEnabled()


def test_enable_deploy_enables_both_buttons(qapp):
    from desktop.widgets.capcut_export import CapcutExportWidget
    w = CapcutExportWidget()
    w.enable_deploy()
    assert w._normal_btn.isEnabled()
    assert w._hook_btn.isEnabled()


def test_deploy_normal_calls_deploy_capcut_project(qapp):
    from desktop.widgets.capcut_export import CapcutExportWidget
    w = CapcutExportWidget()
    w.set_task_dir("/fake/task")
    w.enable_deploy()

    with patch("desktop.widgets.capcut_export.deploy_capcut_project", return_value="/jianying/draft") as mock_deploy:
        w._deploy_normal()

    mock_deploy.assert_called_once_with(os.path.join("/fake/task", "capcut", "normal"))
    assert "/jianying/draft" in w._normal_status.text()


def test_deploy_hook_cta_calls_deploy_capcut_project(qapp):
    from desktop.widgets.capcut_export import CapcutExportWidget
    w = CapcutExportWidget()
    w.set_task_dir("/fake/task")
    w.enable_deploy()

    with patch("desktop.widgets.capcut_export.deploy_capcut_project", return_value="/jianying/hook") as mock_deploy:
        w._deploy_hook_cta()

    mock_deploy.assert_called_once_with(os.path.join("/fake/task", "capcut", "hook_cta"))
    assert "/jianying/hook" in w._hook_status.text()


def test_deploy_failure_shows_error_in_status(qapp):
    from desktop.widgets.capcut_export import CapcutExportWidget
    w = CapcutExportWidget()
    w.set_task_dir("/fake/task")
    w.enable_deploy()

    with patch("desktop.widgets.capcut_export.deploy_capcut_project", side_effect=RuntimeError("no path")), \
         patch("desktop.widgets.capcut_export.QMessageBox"):
        w._deploy_normal()

    assert "失败" in w._normal_status.text()
