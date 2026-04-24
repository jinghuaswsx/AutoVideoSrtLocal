# Shopify Image Task Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Shopify image replacement task center that queues product-language image replacement jobs, lets the desktop worker claim and report them, blocks push until manual confirmation, and surfaces actionable status in the product edit page.

**Architecture:** Add a focused `appcore/shopify_image_tasks.py` service for task lifecycle and product-language status. Keep the Windows CDP runner as the executor, add worker polling around it, and let existing Flask OpenAPI routes expose claim/complete/fail endpoints. Push readiness and the product edit modal read the same status summary so operations, automation, and AutoPush stay consistent.

**Tech Stack:** Python, Flask Blueprint, MySQL migrations, Tkinter desktop worker, pytest, existing CDP Shopify image localizer runner.

---

## File Structure

Create:

```text
appcore/shopify_image_tasks.py
db/migrations/2026_04_25_shopify_image_task_center.sql
tests/test_appcore_shopify_image_tasks.py
tests/test_openapi_shopify_image_tasks.py
tests/test_pushes_shopify_image_readiness.py
tests/test_medias_shopify_image_status_routes.py
tests/test_shopify_image_worker_loop.py
```

Modify:

```text
appcore/pushes.py
web/routes/openapi_materials.py
web/routes/medias.py
web/static/medias.js
web/templates/_medias_edit_detail_modal.html
tools/shopify_image_localizer/api_client.py
tools/shopify_image_localizer/controller.py
tools/shopify_image_localizer/rpa/ez_cdp.py
tools/shopify_image_localizer/rpa/run_product_cdp.py
```

Responsibilities:

- `appcore/shopify_image_tasks.py`: parse/update product-language status JSON; evaluate task readiness; create/reuse tasks; claim locks; complete/fail tasks; manual confirmation helpers.
- `web/routes/openapi_materials.py`: worker-facing claim/heartbeat/complete/fail routes.
- `appcore/pushes.py`: add `shopify_image_confirmed` to readiness for non-English items.
- `web/routes/medias.py`: product edit APIs for confirm/unavailable/requeue/clear and product serialization.
- `web/static/medias.js` + template: display current language status and action buttons.
- `tools/shopify_image_localizer/*`: poll tasks and report results; add EZ backend readback verification.

---

### Task 1: Migration And Schema Test

**Files:**
- Create: `db/migrations/2026_04_25_shopify_image_task_center.sql`
- Test: `tests/test_db_migration_shopify_image_task_center.py`

- [ ] **Step 1: Write the failing migration test**

```python
from pathlib import Path


def test_shopify_image_task_center_migration_declares_status_and_queue():
    sql = Path("db/migrations/2026_04_25_shopify_image_task_center.sql").read_text(encoding="utf-8")

    assert "ADD COLUMN shopify_image_status_json JSON NULL" in sql
    assert "CREATE TABLE IF NOT EXISTS media_shopify_image_replace_tasks" in sql
    assert "product_id" in sql
    assert "product_code" in sql
    assert "shopify_product_id" in sql
    assert "locked_until" in sql
    assert "result_json" in sql
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
pytest tests/test_db_migration_shopify_image_task_center.py -q
```

Expected: FAIL because the migration file does not exist.

- [ ] **Step 3: Add the migration**

```sql
ALTER TABLE media_products
  ADD COLUMN shopify_image_status_json JSON NULL COMMENT '按语种记录 Shopify 图片替换和链接确认状态 {lang: payload}';

CREATE TABLE IF NOT EXISTS media_shopify_image_replace_tasks (
  id                 BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
  product_id          INT          NOT NULL,
  product_code        VARCHAR(128) NOT NULL,
  lang                VARCHAR(8)   NOT NULL,
  shopify_product_id  VARCHAR(64)  NOT NULL,
  link_url            VARCHAR(1024) DEFAULT NULL,
  status              VARCHAR(24)  NOT NULL DEFAULT 'pending',
  attempt_count       INT          NOT NULL DEFAULT 0,
  max_attempts        INT          NOT NULL DEFAULT 3,
  worker_id           VARCHAR(128) DEFAULT NULL,
  locked_until        DATETIME     DEFAULT NULL,
  claimed_at          DATETIME     DEFAULT NULL,
  started_at          DATETIME     DEFAULT NULL,
  finished_at         DATETIME     DEFAULT NULL,
  error_code          VARCHAR(64)  DEFAULT NULL,
  error_message       TEXT         DEFAULT NULL,
  result_json         JSON         DEFAULT NULL,
  created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_shopify_image_task_status_lock (status, locked_until, id),
  KEY idx_shopify_image_task_product_lang (product_id, lang, status),
  KEY idx_shopify_image_task_worker (worker_id, status, locked_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Shopify 图片替换任务中心';
```

- [ ] **Step 4: Run test and verify GREEN**

Run:

```powershell
pytest tests/test_db_migration_shopify_image_task_center.py -q
```

Expected: PASS.

---

### Task 2: Product-Language Status Helpers

**Files:**
- Create: `appcore/shopify_image_tasks.py`
- Modify: `appcore/medias.py`
- Test: `tests/test_appcore_shopify_image_tasks.py`

- [ ] **Step 1: Write failing status helper tests**

```python
from appcore import shopify_image_tasks as sit


def test_parse_status_map_accepts_dict_json_and_empty():
    assert sit.parse_status_map(None) == {}
    assert sit.parse_status_map("") == {}
    assert sit.parse_status_map({"it": {"replace_status": "confirmed"}}) == {
        "it": {"replace_status": "confirmed"}
    }
    assert sit.parse_status_map('{"it":{"replace_status":"confirmed"}}') == {
        "it": {"replace_status": "confirmed"}
    }


def test_status_for_lang_applies_defaults():
    status = sit.status_for_lang({}, "it")

    assert status["replace_status"] == "none"
    assert status["link_status"] == "unknown"
    assert status["last_error"] == ""
    assert status["confirmed_by"] is None
    assert status["confirmed_at"] is None


def test_update_lang_status_serializes_json(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        sit.medias,
        "get_product",
        lambda product_id: {"id": product_id, "shopify_image_status_json": '{"it":{"replace_status":"failed"}}'},
    )

    def fake_update_product(product_id, **fields):
        captured["product_id"] = product_id
        captured["fields"] = fields
        return 1

    monkeypatch.setattr(sit.medias, "update_product", fake_update_product)

    sit.update_lang_status(7, "it", replace_status="auto_done", link_status="needs_review", last_error="")

    assert captured["product_id"] == 7
    payload = captured["fields"]["shopify_image_status_json"]
    assert payload["it"]["replace_status"] == "auto_done"
    assert payload["it"]["link_status"] == "needs_review"
    assert payload["it"]["last_error"] == ""
```

