# 投放素材AI分析 产品池40 + google_wj 自适应节流/重试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「投放素材AI分析」产品池从 20 放大到 40（AI 选品覆盖全 40），并为 google_wj 通道加自适应节流（产品间 ~10s 基线）+ 任务级长退避重试 + 进度页可视化，保证整轮评估在限流下也能跑完出结果。

**Architecture:** 新增独立模块 `appcore/ad_material_throttle.py`（`GoogleWjThrottle`：自适应间隔状态机 + 限流识别 + 长退避重试 + 可观测 snapshot），与 adapter 层既有的 3 次瞬时重试分层互补。`appcore/ad_material_ai_analysis.py` 的 4 个 LLM 调用点改走 `throttle.guarded_invoke(...)`，逐产品主循环按产品边界节流，进度 JSON 增 `throttle` 块，前端 run 卡片新增渲染。仅 `provider_code == "google_wj"` 启用强节流，其它通道退化。

**Tech Stack:** Python 3 / pytest / Flask（既有）/ 原生 JS 前端 / google-genai（adapter，不改）。

设计依据：`docs/superpowers/specs/2026-06-14-ad-material-ai-analysis-scale-40-throttle-design.md`

---

## File Structure

- Create: `appcore/ad_material_throttle.py` — google_wj 节流/重试/可观测，单一职责，纯逻辑可单测
- Create: `tests/test_ad_material_throttle.py` — 节流模块单测（注入 fake sleep/clock，不真睡）
- Modify: `appcore/ad_material_ai_analysis.py` — 常量 40/80、ranking 文案与输出量、4 调用点接入、产品边界、progress.throttle、summary 降级清单
- Modify: `web/static/ad_material_ai_analysis.js` — 新增 `renderThrottle`，run 卡片渲染 throttle 状态
- Create: `tests/test_ad_material_ai_analysis_scale.py` — 产品池=40、扩候选 ranking 产出 40 的聚焦测试

---

## Task 1: 节流配置与限流信号识别

**Files:**
- Create: `appcore/ad_material_throttle.py`
- Test: `tests/test_ad_material_throttle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ad_material_throttle.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ad_material_throttle.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'appcore.ad_material_throttle'`

- [ ] **Step 3: Write minimal implementation**

```python
# appcore/ad_material_throttle.py
"""投放素材AI分析专用：google_wj 通道自适应节流 + 任务级重试 + 可观测。

与 adapter 层（gemini_vertex_adapter 的 3 次瞬时重试，退避 1s/2s）分层互补：
adapter 扛秒级抖动；本模块管「调用间主动节流」「通道级持续限流的长退避重试」「可观测」。
仅对 provider_code == "google_wj" 启用强节流；其它 provider 退化为只走调用级最小间隔。
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

log = logging.getLogger(__name__)

GOOGLE_WJ_PROVIDER = "google_wj"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RATE_LIMIT_KEYWORDS = (
    "429", "resource_exhausted", "rate limit", "rate_limit", "quota",
    "too many requests", "unavailable", "deadline", "timeout",
    "vertex gemini call failed",
)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    try:
        return float(raw) if raw.strip() else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw) if raw.strip() else default
    except (TypeError, ValueError):
        return default


@dataclass
class ThrottleConfig:
    product_spacing: float = 10.0     # 产品级基线间隔（秒）
    call_spacing: float = 2.0         # 调用级最小间隔（秒）
    max_retries: int = 4              # 上层任务级重试次数（adapter 3 次之外）
    backoff_base: float = 10.0        # 任务级退避基数（秒）
    backoff_max: float = 120.0        # 退避封顶（秒）
    factor: float = 2.0               # 自适应放大/回落系数
    adaptive_max: float = 60.0        # 自适应间隔封顶（秒）
    recover_successes: int = 3        # 连续成功几次回落一档

    @classmethod
    def from_env(cls) -> "ThrottleConfig":
        return cls(
            product_spacing=_env_float("AD_MATERIAL_AI_ANALYSIS_PRODUCT_SPACING_SECONDS", 10.0),
            call_spacing=_env_float("AD_MATERIAL_AI_ANALYSIS_LLM_SPACING_SECONDS", 2.0),
            max_retries=_env_int("AD_MATERIAL_AI_ANALYSIS_LLM_MAX_RETRIES", 4),
            backoff_base=_env_float("AD_MATERIAL_AI_ANALYSIS_LLM_BACKOFF_BASE_SECONDS", 10.0),
            backoff_max=_env_float("AD_MATERIAL_AI_ANALYSIS_LLM_BACKOFF_MAX_SECONDS", 120.0),
            factor=_env_float("AD_MATERIAL_AI_ANALYSIS_THROTTLE_FACTOR", 2.0),
            adaptive_max=_env_float("AD_MATERIAL_AI_ANALYSIS_THROTTLE_MAX_SECONDS", 60.0),
            recover_successes=_env_int("AD_MATERIAL_AI_ANALYSIS_THROTTLE_RECOVER_SUCCESSES", 3),
        )


def is_rate_limit_error(exc: BaseException) -> bool:
    """遍历异常链，识别限流/可重试错误（adapter 已把原始 429 包装成 RuntimeError）。"""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        code = getattr(cur, "code", None) or getattr(cur, "status_code", None)
        if isinstance(code, int) and code in _RETRYABLE_STATUS:
            return True
        text = str(cur).lower()
        if any(kw in text for kw in _RATE_LIMIT_KEYWORDS):
            return True
        cur = cur.__cause__ or cur.__context__
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ad_material_throttle.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add appcore/ad_material_throttle.py tests/test_ad_material_throttle.py
git commit -m "feat(ad-material): throttle config + rate-limit error detection"
```

