"""B 子系统：新品审核 + AI 评估矩阵 路由。

详见 docs/superpowers/specs/2026-04-26-new-product-review-design.md
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import medias, new_product_review
from appcore.db import query as db_query

logger = logging.getLogger(__name__)

new_product_review_bp = Blueprint(
    "new_product_review",
    __name__,
    url_prefix="/new-product-review",
)


def _is_admin() -> bool:
    return getattr(current_user, "role", "") in ("admin", "superadmin") or \
        getattr(current_user, "is_admin", False)


def _list_translators() -> list[dict]:
    """列出所有有 can_translate 权限的活跃用户。"""
    rows = db_query(
        "SELECT id, username FROM users WHERE is_active=1 ORDER BY username ASC",
        (),
    )
    result = []
    import json as _json
    for row in rows:
        perms = row.get("permissions") or "{}"
        if isinstance(perms, str):
            try:
                perms = _json.loads(perms)
            except (TypeError, ValueError):
                perms = {}
        if perms.get("can_translate"):
            result.append({"id": row["id"], "username": row["username"]})
    return result


# ---- Task 18: GET / 渲染页面 ----

@new_product_review_bp.route("/", methods=["GET"])
@login_required
def index():
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403

    products = new_product_review.list_pending(limit=200)
    languages = medias.list_enabled_languages_kv()
    translators = _list_translators()

    languages_dicts = [
        {"code": code, "code_upper": code.upper(), "name_zh": name}
        for code, name in languages
    ]

    return render_template(
        "new_product_review_list.html",
        products=products,
        languages=languages_dicts,
        translators=translators,
        active_tab="new_product_review",
    )


# ---- Task 19: GET /api/list ----

@new_product_review_bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403

    products = new_product_review.list_pending(limit=200)
    languages = [
        {"code": code, "code_upper": code.upper(), "name_zh": name}
        for code, name in medias.list_enabled_languages_kv()
    ]
    translators = _list_translators()
    return jsonify({
        "products": products,
        "languages": languages,
        "translators": translators,
    })


# ---- Task 20: POST /api/<id>/evaluate ----

@new_product_review_bp.route("/api/<int:product_id>/evaluate", methods=["POST"])
@login_required
def api_evaluate(product_id):
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403

    try:
        result = new_product_review.evaluate_product(
            product_id, actor_user_id=int(current_user.id)
        )
        return jsonify(result), 200
    except new_product_review.ProductNotFoundError as e:
        return jsonify({"error": "product_not_found", "detail": str(e)}), 404
    except new_product_review.NoVideoError as e:
        return jsonify({"error": "no_video", "detail": str(e)}), 422
    except new_product_review.EvaluationError as e:
        return jsonify({"error": "evaluation_failed", "detail": str(e)}), 500
    except Exception as e:
        logger.exception("api_evaluate unexpected error product=%s", product_id)
        return jsonify({"error": "internal", "detail": str(e)}), 500


# ---- Task 21: POST /api/<id>/decide + POST /api/<id>/reject ----

@new_product_review_bp.route("/api/<int:product_id>/decide", methods=["POST"])
@login_required
def api_decide(product_id):
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403

    payload = request.get_json(silent=True) or {}
    countries = payload.get("countries") or []
    translator_id = payload.get("translator_id")

    try:
        result = new_product_review.decide_approve(
            product_id,
            countries=countries,
            translator_id=int(translator_id) if translator_id else 0,
            actor_user_id=int(current_user.id),
        )
        return jsonify(result), 200
    except new_product_review.ProductNotFoundError as e:
        return jsonify({"error": "product_not_found", "detail": str(e)}), 404
    except new_product_review.InvalidStateError as e:
        return jsonify({"error": "already_decided", "detail": str(e)}), 422
    except new_product_review.TranslatorInvalidError as e:
        return jsonify({"error": "invalid_translator", "detail": str(e)}), 422
    except new_product_review.NoVideoError as e:
        return jsonify({"error": "no_video", "detail": str(e)}), 422
    except ValueError as e:
        return jsonify({"error": "no_countries", "detail": str(e)}), 422
    except Exception as e:
        logger.exception("api_decide unexpected error product=%s", product_id)
        return jsonify({"error": "task_create_failed", "detail": str(e)}), 500


@new_product_review_bp.route("/api/<int:product_id>/reject", methods=["POST"])
@login_required
def api_reject(product_id):
    if not _is_admin():
        return jsonify({"error": "仅管理员可访问"}), 403

    payload = request.get_json(silent=True) or {}
    reason = payload.get("reason") or ""

    try:
        result = new_product_review.decide_reject(
            product_id,
            reason=reason,
            actor_user_id=int(current_user.id),
        )
        return jsonify(result), 200
    except new_product_review.ProductNotFoundError as e:
        return jsonify({"error": "product_not_found", "detail": str(e)}), 404
    except new_product_review.InvalidStateError as e:
        return jsonify({"error": "already_decided", "detail": str(e)}), 422
    except ValueError as e:
        return jsonify({"error": "reason_required", "detail": str(e)}), 422
    except Exception as e:
        logger.exception("api_reject unexpected error product=%s", product_id)
        return jsonify({"error": "internal", "detail": str(e)}), 500
