import json


def test_meta_hot_posts_local_video_metadata_backfill_script_runs_service(monkeypatch, capsys):
    from tools import meta_hot_posts_local_video_metadata_backfill as script

    events = []
    monkeypatch.setattr(script.infra_credentials, "sync_to_runtime", lambda: events.append("sync_credentials"))
    monkeypatch.setattr(
        script.video_localization,
        "backfill_local_video_metadata",
        lambda limit=None: {"scanned": 2, "updated": 2, "missing": 0, "failed": 0, "limit": limit},
    )

    code = script.main(["--limit", "25"])

    assert code == 0
    assert events == ["sync_credentials"]
    payload = json.loads(capsys.readouterr().out)
    assert payload["updated"] == 2
    assert payload["limit"] == 25


def test_meta_hot_posts_local_video_metadata_backfill_script_returns_failure_on_errors(monkeypatch):
    from tools import meta_hot_posts_local_video_metadata_backfill as script

    monkeypatch.setattr(script.infra_credentials, "sync_to_runtime", lambda: None)
    monkeypatch.setattr(
        script.video_localization,
        "backfill_local_video_metadata",
        lambda limit=None: {"scanned": 1, "updated": 0, "missing": 0, "failed": 1},
    )

    assert script.main([]) == 1