---

## Task 2: GoogleWjThrottle 节流/重试/自适应/可观测

**Files:**
- Modify: `appcore/ad_material_throttle.py`
- Test: `tests/test_ad_material_throttle.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ad_material_throttle.py  (append)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ad_material_throttle.py -q`
Expected: FAIL — `AttributeError: module 'appcore.ad_material_throttle' has no attribute 'GoogleWjThrottle'`

- [ ] **Step 3: Write minimal implementation (append the class)**

```python
# appcore/ad_material_throttle.py  (append)
class GoogleWjThrottle:
    def __init__(
        self,
        *,
        provider_code: str,
        on_event: Callable[[dict], None] | None = None,
        config: ThrottleConfig | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = (provider_code or "").strip().lower() == GOOGLE_WJ_PROVIDER
        self.config = config or ThrottleConfig.from_env()
        self._on_event = on_event
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call_at: float | None = None
        self._current_interval = self.config.product_spacing if self.enabled else self.config.call_spacing
        self._pending_min_interval = 0.0
        self._consecutive_success = 0
        self.rate_limit_hits = 0
        self.degraded = 0
        self.degraded_events: list[dict] = []
        self._retrying = False
        self._current_retry = 0
        self._last_event = ""

    def _emit(self, kind: str, message: str, *, level: str = "info") -> None:
        self._last_event = message
        if self._on_event is None:
            return
        try:
            self._on_event({"kind": kind, "message": message, "level": level, "throttle": self.snapshot()})
        except Exception:
            log.debug("throttle on_event callback failed", exc_info=True)

    def _wait_before_call(self) -> None:
        if self._last_call_at is None:
            wait = self._pending_min_interval
        else:
            elapsed = self._monotonic() - self._last_call_at
            base = self._current_interval if self.enabled else self.config.call_spacing
            wait = max(base, self._pending_min_interval) - elapsed
        if wait and wait > 0:
            self._sleep(wait)
        self._pending_min_interval = 0.0

    def _on_success(self) -> None:
        if not self.enabled:
            return
        self._consecutive_success += 1
        if (self._consecutive_success >= self.config.recover_successes
                and self._current_interval > self.config.product_spacing):
            self._current_interval = max(self._current_interval / self.config.factor, self.config.product_spacing)
            self._consecutive_success = 0
            self._emit("recover", f"通道平稳，节流间隔回落到 {self._current_interval:.0f}s")

    def _on_rate_limit(self) -> None:
        self.rate_limit_hits += 1
        self._consecutive_success = 0
        if self.enabled:
            self._current_interval = min(self._current_interval * self.config.factor, self.config.adaptive_max)

    def mark_product_boundary(self) -> None:
        """进入新产品前调用：下一次调用前至少等待一个产品级基线间隔。"""
        if self.enabled:
            self._pending_min_interval = max(self._pending_min_interval, self.config.product_spacing)

    def guarded_invoke(self, fn: Callable[[], dict], *, stage: str, product_id: Any = None) -> dict:
        attempt = 0
        while True:
            self._wait_before_call()
            try:
                result = fn()
            except Exception as exc:
                self._last_call_at = self._monotonic()
                if is_rate_limit_error(exc) and attempt < self.config.max_retries:
                    self._on_rate_limit()
                    backoff = min(self.config.backoff_base * (2 ** attempt), self.config.backoff_max)
                    attempt += 1
                    self._retrying = True
                    self._current_retry = attempt
                    self._emit("retry",
                               f"{stage} 命中限流，{backoff:.0f}s 后第 {attempt}/{self.config.max_retries} 次重试",
                               level="warning")
                    self._sleep(backoff)
                    continue
                self._retrying = False
                if is_rate_limit_error(exc):
                    self.degraded += 1
                    self.degraded_events.append({
                        "stage": stage,
                        "product_id": product_id,
                        "at": datetime.now().isoformat(sep=" ", timespec="seconds"),
                    })
                    self._emit("degraded",
                               f"{stage} 限流重试耗尽（{self.config.max_retries} 次），该环节降级兜底",
                               level="warning")
                raise
            else:
                self._last_call_at = self._monotonic()
                self._retrying = False
                self._current_retry = 0
                self._on_success()
                return result

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "base_interval": self.config.product_spacing,
            "current_interval": round(self._current_interval, 2),
            "retrying": self._retrying,
            "current_retry": self._current_retry,
            "max_retries": self.config.max_retries,
            "rate_limit_hits": self.rate_limit_hits,
            "degraded": self.degraded,
            "last_event": self._last_event,
            "updated_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ad_material_throttle.py -q`
