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
