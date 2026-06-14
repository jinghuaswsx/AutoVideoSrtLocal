from datetime import date
from appcore.order_analytics import realtime_warmup as rw


def test_today_and_yesterday():
    t = date(2026, 6, 10)
    assert rw.resolve_meta_calendar_range("today", t) == (date(2026, 6, 10), date(2026, 6, 10))
    assert rw.resolve_meta_calendar_range("yesterday", t) == (date(2026, 6, 9), date(2026, 6, 9))


def test_this_week_monday_start_sunday_end():
    t = date(2026, 6, 10)
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


def test_fast_targets_are_realtime_today_yesterday():
    assert len(rw.FAST_TARGETS) == 8  # today/yesterday × 4 realtime scope
    assert all(t.module == "realtime" and t.range_name in ("today", "yesterday") for t in rw.FAST_TARGETS)
    assert all(t.interval_seconds == 15 for t in rw.FAST_TARGETS)


def test_slow_targets_exclude_fast_cover_rest():
    fast_keys = {(t.range_name, t.module, t.scope) for t in rw.FAST_TARGETS}
    assert all((t.range_name, t.module, t.scope) not in fast_keys for t in rw.SLOW_TARGETS)
    # 慢线含 npl 今昨 + 周月
    assert any(t.module == "npl" and t.range_name == "today" for t in rw.SLOW_TARGETS)
    assert any(t.range_name == "thisWeek" and t.module == "realtime" for t in rw.SLOW_TARGETS)
    assert len(rw.FAST_TARGETS) + len(rw.SLOW_TARGETS) == len(rw.WARMUP_TARGETS) == 48
    assert any(t.module == "subtab" for t in rw.SLOW_TARGETS)


def test_no_year_ranges():
    ranges = {t.range_name for t in rw.WARMUP_TARGETS}
    assert ranges == {"today", "yesterday", "thisWeek", "lastWeek", "thisMonth", "lastMonth"}


def test_due_respects_last_run():
    import appcore.order_analytics.realtime_warmup as m
    m._last_run.clear()
    now = 1000.0
    assert m._due(m.FAST_TARGETS, now)
    t0 = m.FAST_TARGETS[0]
    m._last_run[(t0.range_name, t0.module, t0.scope)] = now
    due2 = {(t.range_name, t.module, t.scope) for t in m._due(m.FAST_TARGETS, now + 5)}
    assert (t0.range_name, t0.module, t0.scope) not in due2


def test_run_fast_only_realtime_today_yesterday(monkeypatch):
    import appcore.order_analytics.realtime_warmup as m
    m._last_run.clear()
    calls = []
    monkeypatch.setattr(m, "_warm_one", lambda t: calls.append((t.range_name, t.module, t.scope)))
    monkeypatch.setattr(m, "_now", lambda: 5000.0)
    m.run_warmup_fast()
    assert len(calls) == len(m.FAST_TARGETS)
    assert all(mod == "realtime" and rng in ("today", "yesterday") for rng, mod, _ in calls)


def test_run_slow_covers_rest(monkeypatch):
    import appcore.order_analytics.realtime_warmup as m
    m._last_run.clear()
    calls = []
    monkeypatch.setattr(m, "_warm_one", lambda t: calls.append((t.range_name, t.module, t.scope)))
    monkeypatch.setattr(m, "_now", lambda: 5000.0)
    m.run_warmup_slow()
    assert len(calls) == len(m.SLOW_TARGETS)