- [ ] **Step 2: Run test and verify RED**

Run:

```powershell
pytest tests/test_appcore_shopify_image_tasks.py::test_parse_status_map_accepts_dict_json_and_empty tests/test_appcore_shopify_image_tasks.py::test_status_for_lang_applies_defaults tests/test_appcore_shopify_image_tasks.py::test_update_lang_status_serializes_json -q
```

Expected: FAIL because `appcore.shopify_image_tasks` is missing and `media_products` updates do not allow the new JSON field.

- [ ] **Step 3: Implement minimal status helpers**

```python
# appcore/shopify_image_tasks.py
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from appcore import medias
from appcore.db import execute, query, query_one

REPLACE_NONE = "none"
REPLACE_PENDING = "pending"
REPLACE_RUNNING = "running"
REPLACE_AUTO_DONE = "auto_done"
REPLACE_FAILED = "failed"
REPLACE_CONFIRMED = "confirmed"

LINK_UNKNOWN = "unknown"
LINK_NEEDS_REVIEW = "needs_review"
LINK_NORMAL = "normal"
LINK_UNAVAILABLE = "unavailable"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_status_map(value: str | dict | None) -> dict[str, dict[str, Any]]:
    if isinstance(value, dict):
        return {str(k).strip().lower(): dict(v or {}) for k, v in value.items() if str(k).strip()}
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k).strip().lower(): dict(v or {}) for k, v in parsed.items() if str(k).strip() and isinstance(v, dict)}


def status_for_lang(status_map: dict[str, dict[str, Any]], lang: str) -> dict[str, Any]:
    lang = (lang or "").strip().lower()
    raw = dict((status_map or {}).get(lang) or {})
    return {
        "replace_status": raw.get("replace_status") or REPLACE_NONE,
        "link_status": raw.get("link_status") or LINK_UNKNOWN,
        "last_task_id": raw.get("last_task_id"),
        "last_error": raw.get("last_error") or "",
        "result_summary": raw.get("result_summary") or {},
        "confirmed_by": raw.get("confirmed_by"),
        "confirmed_at": raw.get("confirmed_at"),
        "updated_at": raw.get("updated_at"),
    }


def update_lang_status(product_id: int, lang: str, **updates: Any) -> dict[str, Any]:
    product = medias.get_product(product_id) or {}
    status_map = parse_status_map(product.get("shopify_image_status_json"))
    normalized_lang = (lang or "").strip().lower()
    current = status_for_lang(status_map, normalized_lang)
    current.update(updates)
    current["updated_at"] = _now_iso()
    status_map[normalized_lang] = current
    medias.update_product(product_id, shopify_image_status_json=status_map)
    return current
```

Modify `appcore/medias.py`:

```python
allowed = {
    "name", "color_people", "source", "archived",
    "importance", "trend_score", "selling_points",
    "product_code", "cover_object_key",
    "localized_links_json", "ad_supported_langs",
    "link_check_tasks_json", "shopify_image_status_json",
    "mk_id",
    "remark", "ai_score", "ai_evaluation_result",
    "ai_evaluation_detail", "listing_status",
}

if k in {"localized_links_json", "link_check_tasks_json", "shopify_image_status_json"} and isinstance(v, dict):
    return _json.dumps(v, ensure_ascii=False)
```

- [ ] **Step 4: Run test and verify GREEN**

Run:

```powershell
pytest tests/test_appcore_shopify_image_tasks.py::test_parse_status_map_accepts_dict_json_and_empty tests/test_appcore_shopify_image_tasks.py::test_status_for_lang_applies_defaults tests/test_appcore_shopify_image_tasks.py::test_update_lang_status_serializes_json -q
```

Expected: PASS.

---

### Task 3: Task Readiness, Creation, Claim, Complete, Fail

**Files:**
- Modify: `appcore/shopify_image_tasks.py`
- Test: `tests/test_appcore_shopify_image_tasks.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
from appcore import shopify_image_tasks as sit


def test_evaluate_candidate_requires_material_and_shopify_id(monkeypatch):
    monkeypatch.setattr(sit.medias, "get_product", lambda pid: {"id": pid, "product_code": "demo-rjc"})
    monkeypatch.setattr(sit.medias, "is_valid_language", lambda lang: lang == "it")
    monkeypatch.setattr(sit.medias, "resolve_shopify_product_id", lambda pid: "855")
    monkeypatch.setattr(
        sit.medias,
        "list_shopify_localizer_images",
        lambda pid, lang: [{"id": f"{lang}-1"}] if lang == "en" else [],
    )

    result = sit.evaluate_candidate(7, "it")

    assert result["ready"] is False
    assert result["block_code"] == "localized_images_not_ready"


def test_create_or_reuse_pending_task_inserts_ready_task(monkeypatch):
    calls = []
    monkeypatch.setattr(sit, "find_active_task", lambda product_id, lang: None)
    monkeypatch.setattr(
        sit,
        "evaluate_candidate",
        lambda product_id, lang: {
            "ready": True,
            "product": {"id": product_id, "product_code": "demo-rjc"},
            "shopify_product_id": "855",
            "link_url": "https://newjoyloo.com/it/products/demo-rjc",
        },
    )
    monkeypatch.setattr(sit, "execute", lambda sql, args=(): calls.append((sql, args)) or 44)
    monkeypatch.setattr(sit, "update_lang_status", lambda *args, **kwargs: calls.append(("status", args, kwargs)) or {})

    task = sit.create_or_reuse_task(7, "it")

    assert task["id"] == 44
    assert calls[0][1][:5] == (7, "demo-rjc", "it", "855", "https://newjoyloo.com/it/products/demo-rjc")
    assert calls[1][2]["replace_status"] == sit.REPLACE_PENDING


def test_claim_next_task_marks_running(monkeypatch):
    rows = [
        {"id": 9, "product_id": 7, "product_code": "demo-rjc", "lang": "it", "shopify_product_id": "855", "link_url": "url"}
    ]
    monkeypatch.setattr(sit, "query", lambda sql, args=(): rows if "SELECT" in sql else [])
    updates = []
    monkeypatch.setattr(sit, "execute", lambda sql, args=(): updates.append((sql, args)) or 1)
    monkeypatch.setattr(sit, "update_lang_status", lambda *args, **kwargs: updates.append(("status", args, kwargs)) or {})

    task = sit.claim_next_task("worker-1", lock_seconds=600)

    assert task["id"] == 9
    assert updates[0][1][0] == "worker-1"
    assert updates[1][2]["replace_status"] == sit.REPLACE_RUNNING


def test_complete_task_sets_auto_done_needs_review(monkeypatch):
    monkeypatch.setattr(sit, "get_task", lambda task_id: {"id": task_id, "product_id": 7, "lang": "it"})
    calls = []
    monkeypatch.setattr(sit, "execute", lambda sql, args=(): calls.append((sql, args)) or 1)
    monkeypatch.setattr(sit, "update_lang_status", lambda *args, **kwargs: calls.append(("status", args, kwargs)) or {})

    sit.complete_task(9, {"carousel": {"ok": 11}, "detail": {"replacement_count": 4}})

    assert calls[0][1][0] == '{"carousel": {"ok": 11}, "detail": {"replacement_count": 4}}'
    assert calls[1][2]["replace_status"] == sit.REPLACE_AUTO_DONE
    assert calls[1][2]["link_status"] == sit.LINK_NEEDS_REVIEW
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/test_appcore_shopify_image_tasks.py -q
```

