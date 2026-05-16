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
