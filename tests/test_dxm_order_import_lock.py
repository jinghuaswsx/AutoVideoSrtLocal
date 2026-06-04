import pytest


def test_default_lock_path_uses_output_locally(monkeypatch, tmp_path):
    from appcore import dxm_order_import_lock as lock

    monkeypatch.delenv("DXM_ORDER_IMPORT_LOCK_PATH", raising=False)
    monkeypatch.setattr(lock.Path, "cwd", lambda: tmp_path)
    monkeypatch.setattr(lock, "DEFAULT_LINUX_LOCK_PATH", tmp_path / "linux" / "automation.lock")
    monkeypatch.setattr(lock.os.path, "exists", lambda path: False)

    assert lock.default_dxm_order_import_lock_path() == tmp_path / "output" / "browser_automation" / "dxm_order_import.lock"


def test_env_lock_path_wins(monkeypatch, tmp_path):
    from appcore import dxm_order_import_lock as lock

    custom = tmp_path / "custom.lock"
    monkeypatch.setenv("DXM_ORDER_IMPORT_LOCK_PATH", str(custom))

    assert lock.default_dxm_order_import_lock_path() == custom


def test_timeout_summary_reads_holder_json(tmp_path):
    from appcore import dxm_order_import_lock as lock

    path = tmp_path / "automation.lock"
    path.write_text('{"pid":123,"command":"python tools/roi_hourly_sync.py"}\n', encoding="utf-8")

    summary = lock.lock_timeout_summary(path, timeout_seconds=60, error_message="busy")

    assert summary["lock_path"] == str(path)
    assert summary["timeout_seconds"] == 60
    assert summary["holder_pid"] == 123
    assert summary["holder_command"] == "python tools/roi_hourly_sync.py"
    assert summary["error"] == "busy"