Expected: FAIL for missing lifecycle functions.

- [ ] **Step 3: Implement lifecycle functions**

Add these functions to `appcore/shopify_image_tasks.py`:

```python
TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_SUCCESS = "success"
TASK_FAILED = "failed"
TASK_BLOCKED = "blocked"
TASK_CANCELLED = "cancelled"
ACTIVE_TASK_STATUSES = {TASK_PENDING, TASK_RUNNING}


def _loads_product_links(product: dict) -> dict[str, str]:
    links = {}
    raw = product.get("localized_links_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
    if isinstance(raw, dict):
        links = {str(k).strip().lower(): str(v).strip() for k, v in raw.items() if str(v).strip()}
    return links


def resolve_link_url(product: dict, lang: str) -> str:
    lang = (lang or "").strip().lower()
    links = _loads_product_links(product)
    if links.get(lang):
        return links[lang]
    code = (product.get("product_code") or "").strip()
    return f"https://newjoyloo.com/{lang}/products/{code}" if lang != "en" else f"https://newjoyloo.com/products/{code}"


def evaluate_candidate(product_id: int, lang: str) -> dict[str, Any]:
    lang = (lang or "").strip().lower()
    product = medias.get_product(product_id)
    if not product:
        return {"ready": False, "block_code": "product_not_found"}
    if lang == "en" or not medias.is_valid_language(lang):
        return {"ready": False, "block_code": "invalid_lang"}
    if not (product.get("product_code") or "").strip():
        return {"ready": False, "block_code": "product_code_missing", "product": product}
    current = status_for_lang(parse_status_map(product.get("shopify_image_status_json")), lang)
    if current["replace_status"] == REPLACE_CONFIRMED and current["link_status"] == LINK_NORMAL:
        return {"ready": False, "block_code": "already_confirmed", "product": product}
    shopify_id = medias.resolve_shopify_product_id(int(product_id))
    if not shopify_id:
        return {"ready": False, "block_code": "shopify_product_id_missing", "product": product}
    if not medias.list_shopify_localizer_images(int(product_id), "en"):
        return {"ready": False, "block_code": "english_references_not_ready", "product": product, "shopify_product_id": shopify_id}
    if not medias.list_shopify_localizer_images(int(product_id), lang):
        return {"ready": False, "block_code": "localized_images_not_ready", "product": product, "shopify_product_id": shopify_id}
    link_url = resolve_link_url(product, lang)
    return {"ready": True, "product": product, "shopify_product_id": shopify_id, "link_url": link_url}


def find_active_task(product_id: int, lang: str) -> dict | None:
    return query_one(
        "SELECT * FROM media_shopify_image_replace_tasks "
        "WHERE product_id=%s AND lang=%s AND status IN ('pending','running') "
        "ORDER BY id DESC LIMIT 1",
        (product_id, lang),
    )


def get_task(task_id: int) -> dict | None:
    return query_one("SELECT * FROM media_shopify_image_replace_tasks WHERE id=%s", (task_id,))


def create_or_reuse_task(product_id: int, lang: str) -> dict:
    lang = (lang or "").strip().lower()
    active = find_active_task(product_id, lang)
    if active:
        return active
    candidate = evaluate_candidate(product_id, lang)
    if not candidate.get("ready"):
        update_lang_status(product_id, lang, replace_status=REPLACE_FAILED, link_status=LINK_NEEDS_REVIEW, last_error=candidate.get("block_code") or "not_ready")
        return {"id": None, "status": TASK_BLOCKED, **candidate}
    product = candidate["product"]
    task_id = execute(
        "INSERT INTO media_shopify_image_replace_tasks "
        "(product_id, product_code, lang, shopify_product_id, link_url, status) "
        "VALUES (%s,%s,%s,%s,%s,'pending')",
        (product_id, product["product_code"], lang, candidate["shopify_product_id"], candidate["link_url"]),
    )
    update_lang_status(product_id, lang, replace_status=REPLACE_PENDING, link_status=LINK_UNKNOWN, last_task_id=task_id, last_error="")
    return get_task(task_id) or {"id": task_id, "status": TASK_PENDING, **candidate}


def claim_next_task(worker_id: str, lock_seconds: int = 900) -> dict | None:
    rows = query(
        "SELECT * FROM media_shopify_image_replace_tasks "
        "WHERE status='pending' OR (status='running' AND locked_until < NOW()) "
        "ORDER BY id ASC LIMIT 1"
    )
    if not rows:
        return None
    task = rows[0]
    updated = execute(
        "UPDATE media_shopify_image_replace_tasks "
        "SET status='running', worker_id=%s, locked_until=DATE_ADD(NOW(), INTERVAL %s SECOND), "
        "claimed_at=COALESCE(claimed_at, NOW()), started_at=COALESCE(started_at, NOW()), attempt_count=attempt_count+1 "
        "WHERE id=%s AND (status='pending' OR locked_until < NOW())",
        (worker_id, int(lock_seconds), task["id"]),
    )
    if not updated:
        return None
    update_lang_status(int(task["product_id"]), task["lang"], replace_status=REPLACE_RUNNING, link_status=LINK_NEEDS_REVIEW, last_task_id=task["id"])
    return get_task(task["id"]) or task


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    carousel = result.get("carousel") or {}
    detail = result.get("detail") or {}
    return {
        "carousel_requested": carousel.get("requested", 0),
        "carousel_ok": carousel.get("ok", 0),
        "carousel_skipped": carousel.get("skipped", 0),
        "detail_replacement_count": detail.get("replacement_count", 0),
        "detail_skipped_existing_count": detail.get("skipped_existing_count", 0),
    }


def complete_task(task_id: int, result: dict[str, Any]) -> dict[str, Any]:
    task = get_task(task_id)
    if not task:
        raise ValueError("task not found")
    payload = json.dumps(result or {}, ensure_ascii=False)
    execute(
        "UPDATE media_shopify_image_replace_tasks "
        "SET status='success', result_json=%s, error_code=NULL, error_message=NULL, finished_at=NOW(), locked_until=NULL "
        "WHERE id=%s",
        (payload, task_id),
    )
    return update_lang_status(
        int(task["product_id"]),
        task["lang"],
        replace_status=REPLACE_AUTO_DONE,
        link_status=LINK_NEEDS_REVIEW,
        last_task_id=task_id,
        last_error="",
        result_summary=summarize_result(result or {}),
    )


def fail_task(task_id: int, error_code: str, error_message: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    task = get_task(task_id)
    if not task:
        raise ValueError("task not found")
    link_status = LINK_UNAVAILABLE if error_code in {"link_unavailable", "not_found"} else LINK_NEEDS_REVIEW
    execute(
        "UPDATE media_shopify_image_replace_tasks "
        "SET status='failed', error_code=%s, error_message=%s, result_json=%s, finished_at=NOW(), locked_until=NULL "
        "WHERE id=%s",
        (error_code, error_message, json.dumps(result or {}, ensure_ascii=False), task_id),
    )
    return update_lang_status(
        int(task["product_id"]),
        task["lang"],
        replace_status=REPLACE_FAILED,
        link_status=link_status,
        last_task_id=task_id,
        last_error=error_message or error_code,
    )
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
pytest tests/test_appcore_shopify_image_tasks.py -q
```