Expected: PASS (14 passed)

- [ ] **Step 5: Commit**

```bash
git add appcore/ad_material_throttle.py tests/test_ad_material_throttle.py
git commit -m "feat(ad-material): GoogleWjThrottle adaptive pacing + backoff retry"
```

---

## Task 3: 产品池 40 + 扩候选 + ranking 文案/输出量

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py:33-34`（常量）、`:66`（步骤文案）、`:1869`、`:1904`、`:3377`、`:3380`、`:3496`（文案）
- Test: `tests/test_ad_material_ai_analysis_scale.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ad_material_ai_analysis_scale.py
from appcore import ad_material_ai_analysis as svc


def test_project_top_n_is_40():
    assert svc._PROJECT_TOP_N == 40


def test_max_ai_candidates_is_80():
    assert svc._MAX_AI_CANDIDATES == 80


def test_ranking_prompts_target_40_not_20():
    # 文案不能再出现 Top20/Top10，避免 prompt 误导模型只吐 20 个
    src = svc._ranking_prompt  # 函数存在
    import inspect
    text = inspect.getsource(svc._run_ai_ranking)
    assert "Top14" in text or "Top 14" in text
    assert "Top40" in text or "Top 40" in text
    assert "Top10" not in text and "Top20" not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_ad_material_ai_analysis_scale.py -q`
Expected: FAIL — `assert 20 == 40`

- [ ] **Step 3: Apply edits**

In `appcore/ad_material_ai_analysis.py`:

`:33-34`
```python
_MAX_AI_CANDIDATES = 80
_PROJECT_TOP_N = 40
```

`:66` (PROGRESS_STEPS ai_ranking 步骤)
```python
    {"key": "ai_ranking", "label": "Top 40 AI 复评", "description": "分批调用 GoogleWJ Gemini 复评候选产品。"},
```

`:1869`
```python
                "rule": "本批最多输出 Top14，剔除高ROAS低量产品。",
```

`:1904`
```python
            "rule": "从所有批次候选里输出最终 Top40，仍然坚持有量 + 效率。",
