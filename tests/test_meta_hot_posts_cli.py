from tools.meta_hot_posts import main


def test_sync_cli_defaults_to_full_sync(monkeypatch, capsys):
    captured = {}

    def fake_sync_tick_once(**kwargs):
        captured.update(kwargs)
        return {"posts": 2307}

    monkeypatch.setattr(main.scheduler, "sync_tick_once", fake_sync_tick_once)
    monkeypatch.setattr("sys.argv", ["meta-hot-posts", "--mode", "sync"])

    assert main.main() == 0

    output = capsys.readouterr().out
    assert '"posts": 2307' in output
    assert captured == {"target_count": None, "max_pages": main.scheduler.FULL_SYNC_MAX_PAGES}
