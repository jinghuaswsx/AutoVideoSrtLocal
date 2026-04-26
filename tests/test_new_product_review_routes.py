"""路由集成测试 — B 子系统：新品审核。

使用 authed_client_no_db (admin) + authed_user_client_no_db (普通用户) fixture。
DB 层全部 monkeypatch mock，模板渲染通过 monkeypatch render_template。
"""
from __future__ import annotations

import json

import pytest


# ===========================================================================
# 公共 helpers
# ===========================================================================

def _patch_list_deps(monkeypatch):
    """Mock list_pending / list_enabled_languages_kv / _list_translators。"""
    monkeypatch.setattr(
        "appcore.new_product_review.list_pending",
        lambda **kw: [
            {
                "id": 1, "name": "Test Product", "product_code": "P001",
                "product_link": "https://example.com", "main_image": None,
                "translator_id": 10, "translator_name": "Alice",
                "cover_object_key": None, "mk_id": 123,
                "ai_score": 85.0, "ai_evaluation_result": "适合推广",
                "ai_evaluation_detail": None,
                "npr_decision_status": None, "npr_decided_countries": None,
                "npr_decided_at": None, "npr_eval_clip_path": None,
                "created_at": "2026-04-01 10:00:00",
                "updated_at": "2026-04-01 10:00:00",
            }
        ],
    )
    monkeypatch.setattr(
        "appcore.medias.list_enabled_languages_kv",
        lambda: [("de", "German"), ("fr", "French")],
    )
    monkeypatch.setattr(
        "web.routes.new_product_review._list_translators",
        lambda: [{"id": 10, "username": "Alice"}],
    )


# ===========================================================================
# Task 22: 列表 + 权限测试
# ===========================================================================

def test_get_index_admin_only(authed_user_client_no_db, monkeypatch):
    """普通用户访问 GET / → 403。"""
    _patch_list_deps(monkeypatch)
    resp = authed_user_client_no_db.get("/new-product-review/")
    assert resp.status_code == 403


def test_get_index_admin_ok(authed_client_no_db, monkeypatch):
    """admin 访问 GET / → 200 (mock render_template)。"""
    _patch_list_deps(monkeypatch)
    monkeypatch.setattr(
        "web.routes.new_product_review.render_template",
        lambda template, **ctx: f"<html>新品审核 rendered={template}</html>",
    )
    resp = authed_client_no_db.get("/new-product-review/")
    assert resp.status_code == 200
    assert b"\xe6\x96\xb0\xe5\x93\x81\xe5\xae\xa1\xe6\xa0\xb8" in resp.data  # "新品审核" UTF-8


def test_get_index_renders_template(authed_client_no_db, monkeypatch):
    """GET / admin → render_template 被调用，template 名正确。"""
    _patch_list_deps(monkeypatch)
    called = {}

    def fake_render(template, **ctx):
        called["template"] = template
        called["ctx"] = ctx
        return "<html>ok</html>"

    monkeypatch.setattr("web.routes.new_product_review.render_template", fake_render)
    resp = authed_client_no_db.get("/new-product-review/")
    assert resp.status_code == 200
    assert called["template"] == "new_product_review_list.html"
    assert "products" in called["ctx"]
    assert "languages" in called["ctx"]
    assert "translators" in called["ctx"]


def test_get_list_returns_json(authed_client_no_db, monkeypatch):
    """GET /api/list → 200 JSON，含 products / languages / translators。"""
    _patch_list_deps(monkeypatch)
    resp = authed_client_no_db.get("/new-product-review/api/list")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "products" in data
    assert "languages" in data
    assert "translators" in data
    assert len(data["products"]) == 1
    assert data["products"][0]["id"] == 1


def test_get_list_admin_only(authed_user_client_no_db, monkeypatch):
    """普通用户访问 GET /api/list → 403。"""
    _patch_list_deps(monkeypatch)
    resp = authed_user_client_no_db.get("/new-product-review/api/list")
    assert resp.status_code == 403


def test_get_list_languages_have_code_upper(authed_client_no_db, monkeypatch):
    """languages 字段包含 code_upper。"""
    _patch_list_deps(monkeypatch)
    resp = authed_client_no_db.get("/new-product-review/api/list")
    data = resp.get_json()
    assert data["languages"][0]["code_upper"] == "DE"


# ===========================================================================
# Task 23: evaluate 路由测试
# ===========================================================================

def test_post_evaluate_admin_only(authed_user_client_no_db, monkeypatch):
    """普通用户 POST /api/1/evaluate → 403。"""
    resp = authed_user_client_no_db.post("/new-product-review/api/1/evaluate")
    assert resp.status_code == 403


def test_post_evaluate_calls_service(authed_client_no_db, monkeypatch):
    """admin POST /api/1/evaluate → service.evaluate_product 被调，返回 200。"""
    monkeypatch.setattr(
        "appcore.new_product_review.evaluate_product",
        lambda product_id, actor_user_id: {
            "status": "evaluated",
            "product_id": product_id,
            "ai_score": 85.0,
            "ai_evaluation_result": "适合推广",
            "detail": {},
        },
    )
    resp = authed_client_no_db.post("/new-product-review/api/1/evaluate")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "evaluated"
    assert data["product_id"] == 1


def test_post_evaluate_handles_no_video(authed_client_no_db, monkeypatch):
    """mock raise NoVideoError → 422。"""
    import appcore.new_product_review as npr_svc

    def raise_no_video(product_id, actor_user_id):
        raise npr_svc.NoVideoError("no video")

    monkeypatch.setattr("appcore.new_product_review.evaluate_product", raise_no_video)
    resp = authed_client_no_db.post("/new-product-review/api/1/evaluate")
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "no_video"