```

`:3377`
```python
                "复用已保存 Top 40 排名结果，不重复调用排名模型。",
```

`:3380`
```python
            checkpoint("ai_ranking", "running", 32, "调用 GoogleWJ Gemini 分批复评 Top 40。")
```

`:3496`
```python
        checkpoint("persist", "running", 88, "整理已落库结果，清理不在本轮 Top 40 内的旧结果。")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ad_material_ai_analysis_scale.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add appcore/ad_material_ai_analysis.py tests/test_ad_material_ai_analysis_scale.py
git commit -m "feat(ad-material): scale product pool to 40 + widen ranking candidates"
```

---

## Task 4: 扩候选后 ranking 实际产出 40 的回归测试

**Files:**
- Test: `tests/test_ad_material_ai_analysis_scale.py`（append）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ad_material_ai_analysis_scale.py  (append)
def _synthetic_candidates(n):
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "product_id": i, "product_code": f"P{i}", "product_name": f"name{i}",
            "spend_30d": 1000 - i, "orders_30d": 100 - i, "spend_7d": 200,
            "spend_yesterday": 30, "results_30d": 50, "ad_count_30d": 5,
            "true_roas_30d": 2.0, "meta_roas_30d": 2.0, "profit_30d": 100,
            "score": float(1000 - i), "selection_reasons": ["有量"],
            "local_material_count": 1, "local_material_langs": {}, "delivery_status": "active",
            "effective_breakeven_roas": 1.2,
        })
    return rows


def test_ranking_selects_40_when_ai_covers(monkeypatch):
    from appcore import ad_material_ai_analysis as svc
    candidates = _synthetic_candidates(80)

    def fake_invoke(use_case, **kw):
        # 每批回 14 个、final 回 40 个：用 prompt 里的产品 id 还原
        import re
        ids = [int(x) for x in re.findall(r'"product_id":(\d+)', kw["prompt"])]
        stage = (kw.get("billing_extra") or {}).get("stage")
        take = 40 if stage == "final_rank" else 14
        ranked = [{"product_id": pid, "rank": idx + 1} for idx, pid in enumerate(ids[:take])]
        return {"json": {"ranked_products": ranked}, "text": "", "usage_log_id": None}

    monkeypatch.setattr(svc.llm_client, "invoke_generate", fake_invoke)
    ranking = svc._run_ai_ranking(candidates, project_id=1, user_id=None, run_ai=True)
    selected = svc._select_products(candidates, ranking)
    assert len(ranking["selected_product_ids"]) == 40
    assert len(selected) == 40
```

- [ ] **Step 2: Run test to verify it fails (or errors)**

Run: `python3 -m pytest tests/test_ad_material_ai_analysis_scale.py::test_ranking_selects_40_when_ai_covers -q`
Expected: FAIL — final returns only 20 ids until Task 3's prompt change propagates; with Task 3 applied, expected PASS. (If Task 3 already merged, this is a guard test.)

- [ ] **Step 3: No new impl (Task 3 already changed prompts)**

If failing, verify Task 3 edits at `:1869`/`:1904` are applied. No further code.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_ad_material_ai_analysis_scale.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_ad_material_ai_analysis_scale.py
git commit -m "test(ad-material): ranking selects 40 after candidate widening"
```

---

## Task 5: 接入 throttle —— 函数签名传参

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py`（`_run_ai_ranking` :1860、`_run_product_analysis` :2421、`_run_country_reviews` :4630 三个 `def` 加 `throttle` 参数）

- [ ] **Step 1: Add `throttle` param to the three signatures**

`:1860`
```python
def _run_ai_ranking(candidates: list[dict], *, project_id: int, user_id: int | None, run_ai: bool, throttle=None) -> dict[str, Any]:
```

`:2421-2430` (`_run_product_analysis` signature) — add trailing param:
```python
def _run_product_analysis(
    product: dict,
    countries: list[dict],
    local_materials: list[dict],
    mk_materials: list[dict],
    *,
    project_id: int,
    user_id: int | None,
    run_ai: bool,
    throttle=None,
) -> dict:
```

