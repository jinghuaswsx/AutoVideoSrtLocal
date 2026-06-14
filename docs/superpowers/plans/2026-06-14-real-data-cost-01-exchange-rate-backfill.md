# Plan ① 汇率历史回填 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 回填 2026-02-24 起每个缺失交易日的真实 USD/CNY 日汇率，让历史利润核算从固定 6.83 切换到真实日汇率。

**Architecture:** 复用现有 `appcore/exchange_rates.py` 的日汇率归档体系。新增「frankfurter 历史端点」取数 + 「单源历史回填」旁路（因为 `open_er_api`/`floatrates` 只有 latest、无历史端点，三源交叉校验无法用于历史日期），写入同一张 `usd_cny_daily_exchange_rates` 表，下游 `get_usd_to_cny_for_date` 自动按 `daily_archive` 读取。回填后刷新 30 天均值 fallback。全量重算在 Plan ② 末尾统一编排。

**Tech Stack:** Python 3.12、pymysql、frankfurter API（`https://api.frankfurter.app/{date}?from=USD&to=CNY`，已实测历史端点可用并回溯到 2026-02-24）、pytest + monkeypatch。

**Spec:** `docs/superpowers/specs/2026-06-14-cost-accounting-real-data-first-design.md` §6.1。

---

## File Structure

- **Modify** `appcore/exchange_rates.py`：① `fetch_frankfurter_usd_cny` 支持 `rate_date` 走历史端点；② 新增 `backfill_usd_cny_daily_rate()` 单源历史回填。
- **Modify** `tools/usd_cny_exchange_rate_sync.py`：新增 `run_backfill()` + `--backfill-from/--backfill-to` 参数，遍历缺失日回填并在结束后刷新 fallback。
- **Modify** `tests/test_usd_cny_exchange_rates.py`：新增历史端点 + 单源回填的单测。
- **Create** `tests/test_usd_cny_exchange_rate_sync.py`：`run_backfill` 跳过已有日期、容错的单测。

无新增 DB 表/迁移（沿用 `usd_cny_daily_exchange_rates`）。

---

## Task 1：`fetch_frankfurter_usd_cny` 支持历史日期

**Files:**
- Modify: `appcore/exchange_rates.py:29`（`FRANKFURTER_URL` 常量）、`appcore/exchange_rates.py:119-139`（`fetch_frankfurter_usd_cny`）
- Test: `tests/test_usd_cny_exchange_rates.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_usd_cny_exchange_rates.py` 末尾）

```python
def test_fetch_frankfurter_usd_cny_uses_historical_endpoint_for_date():
    from appcore import exchange_rates

    captured = {}

    def fake_get_json(url):
        captured["url"] = url
        return {"base": "USD", "date": "2026-02-24", "rates": {"CNY": "6.8817"}}

    quote = exchange_rates.fetch_frankfurter_usd_cny(
        rate_date=date(2026, 2, 24), get_json=fake_get_json
    )

    assert captured["url"] == "https://api.frankfurter.app/2026-02-24?from=USD&to=CNY"
    assert quote.source == "frankfurter"
    assert quote.rate == Decimal("6.8817")
    assert quote.source_date == date(2026, 2, 24)


def test_fetch_frankfurter_usd_cny_uses_latest_endpoint_without_date():
    from appcore import exchange_rates

    captured = {}

    def fake_get_json(url):
        captured["url"] = url
        return {"base": "USD", "date": "2026-06-14", "rates": {"CNY": "6.7623"}}

    exchange_rates.fetch_frankfurter_usd_cny(get_json=fake_get_json)
    assert captured["url"] == "https://api.frankfurter.app/latest?from=USD&to=CNY"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_usd_cny_exchange_rates.py::test_fetch_frankfurter_usd_cny_uses_historical_endpoint_for_date -v`
Expected: FAIL，`fetch_frankfurter_usd_cny() got an unexpected keyword argument 'rate_date'`

- [ ] **Step 3: 实现**

在 `appcore/exchange_rates.py` 把第 29 行的 `FRANKFURTER_URL` 常量替换为：

```python
FRANKFURTER_LATEST_URL = "https://api.frankfurter.app/latest?from=USD&to=CNY"
FRANKFURTER_HISTORICAL_URL = "https://api.frankfurter.app/{date}?from=USD&to=CNY"
# 向后兼容：旧引用仍可用
FRANKFURTER_URL = FRANKFURTER_LATEST_URL
```

