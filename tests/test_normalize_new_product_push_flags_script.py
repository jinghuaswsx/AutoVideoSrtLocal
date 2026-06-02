from __future__ import annotations


def test_normalize_new_product_push_flags_script_defaults_to_dry_run(monkeypatch, capsys):
    from scripts import normalize_new_product_push_flags as script

    calls = []

    def fake_normalize(*, dry_run=True):
        calls.append(dry_run)
        return {
            "dry_run": dry_run,
            "scanned_products": 1,
            "scanned_logs": 2,
            "update_count": 1,
            "set_true_count": 0,
            "clear_count": 1,
            "changes": [{"log_id": 11}],
        }

    monkeypatch.setattr(script, "normalize_new_product_push_flags", fake_normalize)

    assert script.main([]) == 0

    assert calls == [True]
    assert '"dry_run": true' in capsys.readouterr().out


def test_normalize_new_product_push_flags_script_apply_writes(monkeypatch, capsys):
    from scripts import normalize_new_product_push_flags as script

    calls = []

    def fake_normalize(*, dry_run=True):
        calls.append(dry_run)
        return {
            "dry_run": dry_run,
            "scanned_products": 1,
            "scanned_logs": 2,
            "update_count": 1,
            "set_true_count": 1,
            "clear_count": 0,
            "changes": [{"log_id": 10}],
        }

    monkeypatch.setattr(script, "normalize_new_product_push_flags", fake_normalize)

    assert script.main(["--apply", "--show-changes", "1"]) == 0

    assert calls == [False]
    assert '"dry_run": false' in capsys.readouterr().out