Expected: PASS.

---

### Task 4: Worker OpenAPI Endpoints

**Files:**
- Modify: `web/routes/openapi_materials.py`
- Test: `tests/test_openapi_shopify_image_tasks.py`

- [ ] **Step 1: Write failing route tests**

```python
import importlib
import pytest
from web.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAPI_MEDIA_API_KEY", "demo-key")
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    import config as _config
    importlib.reload(_config)
    return create_app().test_client()


def test_claim_requires_api_key(client):
    response = client.post("/openapi/medias/shopify-image-localizer/tasks/claim", json={})
    assert response.status_code == 401


def test_claim_returns_task(client, monkeypatch):
    monkeypatch.setattr(
        "web.routes.openapi_materials.shopify_image_tasks.claim_next_task",
        lambda worker_id, lock_seconds=900: {
            "id": 9,
            "product_id": 7,
            "product_code": "demo-rjc",
            "lang": "it",
            "shopify_product_id": "855",
            "link_url": "url",
        },
    )

    response = client.post(
        "/openapi/medias/shopify-image-localizer/tasks/claim",
        headers={"X-API-Key": "demo-key"},
        json={"worker_id": "w1", "lock_seconds": 300},
    )

    assert response.status_code == 200
    assert response.get_json()["task"]["id"] == 9


def test_complete_marks_task_done(client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "web.routes.openapi_materials.shopify_image_tasks.complete_task",
        lambda task_id, result: captured.update({"task_id": task_id, "result": result}) or {"replace_status": "auto_done"},
    )

    response = client.post(
        "/openapi/medias/shopify-image-localizer/tasks/9/complete",
        headers={"X-API-Key": "demo-key"},
        json={"result": {"carousel": {"ok": 11}}},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert captured["task_id"] == 9
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/test_openapi_shopify_image_tasks.py -q
```

Expected: FAIL because routes/imports are missing.

- [ ] **Step 3: Implement routes**

In `web/routes/openapi_materials.py` import the service:

```python
from appcore import medias, pushes, tos_clients, shopify_image_tasks
```

Add routes:

```python
@shopify_localizer_bp.route("/tasks/claim", methods=["POST"])
def shopify_localizer_task_claim():
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    worker_id = str(body.get("worker_id") or "").strip() or "unknown-worker"
    lock_seconds = int(body.get("lock_seconds") or 900)
    task = shopify_image_tasks.claim_next_task(worker_id, lock_seconds=lock_seconds)
    return jsonify({"task": _serialize_shopify_image_task(task) if task else None})


@shopify_localizer_bp.route("/tasks/<int:task_id>/heartbeat", methods=["POST"])
def shopify_localizer_task_heartbeat(task_id: int):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    worker_id = str(body.get("worker_id") or "").strip()
    ok = shopify_image_tasks.heartbeat_task(task_id, worker_id, int(body.get("lock_seconds") or 900))
    return jsonify({"ok": bool(ok)})


@shopify_localizer_bp.route("/tasks/<int:task_id>/complete", methods=["POST"])
def shopify_localizer_task_complete(task_id: int):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    status = shopify_image_tasks.complete_task(task_id, body.get("result") or {})
    return jsonify({"ok": True, "status": status})


@shopify_localizer_bp.route("/tasks/<int:task_id>/fail", methods=["POST"])
def shopify_localizer_task_fail(task_id: int):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    status = shopify_image_tasks.fail_task(
        task_id,
        str(body.get("error_code") or "worker_failed"),
        str(body.get("error_message") or ""),
        body.get("result") or {},
    )
    return jsonify({"ok": True, "status": status})
```

Add helper:

