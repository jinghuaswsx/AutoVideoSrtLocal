"""Routes for single-product AI research page and API."""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from appcore.product_research_schemas import VALID_COUNTRY_CODES
from appcore.product_research_service import (
    ProductResearchError,
    ProductResearchNotFound,
    get_service,
)
from web.auth import admin_required

bp = Blueprint("product_research", __name__)


def _ok(data, status: int = 200):
    return jsonify({"success": True, "data": data, "error": None}), status


def _err(code: str, message: str, status: int = 400):
    return jsonify({"success": False, "data": None, "error": {"code": code, "message": message}}), status


# ── Page ─────────────────────────────────────────────────

@bp.route("/ai-product-research", methods=["GET"])
@login_required
@admin_required
def page():
    return render_template("product_research.html")


# ── API: Runs ────────────────────────────────────────────

@bp.route("/api/product-research/runs", methods=["POST"])
@login_required
@admin_required
def create_run():
    payload = request.get_json(silent=True) or {}
    try:
        service = get_service()
        run = service.create_run(payload)
        service.start_run_async(run["research_run_id"])
        return _ok(run, 202)
    except ValueError as exc:
        return _err("INVALID_REQUEST", str(exc), 400)


@bp.route("/api/product-research/runs/<research_run_id>/status", methods=["GET"])
@login_required
@admin_required
def get_status(research_run_id: str):
    try:
        return _ok(get_service().get_status(research_run_id))
    except ProductResearchNotFound as exc:
        return _err(exc.code, "Research run not found", 404)


@bp.route("/api/product-research/runs/<research_run_id>", methods=["GET"])
@login_required
@admin_required
def get_result(research_run_id: str):
    try:
        return _ok(get_service().get_result(research_run_id))
    except ProductResearchNotFound as exc:
        return _err(exc.code, "Research run not found", 404)


@bp.route("/api/product-research/runs/<research_run_id>/cancel", methods=["POST"])
@login_required
@admin_required
def cancel_run(research_run_id: str):
    try:
        return _ok(get_service().cancel_run(research_run_id))
    except ProductResearchNotFound as exc:
        return _err(exc.code, "Research run not found", 404)


@bp.route("/api/product-research/runs/<research_run_id>/countries/<country_code>/rerun", methods=["POST"])
@login_required
@admin_required
def rerun_country(research_run_id: str, country_code: str):
    code = str(country_code).strip().upper()
    if code not in VALID_COUNTRY_CODES:
        return _err("INVALID_COUNTRY_CODE", f"Unsupported country: {country_code}", 400)
    try:
        return _ok(get_service().rerun_country(research_run_id, code), 202)
    except ProductResearchNotFound as exc:
        return _err(exc.code, "Research run not found", 404)
    except ValueError as exc:
        return _err("INVALID_COUNTRY_CODE", str(exc), 400)


# ── API: Assets ──────────────────────────────────────────

@bp.route("/api/product-research/assets/upload", methods=["POST"])
@login_required
@admin_required
def upload_asset():
    if "file" not in request.files:
        return _err("NO_FILE", "No file provided", 400)
    file = request.files["file"]
    if not file.filename:
        return _err("NO_FILE", "No file selected", 400)
    asset_type = request.form.get("asset_type", "image")
    if asset_type not in ("image", "video"):
        return _err("INVALID_ASSET_TYPE", "asset_type must be image or video", 400)

    from config import UPLOAD_DIR
    import uuid
    from web.upload_util import save_uploaded_file_to_path

    asset_id = f"asset_{uuid.uuid4().hex}"
    ext = os.path.splitext(file.filename)[1] or ".bin"
    dest_path = os.path.join(UPLOAD_DIR, "product_research", f"{asset_id}{ext}")
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    try:
        saved_path = save_uploaded_file_to_path(file, dest_path)
    except ValueError as exc:
        return _err("UPLOAD_FAILED", str(exc), 400)

    from config import LOCAL_SERVER_BASE_URL
    base = (LOCAL_SERVER_BASE_URL or "").rstrip("/")
    relative = os.path.relpath(saved_path, os.path.dirname(UPLOAD_DIR))
    url = f"{base}/uploads/{relative.replace(os.sep, '/')}" if base else ""

    return _ok({
        "asset_id": asset_id,
        "asset_type": asset_type,
        "local_path": saved_path,
        "url": url,
        "mime_type": file.content_type or "",
    }, 201)