def test_post_evaluate_handles_evaluation_error(authed_client_no_db, monkeypatch):
    """mock raise EvaluationError → 500。"""
    import appcore.new_product_review as npr_svc

    def raise_eval_err(product_id, actor_user_id):
        raise npr_svc.EvaluationError("LLM timeout")

    monkeypatch.setattr("appcore.new_product_review.evaluate_product", raise_eval_err)
    resp = authed_client_no_db.post("/new-product-review/api/1/evaluate")
    assert resp.status_code == 500
    assert resp.get_json()["error"] == "evaluation_failed"


def test_post_evaluate_product_not_found(authed_client_no_db, monkeypatch):
    """mock raise ProductNotFoundError → 404。"""
    import appcore.new_product_review as npr_svc

    def raise_not_found(product_id, actor_user_id):
        raise npr_svc.ProductNotFoundError("not found")

    monkeypatch.setattr("appcore.new_product_review.evaluate_product", raise_not_found)
    resp = authed_client_no_db.post("/new-product-review/api/5/evaluate")
    assert resp.status_code == 404


# ===========================================================================
# Task 24: decide 路由测试
# ===========================================================================

def test_post_decide_creates_task(authed_client_no_db, monkeypatch):
    """mock decide_approve 返回 {task_id, ...} → 200。"""
    monkeypatch.setattr(
        "appcore.new_product_review.decide_approve",
        lambda product_id, countries, translator_id, actor_user_id: {
            "task_id": 99,
            "product_id": product_id,
            "countries": countries,
        },
    )
    resp = authed_client_no_db.post(
        "/new-product-review/api/1/decide",
        json={"countries": ["DE", "FR"], "translator_id": 10},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["task_id"] == 99
    assert data["countries"] == ["DE", "FR"]


def test_post_decide_no_countries_returns_422(authed_client_no_db, monkeypatch):
    """mock raise ValueError → 422。"""
    import appcore.new_product_review as npr_svc

    def raise_value_err(product_id, countries, translator_id, actor_user_id):
        raise ValueError("countries must be non-empty")

    monkeypatch.setattr("appcore.new_product_review.decide_approve", raise_value_err)
    resp = authed_client_no_db.post(
        "/new-product-review/api/1/decide",
        json={"countries": [], "translator_id": 10},
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "no_countries"


def test_post_decide_invalid_translator_returns_422(authed_client_no_db, monkeypatch):
    """mock raise TranslatorInvalidError → 422。"""
    import appcore.new_product_review as npr_svc

    def raise_translator_err(product_id, countries, translator_id, actor_user_id):
        raise npr_svc.TranslatorInvalidError("invalid")

    monkeypatch.setattr("appcore.new_product_review.decide_approve", raise_translator_err)
    resp = authed_client_no_db.post(
        "/new-product-review/api/1/decide",
        json={"countries": ["DE"], "translator_id": 99},
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "invalid_translator"


def test_post_decide_already_approved_returns_422(authed_client_no_db, monkeypatch):
    """mock raise InvalidStateError → 422。"""
    import appcore.new_product_review as npr_svc

    def raise_state_err(product_id, countries, translator_id, actor_user_id):
        raise npr_svc.InvalidStateError("already approved")

    monkeypatch.setattr("appcore.new_product_review.decide_approve", raise_state_err)
    resp = authed_client_no_db.post(
        "/new-product-review/api/1/decide",
        json={"countries": ["DE"], "translator_id": 10},
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "already_decided"


def test_post_decide_admin_only(authed_user_client_no_db, monkeypatch):
    """普通用户 POST /api/1/decide → 403。"""
    resp = authed_user_client_no_db.post(
        "/new-product-review/api/1/decide",
        json={"countries": ["DE"], "translator_id": 10},
    )
    assert resp.status_code == 403


# ===========================================================================
# Task 25: reject 路由测试
# ===========================================================================

def test_post_reject_writes_status(authed_client_no_db, monkeypatch):
    """mock decide_reject → 200。"""
    monkeypatch.setattr(
        "appcore.new_product_review.decide_reject",
        lambda product_id, reason, actor_user_id: {"product_id": product_id},
    )
    resp = authed_client_no_db.post(
        "/new-product-review/api/1/reject",
        json={"reason": "产品质量不符合要求，不适合推广"},
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["product_id"] == 1


def test_post_reject_short_reason_returns_422(authed_client_no_db, monkeypatch):
    """mock raise ValueError → 422。"""
    import appcore.new_product_review as npr_svc

    def raise_value_err(product_id, reason, actor_user_id):
        raise ValueError("reason must be at least 10 characters")

    monkeypatch.setattr("appcore.new_product_review.decide_reject", raise_value_err)
    resp = authed_client_no_db.post(
        "/new-product-review/api/1/reject",
        json={"reason": "短"},
    )
    assert resp.status_code == 422
    assert resp.get_json()["error"] == "reason_required"


def test_post_reject_admin_only(authed_user_client_no_db, monkeypatch):
    """普通用户 POST /api/1/reject → 403。"""
    resp = authed_user_client_no_db.post(
        "/new-product-review/api/1/reject",
        json={"reason": "不适合"},
    )
    assert resp.status_code == 403


def test_post_reject_product_not_found(authed_client_no_db, monkeypatch):
    """mock raise ProductNotFoundError → 404。"""
    import appcore.new_product_review as npr_svc

    def raise_not_found(product_id, reason, actor_user_id):
        raise npr_svc.ProductNotFoundError("not found")

    monkeypatch.setattr("appcore.new_product_review.decide_reject", raise_not_found)
    resp = authed_client_no_db.post(
        "/new-product-review/api/99/reject",
        json={"reason": "产品不存在，这是测试"},
    )
    assert resp.status_code == 404