```python
def _serialize_shopify_image_task(task: dict | None) -> dict | None:
    if not task:
        return None
    return {
        "id": task.get("id"),
        "product_id": task.get("product_id"),
        "product_code": task.get("product_code"),
        "lang": task.get("lang"),
        "shopify_product_id": task.get("shopify_product_id"),
        "link_url": task.get("link_url"),
    }
```

Add `heartbeat_task()` in `appcore/shopify_image_tasks.py`:

```python
def heartbeat_task(task_id: int, worker_id: str, lock_seconds: int = 900) -> int:
    return execute(
        "UPDATE media_shopify_image_replace_tasks "
        "SET locked_until=DATE_ADD(NOW(), INTERVAL %s SECOND) "
        "WHERE id=%s AND status='running' AND worker_id=%s",
        (int(lock_seconds), task_id, worker_id),
    )
```

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
pytest tests/test_openapi_shopify_image_tasks.py -q
```

Expected: PASS.

---

### Task 5: Worker Client And EZ Readback Verification

**Files:**
- Modify: `tools/shopify_image_localizer/api_client.py`
- Modify: `tools/shopify_image_localizer/controller.py`
- Modify: `tools/shopify_image_localizer/rpa/ez_cdp.py`
- Modify: `tools/shopify_image_localizer/rpa/run_product_cdp.py`
- Test: `tests/test_shopify_image_worker_loop.py`
- Test: `tests/test_shopify_image_localizer_batch_cdp.py`

- [ ] **Step 1: Write failing worker tests**

```python
from tools.shopify_image_localizer import api_client, controller


def test_worker_claim_posts_to_task_center(monkeypatch):
    calls = []

    class DummyResponse:
        status_code = 200
        def json(self):
            return {"task": {"id": 9, "product_code": "demo-rjc", "lang": "it", "shopify_product_id": "855"}}

    monkeypatch.setattr(api_client.requests, "post", lambda url, headers, json, timeout: calls.append((url, json)) or DummyResponse())

    payload = api_client.claim_task("http://server", "key", worker_id="w1")

    assert payload["task"]["id"] == 9
    assert calls[0][0] == "http://server/openapi/medias/shopify-image-localizer/tasks/claim"
    assert calls[0][1]["worker_id"] == "w1"


def test_run_worker_once_completes_claimed_task(monkeypatch):
    calls = []
    monkeypatch.setattr(
        controller.api_client,
        "claim_task",
        lambda base_url, api_key, worker_id, lock_seconds=900: {
            "task": {"id": 9, "product_code": "demo-rjc", "lang": "it", "shopify_product_id": "855"}
        },
    )
    monkeypatch.setattr(
        controller,
        "run_shopify_localizer",
        lambda **kwargs: {"status": "done", "carousel": {"ok": 1}},
    )
    monkeypatch.setattr(controller.api_client, "complete_task", lambda *args, **kwargs: calls.append(("complete", args, kwargs)) or {"ok": True})
    monkeypatch.setattr(controller.api_client, "fail_task", lambda *args, **kwargs: calls.append(("fail", args, kwargs)) or {"ok": True})

    result = controller.run_worker_once(
        base_url="http://server",
        api_key="key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        worker_id="w1",
    )

    assert result["status"] == "completed"
    assert calls[0][0] == "complete"


def test_run_worker_once_reports_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(
        controller.api_client,
        "claim_task",
        lambda *args, **kwargs: {"task": {"id": 9, "product_code": "demo-rjc", "lang": "it", "shopify_product_id": "855"}},
    )
    monkeypatch.setattr(controller, "run_shopify_localizer", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(controller.api_client, "fail_task", lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True})

    result = controller.run_worker_once(
        base_url="http://server",
        api_key="key",
        browser_user_data_dir=r"C:\chrome-shopify-image",
        worker_id="w1",
    )

    assert result["status"] == "failed"
    assert "boom" in calls[0][1]["error_message"]
```

Add EZ readback test in `tests/test_shopify_image_localizer_batch_cdp.py`:

```python
def test_verify_target_language_marks_all_expected_slots():
    from tools.shopify_image_localizer.rpa import ez_cdp

    class FakeFrame:
        def evaluate(self, script, arg=None):
            return [
                {"slot": 0, "languages": ["Italian"]},
                {"slot": 1, "languages": ["Italian", "Spanish"]},
            ]

    result = ez_cdp.verify_target_language_markers(FakeFrame(), [0, 1], "Italian")

    assert result["ok"] is True
    assert result["expected"] == 2
    assert result["matched"] == 2
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/test_shopify_image_worker_loop.py tests/test_shopify_image_localizer_batch_cdp.py::test_verify_target_language_marks_all_expected_slots -q
```

Expected: FAIL because client and verification functions do not exist.

- [ ] **Step 3: Implement worker client methods**

In `tools/shopify_image_localizer/api_client.py`:

```python
def claim_task(base_url: str, api_key: str, *, worker_id: str, lock_seconds: int = 900, timeout: int = 30) -> dict[str, Any]:
    response = requests.post(
        f"{base_url.rstrip('/')}/openapi/medias/shopify-image-localizer/tasks/claim",
        headers={"X-API-Key": api_key},
        json={"worker_id": worker_id, "lock_seconds": int(lock_seconds)},
        timeout=timeout,
    )
    payload = _json_payload(response)
    if response.status_code >= 400:
        raise ApiError(response.status_code, payload)
    return payload