`:4630-4641` (`_run_country_reviews` signature) — add trailing param:
```python
def _run_country_reviews(
    product: Mapping[str, Any],
    eval_countries: tuple[dict[str, str], ...],
    countries_by_code: Mapping[str, dict],
    *,
    local_materials: list[dict],
    mk_materials: list[dict],
    task_assignments: list[dict],
    project_id: int,
    user_id: int | None,
    run_ai: bool,
    throttle=None,
) -> dict[str, dict]:
```

- [ ] **Step 2: Add throttle import + `_invoke_via_throttle` helper (keep `_pace_llm` for now)**

(a) At the top import section (line ~22, next to `from appcore import db, llm_client`) add:
```python
from appcore.ad_material_throttle import GoogleWjThrottle
```
(b) Immediately AFTER the existing `_pace_llm` definition (`appcore/ad_material_ai_analysis.py:~471`) add — do NOT delete `_pace_llm` yet (the 4 call sites still use it until Task 6):
```python
def _invoke_via_throttle(throttle, *, stage: str, product_id=None, **invoke_kwargs):
    """统一经 throttle 调用 llm_client.invoke_generate；throttle 缺省时退化为直连。"""
    call = lambda: llm_client.invoke_generate(**invoke_kwargs)
    if throttle is None:
        return call()
    return throttle.guarded_invoke(call, stage=stage, product_id=product_id)
```
> Keeping `_pace_llm` until Task 6 means every commit stays runnable; Task 6 removes it once no call site references it.

- [ ] **Step 3: Run import smoke check**

Run: `python3 -c "import appcore.ad_material_ai_analysis"`
Expected: no error (module imports cleanly)

- [ ] **Step 4: Commit**

```bash
git add appcore/ad_material_ai_analysis.py
git commit -m "refactor(ad-material): thread throttle into LLM call sites (signatures)"
```

---

## Task 6: 接入 throttle —— 4 个调用点改写

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py`（:1872、:1907、:2446、:4663 四处）

- [ ] **Step 1: Rewrite ranking batch call (`:1872-1885`)**

Old:
```python
            _pace_llm()
            result = llm_client.invoke_generate(
                RANK_USE_CASE,
                prompt=_ranking_prompt(payload),
                user_id=user_id,
                project_id=str(project_id),
                response_schema=RANKING_RESPONSE_SCHEMA,
                temperature=0.15,
                max_output_tokens=4096,
                provider_override=PROVIDER_CODE,
                model_override=MODEL_ID,
                billing_extra={"stage": "batch_rank", "batch_index": batch_index},
                timeout_seconds=180,
            )
```
New (replace `_pace_llm()` + `llm_client.invoke_generate(...)` with `_invoke_via_throttle`):
```python
            result = _invoke_via_throttle(
                throttle, stage="batch_rank",
                use_case_code=RANK_USE_CASE,
                prompt=_ranking_prompt(payload),
                user_id=user_id,
                project_id=str(project_id),
                response_schema=RANKING_RESPONSE_SCHEMA,
                temperature=0.15,
                max_output_tokens=4096,
                provider_override=PROVIDER_CODE,
                model_override=MODEL_ID,
                billing_extra={"stage": "batch_rank", "batch_index": batch_index},
                timeout_seconds=180,
            )
```
> Note: `invoke_generate`'s first positional arg is `use_case_code`; passed as keyword here so it flows through `**invoke_kwargs`.

- [ ] **Step 2: Rewrite ranking final call (`:1907-1920`)** the same way

Old `_pace_llm()` + `final = llm_client.invoke_generate(RANK_USE_CASE, ...)` →
```python
        final = _invoke_via_throttle(
            throttle, stage="final_rank",
            use_case_code=RANK_USE_CASE,
            prompt=_ranking_prompt(final_payload),
            user_id=user_id,
            project_id=str(project_id),
            response_schema=RANKING_RESPONSE_SCHEMA,
            temperature=0.1,
            max_output_tokens=4096,
            provider_override=PROVIDER_CODE,
            model_override=MODEL_ID,
            billing_extra={"stage": "final_rank"},
            timeout_seconds=180,
        )
