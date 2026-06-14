from datetime import date
from appcore.order_analytics import realtime_warmup as rw


def test_today_and_yesterday():
    t = date(2026, 6, 10)
    assert rw.resolve_meta_calendar_range("today", t) == (date(2026, 6, 10), date(2026, 6, 10))
    assert rw.resolve_meta_calendar_range("yesterday", t) == (date(2026, 6, 9), date(2026, 6, 9))


def test_this_week_monday_start_sunday_end():
    t = date(2026, 6, 10)  # 周三 → 周一 6-08，周日 6-14
    assert rw.resolve_meta_calendar_range("thisWeek", t) == (date(2026, 6, 8), date(2026, 6, 14))


def test_last_week():
    t = date(2026, 6, 10)
    assert rw.resolve_meta_calendar_range("lastWeek", t) == (date(2026, 6, 1), date(2026, 6, 7))


def test_monday_itself():
    t = date(2026, 6, 8)  # 周一
    assert rw.resolve_meta_calendar_range("thisWeek", t) == (date(2026, 6, 8), date(2026, 6, 14))


def test_warmup_targets_intervals():
    targets = {(t.range_name, t.scope): t.interval_seconds for t in rw.WARMUP_TARGETS}
    assert targets[("today", "global")] == 45
    assert targets[("today", "new")] == 150
    assert targets[("thisWeek", "global")] == 45
    assert targets[("yesterday", "global")] == 1200
    assert targets[("lastWeek", "unmatched")] == 1200
    assert all(t.range_name in {"today", "yesterday", "thisWeek", "lastWeek"} for t in rw.WARMUP_TARGETS)


def test_due_targets_respects_last_run():
    import appcore.order_analytics.realtime_warmup as m
    m._last_run.clear()
    now = 1000.0
    assert m._due_targets(now), "首次全部到期"
    m._last_run[("today", "global")] = now
    due2 = {(t.range_name, t.scope) for t in m._due_targets(now + 10)}
    assert ("today", "global") not in due2
    assert ("today", "new") in due2


def test_run_tick_serial_calls_warm_one(monkeypatch):
    import appcore.order_analytics.realtime_warmup as m
    m._last_run.clear()
    calls = []
    monkeypatch.setattr(m, "_warm_one", lambda t: calls.append((t.range_name, t.scope)))
    monkeypatch.setattr(m, "_now", lambda: 5000.0)
    m.run_warmup_tick()
    assert ("today", "global") in calls
    assert len(calls) == len(m.WARMUP_TARGETS)