def complete_task(base_url: str, api_key: str, task_id: int, *, result: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    response = requests.post(
        f"{base_url.rstrip('/')}/openapi/medias/shopify-image-localizer/tasks/{int(task_id)}/complete",
        headers={"X-API-Key": api_key},
        json={"result": result or {}},
        timeout=timeout,
    )
    payload = _json_payload(response)
    if response.status_code >= 400:
        raise ApiError(response.status_code, payload)
    return payload


def fail_task(base_url: str, api_key: str, task_id: int, *, error_code: str, error_message: str, result: dict[str, Any] | None = None, timeout: int = 30) -> dict[str, Any]:
    response = requests.post(
        f"{base_url.rstrip('/')}/openapi/medias/shopify-image-localizer/tasks/{int(task_id)}/fail",
        headers={"X-API-Key": api_key},
        json={"error_code": error_code, "error_message": error_message, "result": result or {}},
        timeout=timeout,
    )
    payload = _json_payload(response)
    if response.status_code >= 400:
        raise ApiError(response.status_code, payload)
    return payload
```

In `tools/shopify_image_localizer/controller.py`:

```python
def run_worker_once(*, base_url: str, api_key: str, browser_user_data_dir: str, worker_id: str, status_cb: StatusCallback | None = None) -> dict:
    reporter = status_cb or _noop
    claimed = api_client.claim_task(base_url, api_key, worker_id=worker_id)
    task = claimed.get("task")
    if not task:
        reporter("当前没有待处理任务")
        return {"status": "idle"}
    try:
        result = run_shopify_localizer(
            base_url=base_url,
            api_key=api_key,
            browser_user_data_dir=browser_user_data_dir,
            product_code=task["product_code"],
            lang=task["lang"],
            shopify_product_id=task.get("shopify_product_id") or "",
            status_cb=reporter,
        )
    except Exception as exc:
        api_client.fail_task(base_url, api_key, int(task["id"]), error_code=exc.__class__.__name__, error_message=str(exc))
        return {"status": "failed", "task": task, "error": str(exc)}
    api_client.complete_task(base_url, api_key, int(task["id"]), result=result)
    return {"status": "completed", "task": task, "result": result}
```

- [ ] **Step 4: Implement EZ readback marker verification**

In `tools/shopify_image_localizer/rpa/ez_cdp.py`:

```python
def verify_target_language_markers(frame, expected_slots: list[int], language: str) -> dict:
    rows = frame.evaluate(
        """() => Array.from(document.querySelectorAll('[data-index], .Polaris-IndexTable__TableRow, tr')).map((row, idx) => {
            const text = (row.textContent || '').trim();
            return {slot: idx, text, languages: text.split(/\\s+/)};
        })"""
    ) or []
    wanted = str(language or "").strip().lower()
    expected = {int(slot) for slot in expected_slots}
    matched = []
    missing = []
    for slot in sorted(expected):
        row = next((item for item in rows if int(item.get("slot") or 0) == slot), None)
        text = str((row or {}).get("text") or " ".join((row or {}).get("languages") or [])).lower()
        if wanted and wanted in text:
            matched.append(slot)
        else:
            missing.append(slot)
    return {"ok": not missing, "expected": len(expected), "matched": len(matched), "missing": missing}
```

In `run_product_cdp.py`, after `replace_many()`, reopen EZ and record verification:

```python
expected_slots = [slot for slot, _path in pairs]
carousel_verify = ez_cdp.verify_many_language_markers(
    ez_url=ez_url,
    user_data_dir=cfg["browser_user_data_dir"],
    expected_slots=expected_slots,
    language=args.language,
    port=args.port,
)
result["carousel"]["verify"] = carousel_verify
if not carousel_verify.get("ok"):
    raise RuntimeError(f"EZ language marker verification failed: {carousel_verify}")
```

Add `verify_many_language_markers()` as a wrapper that opens CDP Chrome, waits for the plugin frame, calls `verify_target_language_markers()`, then closes the page.

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```powershell
pytest tests/test_shopify_image_worker_loop.py tests/test_shopify_image_localizer_batch_cdp.py::test_verify_target_language_marks_all_expected_slots -q
```

Expected: PASS.

---

### Task 6: Push Readiness Blocks Unconfirmed Images

**Files:**
- Modify: `appcore/pushes.py`
- Modify: `web/routes/openapi_materials.py`
- Test: `tests/test_pushes_shopify_image_readiness.py`

- [ ] **Step 1: Write failing readiness tests**

```python
from appcore import pushes


def test_compute_readiness_blocks_non_english_until_shopify_images_confirmed(monkeypatch):
    monkeypatch.setattr(pushes.medias, "is_product_listed", lambda product: True)
    monkeypatch.setattr(pushes.medias, "parse_ad_supported_langs", lambda value: ["it"])
    monkeypatch.setattr(pushes, "_has_valid_en_push_texts", lambda product_id: True)
    monkeypatch.setattr(pushes, "query_one", lambda sql, args=(): {"ok": 1})

    monkeypatch.setattr(
        pushes.shopify_image_tasks,
        "is_confirmed_for_push",
        lambda product, lang: (False, "图片已自动替换，等待人工确认"),
    )

    readiness = pushes.compute_readiness(
        {"id": 1, "product_id": 7, "lang": "it", "object_key": "video.mp4", "cover_object_key": "cover.jpg"},
        {"id": 7, "ad_supported_langs": "it", "listing_status": "上架"},
    )

    assert readiness["shopify_image_confirmed"] is False
    assert readiness["shopify_image_reason"] == "图片已自动替换，等待人工确认"


def test_compute_readiness_allows_english_without_shopify_gate(monkeypatch):
    monkeypatch.setattr(pushes.medias, "is_product_listed", lambda product: True)
    monkeypatch.setattr(pushes.medias, "parse_ad_supported_langs", lambda value: ["it"])
    monkeypatch.setattr(pushes, "_has_valid_en_push_texts", lambda product_id: True)
    monkeypatch.setattr(pushes, "query_one", lambda sql, args=(): {"ok": 1})

    readiness = pushes.compute_readiness(
        {"id": 1, "product_id": 7, "lang": "en", "object_key": "video.mp4", "cover_object_key": "cover.jpg"},
        {"id": 7, "ad_supported_langs": "it", "listing_status": "上架"},
    )

    assert readiness["shopify_image_confirmed"] is True
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/test_pushes_shopify_image_readiness.py -q
```

Expected: FAIL because `compute_readiness()` has no Shopify image gate.

- [ ] **Step 3: Implement readiness helper**

In `appcore/shopify_image_tasks.py`:

```python
def is_confirmed_for_push(product: dict | None, lang: str) -> tuple[bool, str]:
    lang = (lang or "en").strip().lower()
    if lang == "en":
        return True, ""
    status = status_for_lang(parse_status_map((product or {}).get("shopify_image_status_json")), lang)
    if status["replace_status"] == REPLACE_CONFIRMED and status["link_status"] == LINK_NORMAL:
        return True, ""
    if status["link_status"] == LINK_UNAVAILABLE:
        return False, status["last_error"] or "链接不可用，已阻止推送"
    if status["replace_status"] == REPLACE_FAILED:
        return False, status["last_error"] or "图片自动替换失败，需要处理"
    if status["replace_status"] == REPLACE_AUTO_DONE:
        return False, "图片已自动替换，等待人工确认"
    return False, "图片尚未完成替换确认"
```

In `appcore/pushes.py`:

```python
from appcore import medias, settings as system_settings, shopify_image_tasks
```

Inside `compute_readiness()`:

```python
shopify_image_confirmed, shopify_image_reason = shopify_image_tasks.is_confirmed_for_push(product, lang)

return {
    "is_listed": is_listed,
    "has_object": has_object,
    "has_cover": has_cover,
    "has_copywriting": has_copywriting,
    "lang_supported": lang_supported,
    "has_push_texts": has_push_texts,
    "shopify_image_confirmed": shopify_image_confirmed,
    "shopify_image_reason": shopify_image_reason,
}
```

Update `is_ready()` so only boolean readiness items count:

```python
def is_ready(readiness: dict) -> bool:
    return all(value for key, value in readiness.items() if not key.endswith("_reason"))
```

In `web/routes/openapi_materials.py`, pass `shopify_image_status_json` into `product_shape` when serializing push items.

- [ ] **Step 4: Run tests and verify GREEN**

Run:

```powershell
pytest tests/test_pushes_shopify_image_readiness.py tests/test_openapi_materials_routes.py -q
```

Expected: PASS.

---

### Task 7: Product Edit Status APIs And UI Reminder

**Files:**
- Modify: `web/routes/medias.py`
- Modify: `web/templates/_medias_edit_detail_modal.html`
- Modify: `web/static/medias.js`
- Test: `tests/test_medias_shopify_image_status_routes.py`

- [ ] **Step 1: Write failing route tests**

```python
import importlib
import pytest
from web.app import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENAPI_MEDIA_API_KEY", "demo-key")
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    import config as _config
    importlib.reload(_config)
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_confirm_shopify_image_status_updates_state(client, monkeypatch):
    monkeypatch.setattr("web.routes.medias.current_user", type("U", (), {"id": 5, "is_authenticated": True})())
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: {"id": pid, "user_id": 5})
    monkeypatch.setattr("web.routes.medias._can_access_product", lambda product: True)
    monkeypatch.setattr("web.routes.medias.medias.is_valid_language", lambda lang: lang == "it")
    monkeypatch.setattr(
        "web.routes.medias.shopify_image_tasks.confirm_lang",
        lambda product_id, lang, user_id: {"replace_status": "confirmed", "link_status": "normal", "confirmed_by": user_id},
    )

    with client.session_transaction() as sess:
        sess["_user_id"] = "5"
        sess["_fresh"] = True

    response = client.post("/medias/api/products/7/shopify-image/it/confirm")

    assert response.status_code == 200
    assert response.get_json()["status"]["replace_status"] == "confirmed"