def test_warm_one_recomputes_when_stale(monkeypatch):
    import appcore.order_analytics.realtime_warmup as m
    from appcore.order_analytics import realtime_cache
    compute_calls = []

    def fake_compute(date_text, kwargs, *, cache_params, force_refresh=False):
        compute_calls.append(force_refresh)

    monkeypatch.setattr(m, "current_meta_business_date", lambda: date(2026, 6, 13))
    monkeypatch.setattr("web.routes.order_analytics._compute_realtime_overview_cached", fake_compute)
    monkeypatch.setattr(realtime_cache, "touch", lambda key, ttl: True)
    monkeypatch.setattr(m, "_now", lambda: 10000.0)
    m._last_refresh.clear()
    target = next(t for t in m.WARMUP_TARGETS
                  if t.module == "realtime" and t.scope == "global" and t.range_name == "today")
    m._last_refresh[(target.range_name, target.module, target.scope)] = 0.0  # 很久没重算 → 陈旧
    m._warm_one(target)
    assert compute_calls == [True]  # 陈旧 → 强制重算更新数据


def test_warm_one_touches_when_fresh(monkeypatch):
    import appcore.order_analytics.realtime_warmup as m
    from appcore.order_analytics import realtime_cache
    compute_calls = []
    touch_calls = []

    def fake_compute(date_text, kwargs, *, cache_params, force_refresh=False):
        compute_calls.append(force_refresh)

    monkeypatch.setattr(m, "current_meta_business_date", lambda: date(2026, 6, 13))
    monkeypatch.setattr("web.routes.order_analytics._compute_realtime_overview_cached", fake_compute)
    monkeypatch.setattr(realtime_cache, "touch", lambda key, ttl: touch_calls.append(key) or True)
    monkeypatch.setattr(m, "_now", lambda: 10000.0)
    m._last_refresh.clear()
    target = next(t for t in m.WARMUP_TARGETS
                  if t.module == "realtime" and t.scope == "global" and t.range_name == "today")
    m._last_refresh[(target.range_name, target.module, target.scope)] = 9990.0  # 10s 前刚重算，TTL 60s → 未陈旧
    m._warm_one(target)
    assert touch_calls and not compute_calls  # 只 touch 续期，不重算、不占资源


def test_npl_warm_params_have_details_and_paging(monkeypatch):
    import appcore.order_analytics.realtime_warmup as m
    captured = {}

    def fake_compute(date_text, kwargs, *, cache_params, force_refresh=False):
        captured["cache_params"] = cache_params

    monkeypatch.setattr(m, "current_meta_business_date", lambda: date(2026, 6, 13))
    monkeypatch.setattr("web.routes.order_analytics._compute_realtime_overview_cached", fake_compute)
    npl_target = next(t for t in m.WARMUP_TARGETS
                      if t.module == "npl" and t.scope == "new" and t.range_name == "today")
    m._warm_one(npl_target)
    assert captured["cache_params"]["include_details"] is True
    assert captured["cache_params"]["page_size"] == 30
    assert captured["cache_params"]["product_launch_scope"] == "new"


def test_realtime_warm_params_no_details(monkeypatch):
    import appcore.order_analytics.realtime_warmup as m
    captured = {}

    def fake_compute(date_text, kwargs, *, cache_params, force_refresh=False):
        captured["cache_params"] = cache_params

    monkeypatch.setattr(m, "current_meta_business_date", lambda: date(2026, 6, 13))
    monkeypatch.setattr("web.routes.order_analytics._compute_realtime_overview_cached", fake_compute)
    rt_target = next(t for t in m.WARMUP_TARGETS
                     if t.module == "realtime" and t.scope == "global" and t.range_name == "today")
    m._warm_one(rt_target)
    assert captured["cache_params"]["include_details"] is False
    assert captured["cache_params"]["product_launch_scope"] is None


def test_tasks_registered():
    from appcore import scheduled_tasks
    assert "realtime_overview_warmup_fast" in scheduled_tasks.TASK_DEFINITIONS
    assert "realtime_overview_warmup_slow" in scheduled_tasks.TASK_DEFINITIONS
    assert scheduled_tasks.TASK_DEFINITIONS["realtime_overview_warmup_fast"]["runner"].endswith("run_warmup_fast")
    assert scheduled_tasks.TASK_DEFINITIONS["realtime_overview_warmup_slow"]["runner"].endswith("run_warmup_slow")