把 `fetch_frankfurter_usd_cny`（第 119-139 行）改为接受 `rate_date`：

```python
def fetch_frankfurter_usd_cny(
    *, rate_date: date | None = None, get_json: Callable[[str], dict[str, Any]] | None = None
) -> RateQuote:
    url = (
        FRANKFURTER_HISTORICAL_URL.format(date=rate_date.isoformat())
        if rate_date is not None
        else FRANKFURTER_LATEST_URL
    )
    data = (get_json or _http_get_json)(url)
    base = str(data.get("base") or "").upper()
    if base != "USD":
        raise ValueError(f"frankfurter base must be USD, got {base!r}")
    rates = data.get("rates") or {}
    rate = _positive_decimal(rates.get("CNY"), label="frankfurter rates.CNY")
    source_date = _parse_iso_date(data.get("date"))
    return RateQuote(
        source="frankfurter",
        rate=rate,
        source_date=source_date,
        fetched_at=datetime.now(BEIJING_TZ),
        raw={
            "base": data.get("base"),
            "date": data.get("date"),
            "rates": {"CNY": rates.get("CNY")},
        },
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_usd_cny_exchange_rates.py -k frankfurter -v`
Expected: PASS（含既有 floatrates 测试不回归）

- [ ] **Step 5: Commit**

```bash
git add appcore/exchange_rates.py tests/test_usd_cny_exchange_rates.py
git commit -m "feat(exchange): frankfurter fetch 支持历史日期端点"
```

---

## Task 2：`backfill_usd_cny_daily_rate` 单源历史回填

**Files:**
- Modify: `appcore/exchange_rates.py`（在 `sync_usd_cny_daily_rate` 之后新增函数）
- Test: `tests/test_usd_cny_exchange_rates.py`

- [ ] **Step 1: 写失败测试**

```python
def test_backfill_usd_cny_daily_rate_writes_single_source(monkeypatch):
    from appcore import exchange_rates

    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 12

    monkeypatch.setattr(exchange_rates, "execute", fake_execute)

    summary = exchange_rates.backfill_usd_cny_daily_rate(
        rate_date=date(2026, 2, 24),
        fetcher=lambda rate_date: _quote("frankfurter", "6.8817", rate_date),
        source_run_id=5,
    )

    assert summary["rate_date"] == "2026-02-24"
    assert summary["usd_to_cny"] == 6.8817
    assert summary["primary"]["source"] == "frankfurter"
    assert summary["validators"] == []
    assert summary["sample_status"] == "single_source_historical"
    # _upsert_validated_rate 的 args：[0]=rate_date,[5]=validator_quotes_json,[6]=max_diff
    assert captured["args"][0] == date(2026, 2, 24)
    import json as _json
    assert _json.loads(captured["args"][5]) == []
    assert captured["args"][6] == Decimal("0E-8")
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_usd_cny_exchange_rates.py::test_backfill_usd_cny_daily_rate_writes_single_source -v`
Expected: FAIL，`module 'appcore.exchange_rates' has no attribute 'backfill_usd_cny_daily_rate'`

- [ ] **Step 3: 实现**（在 `appcore/exchange_rates.py` 的 `sync_usd_cny_daily_rate` 函数后新增）

```python
def backfill_usd_cny_daily_rate(
    *,
    rate_date: date,
    fetcher: Callable[[date], RateQuote] | None = None,
    source_run_id: int | None = None,
) -> dict[str, Any]:
    """单源历史回填：open_er_api / floatrates 无历史端点，三源交叉校验不适用于历史日期，
    故仅用 frankfurter 历史值写入，validators 置空、max_diff=0，标注 single_source_historical。
    写入同一张 usd_cny_daily_exchange_rates，下游按 daily_archive 读取。
    """
    fetch = fetcher or (lambda d: fetch_frankfurter_usd_cny(rate_date=d))
    primary = fetch(rate_date)
    row_id = _upsert_validated_rate(
        rate_date=rate_date,
        primary=primary,
        validators=[],
        max_relative_diff=Decimal("0"),
        tolerance_ratio=DEFAULT_TOLERANCE_RATIO,
        source_run_id=source_run_id,
    )
    return {
        "rate_date": rate_date.isoformat(),
        "usd_to_cny": float(_q6(primary.rate)),
        "primary": primary.as_summary(),
        "validators": [],
        "sample_status": "single_source_historical",
        "row_id": row_id,
    }
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_usd_cny_exchange_rates.py::test_backfill_usd_cny_daily_rate_writes_single_source -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add appcore/exchange_rates.py tests/test_usd_cny_exchange_rates.py
git commit -m "feat(exchange): 新增单源历史回填 backfill_usd_cny_daily_rate"
```

