# AI精细评估商品链接检测 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 AI 精细评估创建前保证商品链接可访问，并在失败时从明空候选链接中选出可用链接。

**Architecture:** 后端新增一个小型链接检测服务，复用现有 `appcore.link_availability.probe`，由 `web.routes.xuanpin` 在创建外部链接 run 前调用。评估服务只接收最终链接和检测结果，并把结果写入 progress；前端只展示首步卡片和同步当前卡片链接。

**Tech Stack:** Python 3.12, Flask, pytest, Jinja inline JavaScript.

---

### Task 1: Link Check Service

**Files:**
- Create: `web/services/fine_ai_product_link_check.py`
- Test: `tests/test_fine_ai_product_link_check.py`

- [ ] **Step 1: Write failing service tests**

```python
from web.services.fine_ai_product_link_check import resolve_product_link

def test_resolve_product_link_keeps_current_when_available():
    calls = []
    result = resolve_product_link(
        current_link="https://shop.example/products/a",
        candidate_links=["https://shop.example/products/b"],
        probe_fn=lambda url: calls.append(url) or {"ok": True, "http_status": 200, "error": None, "elapsed_ms": 3},
    )
    assert result["ok"] is True
    assert result["selected_link"] == "https://shop.example/products/a"
    assert calls == ["https://shop.example/products/a"]

def test_resolve_product_link_uses_first_available_candidate():
    results = {
        "https://shop.example/products/a": {"ok": False, "http_status": 404, "error": "http 404", "elapsed_ms": 4},
        "https://shop.example/products/b": {"ok": True, "http_status": 200, "error": None, "elapsed_ms": 5},
    }
    result = resolve_product_link(
        current_link="https://shop.example/products/a",
        candidate_links=["https://shop.example/products/a", "https://shop.example/products/b"],
        probe_fn=lambda url: results[url],
    )
    assert result["ok"] is True
    assert result["selected_link"] == "https://shop.example/products/b"
    assert result["status"] == "replaced"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_fine_ai_product_link_check.py -q`

Expected: FAIL because `web.services.fine_ai_product_link_check` does not exist.

- [ ] **Step 3: Implement service**

Implement `resolve_product_link(current_link, candidate_links, probe_fn=link_availability.probe)` with de-duplicated sequential probes and a serializable result containing `ok`, `status`, `selected_link`, `original_link`, `candidates`, `checked_at`, and `message`.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_fine_ai_product_link_check.py -q`

Expected: PASS.

### Task 2: Backend Route And Run Progress

**Files:**
- Modify: `web/routes/xuanpin.py`
- Modify: `appcore/fine_ai_evaluation_service.py`
- Test: `tests/test_xuanpin_routes.py`
- Test: `tests/test_fine_ai_evaluation_pipeline.py`

- [ ] **Step 1: Write failing route and service tests**

Add a route test that posts a bad `product_link` plus `mk_product_id`, monkeypatches `_build_mk_detail_response` to return two `product_links`, monkeypatches the probe so the second link is ok, and asserts `create_external_link_run` receives the replacement link plus `link_check_result`.

Add a service test asserting `create_external_link_run(..., link_check_result=...)` stores `metadata.link_check`, uses `selected_link` in `product_snapshot.product_url`, and creates a completed `product_link_check` progress step before `data_preparation`.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_xuanpin_routes.py::test_xuanpin_fine_ai_external_link_replaces_unavailable_link_from_mingkong_candidates tests/test_fine_ai_evaluation_pipeline.py::test_external_link_run_records_product_link_check_progress -q`

Expected: FAIL because the route does not resolve candidates and the service has no `link_check_result` argument.

- [ ] **Step 3: Implement route and service changes**

In `web.routes.xuanpin`, collect current link and optional `mk_product_id`, call the link resolver before `create_external_link_run`, return `PRODUCT_LINK_UNAVAILABLE` if no candidate is available, and pass the chosen link plus `link_check_result` into the service.

In `appcore.fine_ai_evaluation_service`, add the `product_link_check` step to progress, store sanitized link-check metadata, and include `link_check` in the create response.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_xuanpin_routes.py::test_xuanpin_fine_ai_external_link_replaces_unavailable_link_from_mingkong_candidates tests/test_fine_ai_evaluation_pipeline.py::test_external_link_run_records_product_link_check_progress -q`

Expected: PASS.

### Task 3: Frontend Progress And Card Sync

**Files:**
- Modify: `web/templates/mk_selection.html`
- Test: `tests/test_fine_ai_evaluation_ui.py`
- Test: `tests/test_xuanpin_routes.py`

- [ ] **Step 1: Write failing UI tests**

Assert the template includes `product_link_check`, passes `mk_product_id`, calls `mkiFineAiApplyResolvedProductLink`, and renders first-step startup progress as link checking.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_fine_ai_evaluation_ui.py tests/test_xuanpin_routes.py::test_xuanpin_mk_video_cards_copy_code_and_show_first_mk_product_link -q`

Expected: FAIL on missing new strings.

- [ ] **Step 3: Implement UI changes**

Add `product_link_check` to default progress, make startup progress show it as running, include `mk_product_id` in fine AI button datasets and POST body, and update the current card link text/href/datasets from `resp.data.link_check.selected_link`.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_fine_ai_evaluation_ui.py tests/test_xuanpin_routes.py::test_xuanpin_mk_video_cards_copy_code_and_show_first_mk_product_link -q`

Expected: PASS.

### Task 4: Focused Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run targeted tests**

Run: `pytest tests/test_fine_ai_product_link_check.py tests/test_fine_ai_evaluation_pipeline.py tests/test_xuanpin_routes.py tests/test_fine_ai_evaluation_ui.py -q`

Expected: PASS without local MySQL access.

- [ ] **Step 2: Run syntax check**

Run: `python -m compileall appcore web tests -q`

Expected: PASS.