```

- [ ] **Step 3: Rewrite product analysis call (`:2446-2463`)**

Old `_pace_llm()` + `result = llm_client.invoke_generate(PRODUCT_ANALYSIS_USE_CASE, ...)` →
```python
        result = _invoke_via_throttle(
            throttle, stage="material_review", product_id=product.get("product_id"),
            use_case_code=PRODUCT_ANALYSIS_USE_CASE,
            prompt=_material_review_prompt(review_input),
            user_id=user_id,
            project_id=str(project_id),
            response_schema=MATERIAL_REVIEW_RESPONSE_SCHEMA,
            temperature=0.2,
            max_output_tokens=8192,
            provider_override=PROVIDER_CODE,
            model_override=MODEL_ID,
            billing_extra={
                "stage": "material_review",
                "product_id": product.get("product_id"),
                "prompt_version": PROMPT_VERSION,
            },
            timeout_seconds=180,
        )
```

- [ ] **Step 4: Rewrite country review call (`:4663-4680`)**

Old `_pace_llm()` + `result = llm_client.invoke_generate(COUNTRY_REVIEW_USE_CASE, ...)` →
```python
        result = _invoke_via_throttle(
            throttle, stage="country_review", product_id=product.get("product_id"),
            use_case_code=COUNTRY_REVIEW_USE_CASE,
            prompt=_combined_country_review_prompt(review_input),
            user_id=user_id,
            project_id=str(project_id),
            response_schema=COMBINED_COUNTRY_REVIEW_RESPONSE_SCHEMA,
            temperature=0.2,
            max_output_tokens=8192,
            provider_override=PROVIDER_CODE,
            model_override=MODEL_ID,
            billing_extra={
                "stage": "country_review_combined",
                "product_id": product.get("product_id"),
                "country_count": len(eval_countries),
            },
            timeout_seconds=180,
        )
