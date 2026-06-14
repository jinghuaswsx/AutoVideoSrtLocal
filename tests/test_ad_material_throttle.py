from appcore import ad_material_throttle as thr


def test_config_from_env_defaults(monkeypatch):
    for name in (
        "AD_MATERIAL_AI_ANALYSIS_PRODUCT_SPACING_SECONDS",
        "AD_MATERIAL_AI_ANALYSIS_LLM_MAX_RETRIES",
        "AD_MATERIAL_AI_ANALYSIS_LLM_BACKOFF_BASE_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = thr.ThrottleConfig.from_env()
    assert cfg.product_spacing == 10.0
    assert cfg.max_retries == 4
    assert cfg.backoff_base == 10.0


def test_config_from_env_override(monkeypatch):
    monkeypatch.setenv("AD_MATERIAL_AI_ANALYSIS_PRODUCT_SPACING_SECONDS", "15")
    monkeypatch.setenv("AD_MATERIAL_AI_ANALYSIS_LLM_MAX_RETRIES", "2")
    cfg = thr.ThrottleConfig.from_env()
    assert cfg.product_spacing == 15.0
    assert cfg.max_retries == 2


def test_is_rate_limit_error_status_code():
    class E(Exception):
        code = 429
    assert thr.is_rate_limit_error(E("boom")) is True


def test_is_rate_limit_error_keyword():
    assert thr.is_rate_limit_error(RuntimeError("Vertex Gemini call failed: RESOURCE_EXHAUSTED")) is True
    assert thr.is_rate_limit_error(RuntimeError("429 Too Many Requests")) is True


def test_is_rate_limit_error_cause_chain():
    inner = Exception("rate limit exceeded")
    outer = RuntimeError("wrapper")
    outer.__cause__ = inner
    assert thr.is_rate_limit_error(outer) is True


def test_is_rate_limit_error_non_retryable():
    assert thr.is_rate_limit_error(ValueError("response_schema invalid field")) is False


class FakeClock:
    """fake sleep 推进 fake monotonic，使节流时间逻辑自洽且不真睡。"""
    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, secs):
        if secs and secs > 0:
            self.sleeps.append(secs)
            self.now += secs

    def monotonic(self):
        return self.now


def _make(provider="google_wj", clock=None, **cfg_over):
    clock = clock or FakeClock()
    cfg = thr.ThrottleConfig(**{**dict(
        product_spacing=10.0, call_spacing=2.0, max_retries=4,
        backoff_base=10.0, backoff_max=120.0, factor=2.0,
        adaptive_max=60.0, recover_successes=3,
    ), **cfg_over})
    t = thr.GoogleWjThrottle(provider_code=provider, config=cfg,
                             sleep=clock.sleep, monotonic=clock.monotonic)
    return t, clock


def test_disabled_for_non_google_wj():
    t, clock = _make(provider="openrouter")
    assert t.enabled is False
    out = [t.guarded_invoke(lambda: {"ok": i}, stage="s") for i in range(3)]
    assert out[-1] == {"ok": 2}
    # 退化：调用级间隔(2s)，不是产品级 10s
    assert all(s == 2.0 for s in clock.sleeps)


def test_retries_on_rate_limit_then_succeeds():
    t, clock = _make()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return {"ok": True}

    assert t.guarded_invoke(fn, stage="material_review") == {"ok": True}
    assert calls["n"] == 3
    # 两次退避：10, 20
    assert 10.0 in clock.sleeps and 20.0 in clock.sleeps
    assert t.rate_limit_hits == 2


def test_retry_exhausted_raises_and_marks_degraded():
    t, clock = _make(max_retries=2)

    def fn():
        raise RuntimeError("429 quota")

    try:
        t.guarded_invoke(fn, stage="country_review", product_id=7)
        assert False, "should raise"
    except RuntimeError:
        pass
    assert t.degraded == 1
    assert t.degraded_events and t.degraded_events[0]["product_id"] == 7


def test_non_rate_limit_not_retried():
    t, clock = _make()
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("response_schema invalid")

    try:
        t.guarded_invoke(fn, stage="s")
        assert False
    except ValueError:
        pass
    assert calls["n"] == 1
    assert t.degraded == 0


def test_adaptive_interval_grows_and_caps():
    t, clock = _make(adaptive_max=40.0, max_retries=10)

    def fn():
        raise RuntimeError("rate limit")

    try:
        t.guarded_invoke(fn, stage="s")
    except RuntimeError:
        pass
    # 10 -> 20 -> 40 -> capped 40
    assert t.snapshot()["current_interval"] == 40.0


def test_adaptive_recovers_after_successes():
    t, clock = _make(recover_successes=2)
    # 先抬高一次
    try:
        t.guarded_invoke(lambda: (_ for _ in ()).throw(RuntimeError("429")), stage="s")
    except RuntimeError:
        pass
    high = t.snapshot()["current_interval"]
    assert high > 10.0
    t.guarded_invoke(lambda: {"ok": 1}, stage="s")
    t.guarded_invoke(lambda: {"ok": 1}, stage="s")
    assert t.snapshot()["current_interval"] < high


def test_mark_product_boundary_enforces_base_interval():
    t, clock = _make()
    t.guarded_invoke(lambda: {"ok": 1}, stage="ranking")  # 建立 last_call_at
    clock.sleeps.clear()
    t.mark_product_boundary()
    t.guarded_invoke(lambda: {"ok": 1}, stage="material_review")
    assert clock.sleeps and clock.sleeps[-1] >= 10.0


def test_snapshot_fields():
    t, _ = _make()
    snap = t.snapshot()
    for key in ("enabled", "base_interval", "current_interval", "retrying",
                "current_retry", "max_retries", "rate_limit_hits", "degraded",
                "last_event", "updated_at"):
        assert key in snap