---

## Task 3：sync 脚本 `run_backfill` 遍历缺失日

**Files:**
- Modify: `tools/usd_cny_exchange_rate_sync.py`
- Create: `tests/test_usd_cny_exchange_rate_sync.py`

- [ ] **Step 1: 写失败测试**（新建 `tests/test_usd_cny_exchange_rate_sync.py`）

```python
from __future__ import annotations

from datetime import date


def test_run_backfill_skips_existing_and_collects_results(monkeypatch):
    from tools import usd_cny_exchange_rate_sync as sync_mod
    from appcore import exchange_rates

    # 已有 2/25，缺 2/24 和 2/26
    monkeypatch.setattr(
        sync_mod, "_existing_rate_dates",
        lambda date_from, date_to: {date(2026, 2, 25)},
    )
    calls = []

    def fake_backfill(*, rate_date, source_run_id=None):
        calls.append(rate_date)
        return {"rate_date": rate_date.isoformat(), "usd_to_cny": 6.88, "sample_status": "single_source_historical"}

    monkeypatch.setattr(exchange_rates, "backfill_usd_cny_daily_rate", fake_backfill)
    monkeypatch.setattr(sync_mod.exchange_rates, "refresh_usd_cny_fallback_rate", lambda **kw: {"sample_count": 3})

    results = sync_mod.run_backfill(date_from=date(2026, 2, 24), date_to=date(2026, 2, 26))

    assert calls == [date(2026, 2, 24), date(2026, 2, 26)]
    assert [r["rate_date"] for r in results["filled"]] == ["2026-02-24", "2026-02-26"]
    assert results["skipped"] == ["2026-02-25"]


def test_run_backfill_records_failure_without_aborting(monkeypatch):
    from tools import usd_cny_exchange_rate_sync as sync_mod
    from appcore import exchange_rates

    monkeypatch.setattr(sync_mod, "_existing_rate_dates", lambda date_from, date_to: set())

    def fake_backfill(*, rate_date, source_run_id=None):
        if rate_date == date(2026, 2, 24):
            raise RuntimeError("frankfurter 5xx")
        return {"rate_date": rate_date.isoformat(), "usd_to_cny": 6.88}

    monkeypatch.setattr(exchange_rates, "backfill_usd_cny_daily_rate", fake_backfill)
    monkeypatch.setattr(sync_mod.exchange_rates, "refresh_usd_cny_fallback_rate", lambda **kw: {"sample_count": 1})

    results = sync_mod.run_backfill(date_from=date(2026, 2, 24), date_to=date(2026, 2, 25))

    assert results["filled"][0]["rate_date"] == "2026-02-25"
    assert results["failed"][0]["rate_date"] == "2026-02-24"
    assert "frankfurter 5xx" in results["failed"][0]["error"]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_usd_cny_exchange_rate_sync.py -v`
Expected: FAIL，`module 'tools.usd_cny_exchange_rate_sync' has no attribute 'run_backfill'`

- [ ] **Step 3: 实现**（在 `tools/usd_cny_exchange_rate_sync.py` 顶部 import 增加 `timedelta`，并新增函数）

import 行（第 11 行）改为：

```python
from datetime import datetime, timedelta
```

在 `run_sync` 之后新增：

```python
def _existing_rate_dates(date_from, date_to) -> set:
    from appcore.db import query
    rows = query(
        "SELECT rate_date FROM usd_cny_daily_exchange_rates WHERE rate_date BETWEEN %s AND %s",
        (date_from, date_to),
    )
    out = set()
    for row in rows or []:
        value = row.get("rate_date")
        out.add(value if hasattr(value, "year") else _parse_date(str(value)[:10]))
    return out


def run_backfill(*, date_from, date_to) -> dict:
    """遍历 [date_from, date_to] 缺失日，单源回填 frankfurter 历史汇率；结束后刷新 30 天 fallback。"""
    existing = _existing_rate_dates(date_from, date_to)
    filled, failed, skipped = [], [], []
    cur = date_from
    while cur <= date_to:
        if cur in existing:
            skipped.append(cur.isoformat())
        else:
            try:
                filled.append(exchange_rates.backfill_usd_cny_daily_rate(rate_date=cur))
            except Exception as exc:  # noqa: BLE001 - 单日失败不阻断整段回填
                log.warning("backfill %s failed: %s", cur, exc)
                failed.append({"rate_date": cur.isoformat(), "error": str(exc)})
        cur += timedelta(days=1)
    fallback = exchange_rates.refresh_usd_cny_fallback_rate(fallback_date=date_to)
    summary = {
        "task_code": TASK_CODE,
        "mode": "backfill",
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "filled": filled,
        "failed": failed,
        "skipped": skipped,
        "fallback": fallback,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary
```

