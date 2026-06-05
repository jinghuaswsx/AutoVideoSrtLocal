from __future__ import annotations

import time
import pytest
from appcore import pushes, push_quality_checks


def test_delete_for_item_clears_database_records(monkeypatch):
    captured = []
    monkeypatch.setattr(push_quality_checks, "ensure_table", lambda: None)
    monkeypatch.setattr(
        push_quality_checks,
        "execute",
        lambda sql, args: captured.append((sql, args)) or 1
    )
    
    push_quality_checks.delete_for_item(123)
    
    assert len(captured) == 1
    assert "DELETE FROM media_push_quality_checks" in captured[0][0]
    assert captured[0][1] == (123,)


def test_api_reject_to_task_triggers_quality_reset(authed_client_no_db, monkeypatch):
    deleted_items = []
    
    monkeypatch.setattr(
        "web.routes.pushes.push_quality_checks.delete_for_item",
        lambda item_id: deleted_items.append(item_id)
    )
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {"id": item_id, "product_id": 1, "task_id": 999, "lang": "fr"}
    )
    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.reject_child_from_push",
        lambda **kwargs: {"ok": True, "issue_keys": []}
    )
    monkeypatch.setattr(
        "web.routes.pushes.pushes.refresh_push_status_cache_for_item",
        lambda item_id: None
    )
    monkeypatch.setattr(
        "web.routes.pushes.system_audit.record_from_request",
        lambda **kwargs: None
    )
    
    resp = authed_client_no_db.post(
        "/pushes/api/items/123/reject-to-task",
        json={
            "issue_keys": ["has_object"],
            "reason": "视频字幕错位打回重做",
        }
    )
    
    assert resp.status_code == 200
    assert 123 in deleted_items


def test_refresh_push_status_cache_rows_triggers_async_evaluate(monkeypatch):
    triggered_evals = []
    
    monkeypatch.setattr(pushes, "_upsert_push_status_cache_entries", lambda entries: len(entries))
    monkeypatch.setattr(pushes, "build_push_list_context", lambda rows: {})
    monkeypatch.setattr(
        pushes,
        "_status_cache_entry_from_row",
        lambda row, **kwargs: {
            "item_id": row["id"],
            "status": "pending",
            "readiness": {},
            "computed_at": None
        }
    )
    monkeypatch.setattr(pushes, "_push_product_shape_from_row", lambda row: {})
    
    from appcore import push_quality_checks as qc
    monkeypatch.setattr(
        qc,
        "has_reusable_auto_result_for_item",
        lambda row, product: False
    )
    monkeypatch.setattr(
        qc,
        "evaluate_item",
        lambda item_id, source: triggered_evals.append((item_id, source)) or {}
    )
    monkeypatch.setattr(pushes, "refresh_push_status_cache_for_item", lambda item_id: None)
    
    rows = [{"id": 456, "product_id": 1, "lang": "fr"}]
    
    with pushes._EVALUATING_LOCK:
        pushes._EVALUATING_ITEM_IDS.clear()
        
    pushes.refresh_push_status_cache_rows(rows)
    time.sleep(0.1)
    
    assert (456, "auto") in triggered_evals


def test_refresh_push_status_cache_rows_prevents_duplicate_concurrency(monkeypatch):
    triggered_evals = []
    
    monkeypatch.setattr(pushes, "_upsert_push_status_cache_entries", lambda entries: len(entries))
    monkeypatch.setattr(pushes, "build_push_list_context", lambda rows: {})
    monkeypatch.setattr(
        pushes,
        "_status_cache_entry_from_row",
        lambda row, **kwargs: {
            "item_id": row["id"],
            "status": "pending",
            "readiness": {},
            "computed_at": None
        }
    )
    monkeypatch.setattr(pushes, "_push_product_shape_from_row", lambda row: {})
    
    from appcore import push_quality_checks as qc
    monkeypatch.setattr(qc, "has_reusable_auto_result_for_item", lambda row, product: False)
    
    def slow_evaluate(item_id, source):
        time.sleep(0.2)
        triggered_evals.append((item_id, source))
        return {}
        
    monkeypatch.setattr(qc, "evaluate_item", slow_evaluate)
    monkeypatch.setattr(pushes, "refresh_push_status_cache_for_item", lambda item_id: None)
    
    rows = [{"id": 789}]
    
    with pushes._EVALUATING_LOCK:
        pushes._EVALUATING_ITEM_IDS.clear()
        
    pushes.refresh_push_status_cache_rows(rows)
    pushes.refresh_push_status_cache_rows(rows)
    time.sleep(0.3)
    
    assert len(triggered_evals) == 1
    assert triggered_evals == [(789, "auto")]