def test_requeue_shopify_image_status_returns_task(client, monkeypatch):
    monkeypatch.setattr("web.routes.medias.current_user", type("U", (), {"id": 5, "is_authenticated": True})())
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: {"id": pid, "user_id": 5})
    monkeypatch.setattr("web.routes.medias._can_access_product", lambda product: True)
    monkeypatch.setattr("web.routes.medias.medias.is_valid_language", lambda lang: lang == "it")
    monkeypatch.setattr(
        "web.routes.medias.shopify_image_tasks.create_or_reuse_task",
        lambda product_id, lang: {"id": 44, "status": "pending"},
    )

    with client.session_transaction() as sess:
        sess["_user_id"] = "5"
        sess["_fresh"] = True

    response = client.post("/medias/api/products/7/shopify-image/it/requeue")

    assert response.status_code == 200
    assert response.get_json()["task"]["id"] == 44
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
pytest tests/test_medias_shopify_image_status_routes.py -q
```

Expected: FAIL because routes do not exist.

- [ ] **Step 3: Implement server routes**

In `web/routes/medias.py` import:

```python
from appcore import local_media_storage, material_evaluation, medias, object_keys, pushes, task_state, shopify_image_tasks
```

In `_serialize_product()` add:

```python
"shopify_image_status": shopify_image_tasks.parse_status_map(p.get("shopify_image_status_json")),
```

Add routes:

```python
def _require_shopify_image_lang(pid: int, lang: str):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (lang or "").strip().lower()
    if not medias.is_valid_language(lang) or lang == "en":
        return p, None, (jsonify({"error": "invalid lang"}), 400)
    return p, lang, None


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/confirm", methods=["POST"])
@login_required
def api_shopify_image_confirm(pid: int, lang: str):
    _p, lang, error = _require_shopify_image_lang(pid, lang)
    if error:
        return error
    status = shopify_image_tasks.confirm_lang(pid, lang, current_user.id)
    return jsonify({"ok": True, "status": status})


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/unavailable", methods=["POST"])
@login_required
def api_shopify_image_unavailable(pid: int, lang: str):
    _p, lang, error = _require_shopify_image_lang(pid, lang)
    if error:
        return error
    body = request.get_json(silent=True) or {}
    status = shopify_image_tasks.mark_link_unavailable(pid, lang, str(body.get("reason") or "链接不可用"))
    return jsonify({"ok": True, "status": status})


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/requeue", methods=["POST"])
@login_required
def api_shopify_image_requeue(pid: int, lang: str):
    _p, lang, error = _require_shopify_image_lang(pid, lang)
    if error:
        return error
    task = shopify_image_tasks.create_or_reuse_task(pid, lang)
    return jsonify({"ok": True, "task": task})


@bp.route("/api/products/<int:pid>/shopify-image/<lang>/clear", methods=["POST"])
@login_required
def api_shopify_image_clear(pid: int, lang: str):
    _p, lang, error = _require_shopify_image_lang(pid, lang)
    if error:
        return error
    status = shopify_image_tasks.update_lang_status(pid, lang, replace_status=shopify_image_tasks.REPLACE_NONE, link_status=shopify_image_tasks.LINK_UNKNOWN, last_error="")
    return jsonify({"ok": True, "status": status})
```

Add helpers in `appcore/shopify_image_tasks.py`:

```python
def confirm_lang(product_id: int, lang: str, user_id: int) -> dict[str, Any]:
    return update_lang_status(product_id, lang, replace_status=REPLACE_CONFIRMED, link_status=LINK_NORMAL, confirmed_by=user_id, confirmed_at=_now_iso(), last_error="")