```

- [ ] **Step 5: Remove now-unused `_pace_llm` and verify**

All 4 call sites no longer call `_pace_llm()`. Delete the now-dead definitions `_LAST_LLM_AT` (`:448`), `_llm_spacing_seconds` (`:451`) and `_pace_llm` (`:459`). The call-spacing logic now lives in `GoogleWjThrottle` (env `AD_MATERIAL_AI_ANALYSIS_LLM_SPACING_SECONDS` preserved there).

Run: `grep -n "_pace_llm\|_LAST_LLM_AT\|_llm_spacing_seconds" appcore/ad_material_ai_analysis.py`
Expected: no output (all removed)

Run: `python3 -c "import appcore.ad_material_ai_analysis"`
Expected: no error

- [ ] **Step 6: Commit**

```bash
git add appcore/ad_material_ai_analysis.py
git commit -m "feat(ad-material): route all google_wj calls through throttle"
```

---

## Task 7: 实例化 throttle + 产品边界 + progress.throttle + 降级清单

**Files:**
- Modify: `appcore/ad_material_ai_analysis.py`（`_run_project_locked` :3297；主循环 :3397；调用三函数处 :3381/:3441/:3465；summary :3502；新增模块级 `_apply_throttle_event`）

- [ ] **Step 1: Add module-level `_apply_throttle_event` (near `_progress_update`, after `:202`)**

```python
def _apply_throttle_event(progress: dict[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    """把 throttle 事件写入 progress：刷新 throttle 状态块，关键事件追加一条日志。"""
    progress["throttle"] = dict(event.get("throttle") or {})
    kind = str(event.get("kind") or "")
    if kind in {"retry", "degraded", "recover"}:
        logs = list(progress.get("logs") or [])
        logs.append({
            "time": _now_iso(),
            "level": str(event.get("level") or "info"),
            "message": str(event.get("message") or ""),
        })
        progress["logs"] = logs[-_PROGRESS_LOG_LIMIT:]
    progress["updated_at"] = _now_iso()
    return progress
```

- [ ] **Step 2: Instantiate throttle in `_run_project_locked` (after `checkpoint` def, before the `try:` at `:3333`)**

Insert:
```python
    def _throttle_event(event: dict) -> None:
        nonlocal progress
        progress = _apply_throttle_event(progress, event)
        _save_progress(project_id, progress)

    provider_code = str(project_row.get("provider_code") or PROVIDER_CODE)
    throttle = GoogleWjThrottle(provider_code=provider_code, on_event=_throttle_event)
```

- [ ] **Step 3: Pass `throttle` to the three calls**

`:3381`
```python
            ranking = _run_ai_ranking(candidates, project_id=project_id, user_id=user_id, run_ai=run_ai, throttle=throttle)
```
`:3441-3449` add `throttle=throttle` to `_run_product_analysis(...)`:
```python
            ai_result = _run_product_analysis(
                product, countries, local_materials, mk_materials,
                project_id=project_id, user_id=user_id, run_ai=run_ai, throttle=throttle,
            )
```
`:3465-3470` add `throttle=throttle` to `_run_country_reviews(...)`:
```python
            country_reviews = _run_country_reviews(
                product, TARGET_EVAL_COUNTRIES, countries_by_code,
                local_materials=local_materials, mk_materials=mk_materials,
                task_assignments=task_assignments,
                project_id=project_id, user_id=user_id, run_ai=run_ai, throttle=throttle,
            )
```

- [ ] **Step 4: Product boundary in the main loop (`:3397`, first line inside the `for`)**

After `for rank_no, product in enumerate(selected, start=1):` add as the first statement:
```python
            throttle.mark_product_boundary()
```

- [ ] **Step 5: Inject throttle snapshot + degraded list into summary (`:3502`)**

Old:
```python
        summary = _summarize_project(results, ranking, snapshot)
```
New:
```python
        summary = _summarize_project(results, ranking, snapshot)
        summary["throttle"] = throttle.snapshot()
        summary["degraded_list"] = throttle.degraded_events
        if throttle.degraded_events:
            checkpoint("summary", "running", 97,
                       f"完成，但 {len(throttle.degraded_events)} 个评估环节因限流降级兜底。",
                       level="warning")
```

- [ ] **Step 6: Smoke check**

Run: `python3 -c "import appcore.ad_material_ai_analysis"`
Expected: no error

- [ ] **Step 7: Commit**

```bash
git add appcore/ad_material_ai_analysis.py
git commit -m "feat(ad-material): wire throttle lifecycle, product pacing, progress + summary observability"
```

---

## Task 8: 前端 —— renderThrottle 渲染节流/重试状态

**Files:**
- Modify: `web/static/ad_material_ai_analysis.js`（`renderProgressLogs` 后新增 `renderThrottle`；run 卡片 `:1036-1037` 插入调用）
- Modify: run 卡片模板引用版本号（grep 确认 `ad_material_ai_analysis.js?v=` 所在模板）

- [ ] **Step 1: Add `renderThrottle` after `renderProgressLogs` (`:1070`)**

```javascript
  function renderThrottle(t) {
    if (!t || !t.enabled) return '';
    const retry = t.retrying
      ? `<span class="aims-throttle-retry">重试中 ${Number(t.current_retry || 0)}/${Number(t.max_retries || 0)}</span>`
      : '';
    const degraded = Number(t.degraded || 0)
      ? `<span class="aims-throttle-degraded">降级 ${Number(t.degraded)}</span>` : '';
    return `
      <div class="aims-throttle">
        <span class="aims-throttle-title">通道节流/重试</span>
        <span>当前间隔 ${Number(t.current_interval || 0)}s（基线 ${Number(t.base_interval || 0)}s）</span>
        <span>限流命中 ${Number(t.rate_limit_hits || 0)}</span>
        ${retry}${degraded}
      </div>
    `;
  }
```

- [ ] **Step 2: Call it in the running-project card (`:1036-1037`)**

Old:
```javascript
        ${steps.length ? renderProgressSteps(steps) : ''}
        ${renderProgressLogs(progress.logs || [])}
```
New:
```javascript
        ${steps.length ? renderProgressSteps(steps) : ''}
        ${renderThrottle(progress.throttle)}
        ${renderProgressLogs(progress.logs || [])}
```

- [ ] **Step 3: Add minimal styles (Ocean Blue tokens, zero purple)**

Run: `grep -rn "aims-progress-logs" web/static/*.css web/static/css/*.css 2>/dev/null`
In the CSS file that defines `.aims-progress-logs`, append:
```css
.aims-throttle {
  display: flex; flex-wrap: wrap; gap: var(--space-3);
  align-items: center; margin-top: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--bg-muted); color: var(--fg-muted);
  font-size: 12px;
}
.aims-throttle-title { font-weight: 600; color: var(--fg); }
.aims-throttle-retry { color: var(--warning-fg); }
.aims-throttle-degraded { color: var(--danger-fg); }
```
> If no `.css` defines `.aims-progress-logs` (inline `<style>` in template), add the block there instead — grep result tells you which file.

- [ ] **Step 4: Bump cache-busting version**

Run: `grep -rn "ad_material_ai_analysis.js?v=" web/templates/`
Increment the `?v=` value in the matched template line (e.g. `v=20260609` → `v=20260614`).

- [ ] **Step 5: Manual verify (webapp-testing or browser)**

Open the running project view; confirm a "通道节流/重试" row shows current interval + 限流命中 count, and that retry/degraded badges appear when present. No console errors. (No JS unit framework in repo — visual check.)

- [ ] **Step 6: Commit**

```bash
git add web/static/ad_material_ai_analysis.js web/static/ web/templates/
git commit -m "feat(ad-material): render throttle/retry status on progress card"
```

---

## Task 9: 聚焦验证 + 收尾

**Files:** none (verification)

- [ ] **Step 1: Run focused related tests**

Run: `python3 scripts/pytest_related.py --base origin/master --run`
Expected: PASS. Must include `tests/test_ad_material_throttle.py`, `tests/test_ad_material_ai_analysis_scale.py`, `tests/test_ad_material_ai_analysis_routes.py`.

- [ ] **Step 2: If script finds no targets for a changed file, run explicitly**

Run: `python3 -m pytest tests/test_ad_material_throttle.py tests/test_ad_material_ai_analysis_scale.py tests/test_ad_material_ai_analysis_routes.py -q`
Expected: all PASS.

- [ ] **Step 3: Acceptance walkthrough (against spec §7)**

Confirm by reading the diff / running a dry project if possible:
1. `_PROJECT_TOP_N == 40`, `_MAX_AI_CANDIDATES == 80`; ranking产出 40。
2. `provider == google_wj` → 产品间 ~10s、限流退避重试、平稳回落（覆盖在单测）。
3. 进度页有「通道节流/重试」块 + 限流/重试/降级日志。
4. 降级在 `summary.degraded_list` 列出。
5. 非 google_wj → throttle.enabled False（单测 `test_disabled_for_non_google_wj`）。

- [ ] **Step 4: Report**

汇报：全量 pytest 是否跳过 + 理由（本次为局部功能改动，按仓库最小化规则跑 focused related tests）；实际运行的 focused 测试清单与结果。

---

## Self-Review notes

- Spec §4.1 产品池/扩候选/文案 → Task 3、Task 4。
- Spec §4.2 节流模块 → Task 1、Task 2。
- Spec §4.3 接入点（4 调用点 + 产品边界）→ Task 5、Task 6、Task 7 Step 3-4。
- Spec §4.4 重试耗尽降级 + 标注 → Task 2（degraded_events）、Task 7 Step 5（summary.degraded_list）。
- Spec §4.5 可视化（progress.throttle + logs + 前端）→ Task 7 Step 1-2、Task 8。
- Spec §4.6 配置 env → Task 1（ThrottleConfig.from_env）。
- Spec §6 测试 → Task 1/2/4/9。
- 类型一致性：`GoogleWjThrottle` / `guarded_invoke(fn, *, stage, product_id)` / `mark_product_boundary()` / `snapshot()` / `degraded_events` 在 Task 2 定义，Task 5-8 一致引用。`_invoke_via_throttle(throttle, *, stage, product_id=None, **invoke_kwargs)` 在 Task 5 定义，Task 6 一致引用（首参 `use_case_code` 以关键字传入）。
