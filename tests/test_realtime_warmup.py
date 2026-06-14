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


def test_this_month_and_last_month():
    t = date(2026, 6, 14)
    assert rw.resolve_meta_calendar_range("thisMonth", t) == (date(2026, 6, 1), date(2026, 6, 30))
    assert rw.resolve_meta_calendar_range("lastMonth", t) == (date(2026, 5, 1), date(2026, 5, 31))


def test_this_month_december_and_last_month_january():
    assert rw.resolve_meta_calendar_range("thisMonth", date(2026, 12, 10)) == (date(2026, 12, 1), date(2026, 12, 31))
    assert rw.resolve_meta_calendar_range("lastMonth", date(2026, 1, 15)) == (date(2025, 12, 1), date(2025, 12, 31))


def test_warmup_targets_matrix():
    by_key = {(t.range_name, t.module, t.scope): t.interval_seconds for t in rw.WARMUP_TARGETS}
    # 频率分档
    assert by_key[("today", "realtime", "global")] == 15
    assert by_key[("yesterday", "npl", "new")] == 15
    assert by_key[("thisWeek", "realtime", "global")] == 600
    assert by_key[("thisMonth", "npl", "old")] == 600
    assert by_key[("lastMonth", "realtime", "unmatched")] == 600
    # 范围：6 个，不含年度
    ranges = {t.range_name for t in rw.WARMUP_TARGETS}
    assert ranges == {"today", "yesterday", "thisWeek", "lastWeek", "thisMonth", "lastMonth"}
    assert "thisYear" not in ranges and "lastYear" not in ranges
    # 模块 scope：realtime 含 global，npl 不含
    rt = {t.scope for t in rw.WARMUP_TARGETS if t.module == "realtime"}
    npl = {t.scope for t in rw.WARMUP_TARGETS if t.module == "npl"}
    assert rt == {"global", "new", "old", "unmatched"}
    assert npl == {"new", "old", "unmatched"}
    # 总数 6 × (4 + 3) = 42
    assert len(rw.WARMUP_TARGETS) == 42


def test_due_targets_respects_last_run():
    import appcore.order_analytics.realtime_warmup as m
    m._last_run.clear()
    now = 1000.0
    assert m._due_targets(now)
    key = ("today", "realtime", "global")
    m._last_run[key] = now
    due2 = {(t.range_name, t.module, t.scope) for t in m._due_targets(now + 5)}
    assert key not in due2                                   # 15s 未到
    assert ("thisWeek", "realtime", "global") in due2        # 仍首次到期


def test_run_tick_serial_calls_warm_one(monkeypatch):
    import appcore.order_analytics.realtime_warmup as m
    m._last_run.clear()
    calls = []
    monkeypatch.setattr(m, "_warm_one", lambda t: calls.append((t.range_name, t.module, t.scope)))
    monkeypatch.setattr(m, "_now", lambda: 5000.0)
    m.run_warmup_tick()
    assert len(calls) == len(m.WARMUP_TARGETS)
    assert ("today", "realtime", "global") in calls
    assert ("today", "npl", "new") in calls


def test_npl_warm_params_have_details_and_paging(monkeypatch):
    import appcore.order_analytics.realtime_warmup as m
    captured = {}

    def fake_compute(date_text, kwargs, *, cache_params):
        captured["kwargs"] = kwargs
        captured["cache_params"] = cache_params

    monkeypatch.setattr(m, "current_meta_business_date", lambda: date(2026, 6, 13))
    monkeypatch.setattr("web.routes.order_analytics._compute_realtime_overview_cached", fake_compute)
    npl_target = next(t for t in m.WARMUP_TARGETS
                      if t.module == "npl" and t.scope == "new" and t.range_name == "today")
    m._warm_one(npl_target)
    assert captured["cache_params"]["include_details"] is True
    assert captured["cache_params"]["page_size"] == 30
    assert captured["cache_params"]["order_page"] == 1
    assert captured["cache_params"]["product_launch_scope"] == "new"


def test_realtime_warm_params_no_details(monkeypatch):
    import appcore.order_analytics.realtime_warmup as m
    captured = {}

    def fake_compute(date_text, kwargs, *, cache_params):
        captured["cache_params"] = cache_params

    monkeypatch.setattr(m, "current_meta_business_date", lambda: date(2026, 6, 13))
    monkeypatch.setattr("web.routes.order_analytics._compute_realtime_overview_cached", fake_compute)
    rt_target = next(t for t in m.WARMUP_TARGETS
                     if t.module == "realtime" and t.scope == "global" and t.range_name == "today")
    m._warm_one(rt_target)
    assert captured["cache_params"]["include_details"] is False
    assert captured["cache_params"]["page"] is None
    assert captured["cache_params"]["product_launch_scope"] is None


def test_task_registered_in_definitions():
    from appcore import scheduled_tasks
    assert "realtime_overview_warmup" in scheduled_tasks.TASK_DEFINITIONS
    t = scheduled_tasks.TASK_DEFINITIONS["realtime_overview_warmup"]
    assert t["source_type"] == "apscheduler"
    assert t["runner"].endswith("run_warmup_tick")
