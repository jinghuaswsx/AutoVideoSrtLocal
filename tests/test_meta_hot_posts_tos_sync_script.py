import importlib.util
import subprocess
import sys
from pathlib import Path


def test_meta_hot_posts_tos_sync_script_exposes_backfill_entrypoint():
    script = Path("tools/meta_hot_posts_tos_sync.py")

    assert script.exists()
    source = script.read_text(encoding="utf-8")
    assert "sync_localized_videos_to_tos" in source
    assert "--limit" in source
    assert "default=0" in source


def test_meta_hot_posts_tos_sync_script_runs_directly():
    script = Path("tools/meta_hot_posts_tos_sync.py")

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Backfill localized Meta hot-post videos to TOS" in result.stdout


def test_meta_hot_posts_tos_sync_script_syncs_runtime_credentials(monkeypatch):
    script = Path("tools/meta_hot_posts_tos_sync.py")
    spec = importlib.util.spec_from_file_location("meta_hot_posts_tos_sync_script", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    calls = []

    def fake_sync_to_runtime():
        calls.append("sync_to_runtime")

    def fake_sync_localized_videos_to_tos(*, limit):
        calls.append(("sync_localized_videos_to_tos", limit))
        return {"files_checked": 0, "actions": {}, "failed": 0, "errors": []}

    monkeypatch.setattr(module.infra_credentials, "sync_to_runtime", fake_sync_to_runtime)
    monkeypatch.setattr(module.tos_sync, "sync_localized_videos_to_tos", fake_sync_localized_videos_to_tos)
    monkeypatch.setattr(sys, "argv", [str(script), "--limit", "7"])

    assert module.main() == 0
    assert calls == ["sync_to_runtime", ("sync_localized_videos_to_tos", 7)]
