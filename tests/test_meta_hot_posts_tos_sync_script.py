from pathlib import Path


def test_meta_hot_posts_tos_sync_script_exposes_backfill_entrypoint():
    script = Path("tools/meta_hot_posts_tos_sync.py")

    assert script.exists()
    source = script.read_text(encoding="utf-8")
    assert "sync_localized_videos_to_tos" in source
    assert "--limit" in source
    assert "default=0" in source