在 `build_arg_parser` 增加两个参数（`--date` 之后）：

```python
    parser.add_argument("--backfill-from", help="历史回填起始日 YYYY-MM-DD（与 --backfill-to 同用）。")
    parser.add_argument("--backfill-to", help="历史回填结束日 YYYY-MM-DD。")
```

把 `main` 改为支持回填分支：

```python
def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.backfill_from or args.backfill_to:
        if not (args.backfill_from and args.backfill_to):
            raise SystemExit("--backfill-from 与 --backfill-to 必须同时提供")
        results = run_backfill(
            date_from=_parse_date(args.backfill_from),
            date_to=_parse_date(args.backfill_to),
        )
        return 1 if results["failed"] else 0
    summary = run_sync(
        rate_date=_parse_date(args.rate_date),
        tolerance_ratio=Decimal(str(args.tolerance_ratio)),
    )
    return 1 if (summary.get("fallback") or {}).get("status") == "failed" else 0
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_usd_cny_exchange_rate_sync.py -v`
Expected: PASS（2 项）

- [ ] **Step 5: Commit**

```bash
git add tools/usd_cny_exchange_rate_sync.py tests/test_usd_cny_exchange_rate_sync.py
git commit -m "feat(exchange): sync 脚本支持 --backfill-from/--backfill-to 历史回填"
```

---

## Task 4：运维回填执行（线上，非代码）

**前置**：Task 1-3 已部署到线上 `/opt/autovideosrt`。

- [ ] **Step 1: dry 验证单日**

```bash
ssh avsl 'cd /opt/autovideosrt && sudo venv/bin/python tools/usd_cny_exchange_rate_sync.py --backfill-from 2026-02-24 --backfill-to 2026-02-24'
```
Expected: JSON 含 `filled[0].usd_to_cny`≈6.88、`sample_status=single_source_historical`。

- [ ] **Step 2: 全量回填 2/24~昨天**

```bash
ssh avsl 'cd /opt/autovideosrt && sudo venv/bin/python tools/usd_cny_exchange_rate_sync.py --backfill-from 2026-02-24 --backfill-to 2026-06-13'
```
Expected: `failed` 为空或极少；`skipped` 含已有的 6/6–6/13。

- [ ] **Step 3: 验证覆盖**

```bash
ssh avsl 'cd /opt/autovideosrt && sudo venv/bin/python -c "from appcore.db import query_one; print(query_one(\"SELECT MIN(rate_date) a, MAX(rate_date) b, COUNT(*) n FROM usd_cny_daily_exchange_rates\"))"'
```
Expected: `a≈2026-02-24`、`n` 覆盖约 110 个交易日（周末顺延会去重）。

> **全量重算**（让历史利润行真正用上回填的日汇率）在 **Plan ② 末尾统一编排**（`order_profit_backfill` 不传 `--rmb`），本 plan 只负责把日汇率数据补齐。

---

## Self-Review

- **Spec 覆盖**：§6.1「历史端点回填」=Task 1+2+4；「三源交叉校验」对历史不适用，已用单源旁路并在 §6.1 of spec 的「需支持传入日期走历史端点」基础上明确（建议在 spec §6.1 补一句「历史回填为 frankfurter 单源、标 single_source_historical」——见交付说明）；「降级链保持」无需改；「全量重算」转 Plan ②。
- **占位符**：无 TODO/TBD；每个代码步给出完整代码与命令。
- **类型一致**：`backfill_usd_cny_daily_rate(rate_date=, fetcher=, source_run_id=)` 在 Task 2 定义、Task 3 调用一致；`run_backfill(date_from=, date_to=)` 返回 `{filled, failed, skipped, fallback}` 在测试与实现一致；`_existing_rate_dates` 定义与 monkeypatch 一致。
- **回归**：Task 1 保留 `FRANKFURTER_URL` 别名，既有 `sync_usd_cny_daily_rate` 三源路径不受影响（既有测试 `test_sync_three_sources_*` 仍 PASS）。
