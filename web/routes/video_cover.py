from __future__ import annotations

from datetime import date
import json
import tempfile
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from appcore.video_cover_generation import (
    VideoCoverGenerationError,
    generate_ad_copy_sets,
    generate_product_analysis,
    generate_video_analysis,
    generate_video_covers,
    video_cover_model_options,
)
from appcore.video_cover_generation import _fetch_product_image, _product_value
from appcore.meta_hot_posts.product_analysis import fetch_product_analysis
from pipeline.ffutil import probe_media_info
from web.auth import admin_required


bp = Blueprint("video_cover", __name__)


def _artifact_url(object_key: str) -> str:
    return url_for("medias.media_object_proxy", object_key=object_key)


def _attach_urls(payload: dict) -> dict:
    data = dict(payload)
    reference = dict(data.get("reference") or {})
    if reference.get("object_key") and not reference.get("url"):
        reference["url"] = _artifact_url(reference["object_key"])
    data["reference"] = reference

    covers = []
    for cover in data.get("covers") or []:
        row = dict(cover)
        if row.get("object_key") and not row.get("url"):
            row["url"] = _artifact_url(row["object_key"])
        covers.append(row)
    data["covers"] = covers
    return data


def _json_or_form(name: str) -> str:
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        return str(payload.get(name) or "").strip()
    return str(request.form.get(name) or "").strip()


def _parse_ad_copy_payload(raw: str | None) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _save_upload_to_temp(work_dir: str):
    upload = request.files.get("video_file")
    if not upload or not (upload.filename or "").strip():
        raise VideoCoverGenerationError("请上传视频文件")
    filename = upload.filename or "video.mp4"
    suffix = Path(filename).suffix or ".mp4"
    video_path = Path(work_dir) / f"source{suffix}"
    upload.save(video_path)
    return filename, video_path


def _extract_product(product_url: str):
    product = fetch_product_analysis(product_url)
    title = _product_value(product, "title")
    image_url = _product_value(product, "main_image_url")
    if not title:
        raise VideoCoverGenerationError("无法从商品链接提取商品标题")
    if not image_url:
        raise VideoCoverGenerationError("无法从商品链接提取商品主图")
    return product, title, image_url


def _product_payload(product_url: str, title: str, image_url: str) -> dict:
    return {
        "title": title,
        "main_image_url": image_url,
        "product_url": product_url,
    }


@bp.route("/video-cover", methods=["GET"])
@login_required
@admin_required
def page():
    return render_template("video_cover.html", model_options=video_cover_model_options())


@bp.route("/video-cover/api/product-analysis", methods=["POST"])
@login_required
@admin_required
def api_product_analysis():
    product_url = (request.form.get("product_url") or "").strip()
    try:
        with tempfile.TemporaryDirectory(prefix="video_cover_product_") as work_dir:
            product, title, image_url = _extract_product(product_url)
            image_bytes = _fetch_product_image(image_url)
            product_image_path = Path(work_dir) / "product_image.png"
            product_image_path.write_bytes(image_bytes)
            product_analysis = generate_product_analysis(
                product=product,
                product_title=title,
                main_image_url=image_url,
                product_image_path=product_image_path,
                provider=request.form.get("provider") or request.form.get("product_provider"),
                model=request.form.get("model") or request.form.get("product_model"),
                user_id=int(getattr(current_user, "id", 0) or 0),
            )
    except VideoCoverGenerationError as exc:
        message = str(exc)
        status_code = 502 if message.startswith("产品分析失败") else 400
        return jsonify({"ok": False, "error": message}), status_code
    return jsonify({
        "ok": True,
        "data": {
            "product": _product_payload(product_url, title, image_url),
            "product_analysis": product_analysis,
        },
    })


@bp.route("/video-cover/api/video-analysis", methods=["POST"])
@login_required
@admin_required
def api_video_analysis():
    product_url = (request.form.get("product_url") or "").strip()
    try:
        with tempfile.TemporaryDirectory(prefix="video_cover_video_") as work_dir:
            filename, video_path = _save_upload_to_temp(work_dir)
            product, title, image_url = _extract_product(product_url)
            video_analysis = generate_video_analysis(
                video_path=str(video_path),
                product_title=title,
                product_url=product_url,
                main_image_url=image_url,
                video_info=probe_media_info(str(video_path)),
                provider=request.form.get("provider") or request.form.get("video_provider"),
                model=request.form.get("model") or request.form.get("video_model"),
                user_id=int(getattr(current_user, "id", 0) or 0),
            )
    except VideoCoverGenerationError as exc:
        message = str(exc)
        status_code = 502 if message.startswith("视频分析失败") else 400
        return jsonify({"ok": False, "error": message}), status_code
    return jsonify({
        "ok": True,
        "data": {
            "product": _product_payload(product_url, title, image_url),
            "video_filename": filename,
            "video_analysis": video_analysis,
        },
    })


@bp.route("/video-cover/api/ad-copy", methods=["POST"])
@login_required
@admin_required
def api_ad_copy():
    try:
        ad_copy_sets = generate_ad_copy_sets(
            product_analysis=_json_or_form("product_analysis"),
            video_analysis=_json_or_form("video_analysis"),
            current_date=_json_or_form("current_date") or date.today().isoformat(),
            provider=_json_or_form("provider") or _json_or_form("ad_copy_provider"),
            model=_json_or_form("model") or _json_or_form("ad_copy_model"),
            user_id=int(getattr(current_user, "id", 0) or 0),
        )
    except VideoCoverGenerationError as exc:
        message = str(exc)
        status_code = 502 if message.startswith("文案创作失败") else 400
        return jsonify({"ok": False, "error": message}), status_code
    return jsonify({"ok": True, "data": {"ad_copy_sets": ad_copy_sets}})


@bp.route("/video-cover/api/generate", methods=["POST"])
@login_required
@admin_required
def api_generate():
    product_url = (request.form.get("product_url") or "").strip()
    upload = request.files.get("video_file")
    if not upload or not (upload.filename or "").strip():
        return jsonify({"ok": False, "error": "请上传视频文件"}), 400

    filename = upload.filename or "video.mp4"
    suffix = Path(filename).suffix or ".mp4"
    try:
        with tempfile.TemporaryDirectory(prefix="video_cover_upload_") as work_dir:
            video_path = Path(work_dir) / f"source{suffix}"
            upload.save(video_path)
            result = generate_video_covers(
                product_url=product_url,
                video_path=str(video_path),
                video_filename=filename,
                user_id=int(getattr(current_user, "id", 0) or 0),
                cover_provider=request.form.get("cover_provider") or request.form.get("provider") or "",
                cover_model=request.form.get("cover_model") or request.form.get("model") or "",
                product_analysis_provider=request.form.get("product_provider") or "",
                product_analysis_model=request.form.get("product_model") or "",
                video_analysis_provider=request.form.get("video_provider") or "",
                video_analysis_model=request.form.get("video_model") or "",
                ad_copy_provider=request.form.get("ad_copy_provider") or "",
                ad_copy_model=request.form.get("ad_copy_model") or "",
                product_analysis_text=request.form.get("product_analysis") or "",
                video_analysis_text=request.form.get("video_analysis") or "",
                ad_copy_payload=_parse_ad_copy_payload(request.form.get("ad_copy_sets")),
            )
    except VideoCoverGenerationError as exc:
        message = str(exc)
        status_code = 502 if message.startswith(("封面生成失败", "文案创作失败", "产品分析失败", "视频分析失败")) else 400
        return jsonify({"ok": False, "error": message}), status_code

    return jsonify({"ok": True, "data": _attach_urls(result)})
