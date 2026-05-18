from __future__ import annotations


def test_voice_preview_rate_backfill_script_passes_cli_options(monkeypatch, tmp_path, capsys):
    from scripts import backfill_voice_preview_speech_rates as script

    calls = []

    monkeypatch.setattr(
        script,
        "backfill_missing_preview_speech_rates",
        lambda cache_dir, **kwargs: calls.append((cache_dir, kwargs)) or {
            "total": 2,
            "processed": 1,
            "updated": 1,
            "failed": 0,
            "skipped": 1,
        },
    )

    code = script.main([
        "--cache-dir",
        str(tmp_path),
        "--language",
        "en",
        "--limit",
        "2",
        "--workers",
        "3",
        "--dry-run",
    ])

    out = capsys.readouterr().out
    assert code == 0
    assert calls == [
        (
            str(tmp_path),
            {
                "language": "en",
                "limit": 2,
                "dry_run": True,
                "workers": 3,
            },
        )
    ]
    assert '"updated": 1' in out