def mark_link_unavailable(product_id: int, lang: str, reason: str) -> dict[str, Any]:
    return update_lang_status(product_id, lang, link_status=LINK_UNAVAILABLE, last_error=reason or "链接不可用")
```

- [ ] **Step 4: Add minimal UI reminder**

In `_medias_edit_detail_modal.html`, under `edLinkCheckSummary`, add:

```html
<div id="edShopifyImageStatus" class="oc-link-check-summary" hidden></div>
```

In `web/static/medias.js`, add:

```javascript
function edShopifyImageStatusForLang(lang) {
  const product = edState.productData && edState.productData.product;
  const map = (product && product.shopify_image_status) || {};
  return map[lang] || {};
}

function edRenderShopifyImageStatus() {
  const box = $('edShopifyImageStatus');
  if (!box) return;
  const lang = edState.activeLang;
  if (!lang || lang === 'en') {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  const status = edShopifyImageStatusForLang(lang);
  const replaceStatus = status.replace_status || 'none';
  const linkStatus = status.link_status || 'unknown';
  const error = status.last_error || '';
  let label = '图片尚未完成替换确认';
  let kind = 'warning';
  if (replaceStatus === 'confirmed' && linkStatus === 'normal') {
    label = '已确认正常，可以推送';
    kind = 'success';
  } else if (replaceStatus === 'auto_done') {
    label = '自动换图完成，等待人工确认';
  } else if (replaceStatus === 'failed') {
    label = error || '自动换图失败，需要处理';
    kind = 'danger';
  } else if (linkStatus === 'unavailable') {
    label = error || '链接不可用，已阻止推送';
    kind = 'danger';
  }
  box.hidden = false;
  box.innerHTML = `
    <span class="oc-link-check-badge ${kind}">${escapeHtml(label)}</span>
    <button type="button" class="oc-btn ghost sm" data-shopify-image-action="confirm">确认链接图片正常</button>
    <button type="button" class="oc-btn ghost sm" data-shopify-image-action="requeue">重新排队换图</button>
    <button type="button" class="oc-btn text sm" data-shopify-image-action="unavailable">标记链接不可用</button>
  `;
}

async function edHandleShopifyImageAction(action) {
  const p = edState.productData && edState.productData.product;
  const lang = edState.activeLang;
  if (!p || !lang || lang === 'en') return;
  const url = `/medias/api/products/${p.id}/shopify-image/${encodeURIComponent(lang)}/${action}`;
  const body = action === 'unavailable' ? { reason: '人工标记链接不可用' } : {};
  const data = await fetchJSON(url, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } });
  if (!p.shopify_image_status) p.shopify_image_status = {};
  if (data.status) p.shopify_image_status[lang] = data.status;
  if (data.task && !p.shopify_image_status[lang]) p.shopify_image_status[lang] = { replace_status: 'pending', link_status: 'unknown' };
  edRenderShopifyImageStatus();
}
```

Call `edRenderShopifyImageStatus()` from `edRenderActiveLangView()` after rendering the product URL/link-check summary, and wire clicks:

```javascript
document.addEventListener('click', (event) => {
  const btn = event.target.closest('[data-shopify-image-action]');
  if (!btn) return;
  edHandleShopifyImageAction(btn.dataset.shopifyImageAction).catch((err) => alert(err.message || String(err)));
});
```

- [ ] **Step 5: Run route tests and focused JS layout tests**

Run:

```powershell
pytest tests/test_medias_shopify_image_status_routes.py tests/test_medias_edit_modal_layout.py -q
```

Expected: PASS.

---

### Task 8: Focused Verification And Commits

**Files:**
- Modify: `AGENTS.md` if command docs need updating.

- [ ] **Step 1: Run focused backend and worker tests**

Run:

```powershell
pytest tests/test_db_migration_shopify_image_task_center.py tests/test_appcore_shopify_image_tasks.py tests/test_openapi_shopify_image_tasks.py tests/test_pushes_shopify_image_readiness.py tests/test_medias_shopify_image_status_routes.py tests/test_shopify_image_worker_loop.py tests/test_shopify_image_localizer_batch_cdp.py tests/test_openapi_materials_routes.py tests/test_medias_edit_modal_layout.py -q
```

Expected: PASS.

- [ ] **Step 2: Run syntax verification**

Run:

```powershell
@'
import py_compile
for path in [
    r"appcore/shopify_image_tasks.py",
    r"appcore/pushes.py",
    r"web/routes/openapi_materials.py",
    r"web/routes/medias.py",
    r"tools/shopify_image_localizer/api_client.py",
    r"tools/shopify_image_localizer/controller.py",
    r"tools/shopify_image_localizer/rpa/ez_cdp.py",
    r"tools/shopify_image_localizer/rpa/run_product_cdp.py",
]:
    py_compile.compile(path, doraise=True)
print("ok")
'@ | python -
```

Expected: `ok`.

- [ ] **Step 3: Commit implementation**

Run:

```powershell
git add appcore/shopify_image_tasks.py appcore/medias.py appcore/pushes.py web/routes/openapi_materials.py web/routes/medias.py web/static/medias.js web/templates/_medias_edit_detail_modal.html tools/shopify_image_localizer/api_client.py tools/shopify_image_localizer/controller.py tools/shopify_image_localizer/rpa/ez_cdp.py tools/shopify_image_localizer/rpa/run_product_cdp.py db/migrations/2026_04_25_shopify_image_task_center.sql tests/test_db_migration_shopify_image_task_center.py tests/test_appcore_shopify_image_tasks.py tests/test_openapi_shopify_image_tasks.py tests/test_pushes_shopify_image_readiness.py tests/test_medias_shopify_image_status_routes.py tests/test_shopify_image_worker_loop.py tests/test_shopify_image_localizer_batch_cdp.py
git commit -m "feat: add shopify image replacement task center"
```

Expected: commit succeeds.

- [ ] **Step 4: Manual smoke checklist after deployment**

Run one claimed task on production API with a known product/language:

```powershell
python -m tools.shopify_image_localizer.main
```

Manual checks:

1. Worker can claim a task.
2. Carousel completes and EZ readback marker count matches expected slots.
3. Detail HTML saves and readback contains all expected CDN URLs.
4. Product edit page shows “自动换图完成，等待人工确认”.
5. AutoPush sees `shopify_image_confirmed=false` before confirmation.
6. After clicking “确认链接图片正常”, AutoPush sees readiness pass.
