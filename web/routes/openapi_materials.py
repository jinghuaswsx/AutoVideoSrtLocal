"""素材信息开放接口。

- 使用 ``X-API-Key`` 校验请求，密钥从 ``config.OPENAPI_MEDIA_API_KEY`` 读取
- 按 ``product_code`` 聚合返回产品基础信息、主图、文案和视频素材
- 主图 / 视频 / 视频封面的下载地址均为 TOS 临时签名地址
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from flask import Blueprint, jsonify, request

import config
from appcore import medias, tos_clients

bp = Blueprint("openapi_materials", __name__, url_prefix="/openapi/materials")


def _api_key_valid() -> bool:
    expected = (config.OPENAPI_MEDIA_API_KEY or "").strip()
    provided = (request.headers.get("X-API-Key") or "").strip()
    return bool(expected) and provided == expected


def _iso_or_none(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _serialize_product(product: dict) -> dict:
    return {
        "id": product.get("id"),
        "product_code": product.get("product_code"),
        "name": product.get("name"),
        "archived": bool(product.get("archived")),
        "created_at": _iso_or_none(product.get("created_at")),
        "updated_at": _iso_or_none(product.get("updated_at")),
    }


def _serialize_cover_map(covers: dict) -> dict:
    payload: dict = {}
    for lang, object_key in (covers or {}).items():
        if not object_key:
            continue
        payload[lang] = {
            "object_key": object_key,
            "download_url": tos_clients.generate_signed_media_download_url(object_key),
            "expires_in": config.TOS_SIGNED_URL_EXPIRES,
        }
    return payload


def _group_copywritings(rows: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows or []:
        lang = row.get("lang") or "en"
        grouped[lang].append({
            "title": row.get("title"),
            "body": row.get("body"),
            "description": row.get("description"),
            "ad_carrier": row.get("ad_carrier"),
            "ad_copy": row.get("ad_copy"),
            "ad_keywords": row.get("ad_keywords"),
        })
    return dict(grouped)


def _serialize_items(rows: list[dict]) -> list[dict]:
    items: list[dict] = []
    for row in rows or []:
        object_key = row.get("object_key")
        cover_object_key = row.get("cover_object_key")
        items.append({
            "id": row.get("id"),
            "lang": row.get("lang") or "en",
            "filename": row.get("filename"),
            "display_name": row.get("display_name") or row.get("filename"),
            "object_key": object_key,
            "video_download_url": (
                tos_clients.generate_signed_media_download_url(object_key)
                if object_key else None
            ),
            "cover_object_key": cover_object_key,
            "video_cover_download_url": (
                tos_clients.generate_signed_media_download_url(cover_object_key)
                if cover_object_key else None
            ),
            "duration_seconds": row.get("duration_seconds"),
            "file_size": row.get("file_size"),
            "created_at": _iso_or_none(row.get("created_at")),
        })
    return items


@bp.route("/<product_code>", methods=["GET"])
def get_material(product_code: str):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    product = medias.get_product_by_code((product_code or "").strip().lower())
    if not product:
        return jsonify({"error": "product not found"}), 404

    product_id = product["id"]
    covers = medias.get_product_covers(product_id)
    copywritings = medias.list_copywritings(product_id)
    items = medias.list_items(product_id)

    return jsonify({
        "product": _serialize_product(product),
        "covers": _serialize_cover_map(covers),
        "copywritings": _group_copywritings(copywritings),
        "items": _serialize_items(items),
        "expires_in": config.TOS_SIGNED_URL_EXPIRES,
    })


@bp.route("/<product_code>/push-payload", methods=["GET"])
def build_push_payload(product_code: str):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    lang = (request.args.get("lang") or "").strip().lower()
    if not lang:
        return jsonify({"error": "missing lang"}), 400

    code = (product_code or "").strip().lower()
    product = medias.get_product_by_code(code)
    if not product:
        return jsonify({"error": "product not found"}), 404

    product_id = product["id"]
    items = medias.list_items(product_id, lang)

    product_links = (
        [f"https://newjoyloo.com/{lang}/products/{code}-rjc"]
        if lang != "en" else []
    )

    texts = [{"title": "tiktok", "message": "tiktok", "description": "tiktok"}]

    videos = []
    for it in items:
        object_key = it.get("object_key")
        cover_object_key = it.get("cover_object_key")
        videos.append({
            "name": it.get("display_name") or it.get("filename") or "",
            "size": int(it.get("file_size") or 0),
            "width": 1080,
            "height": 1920,
            "url": (
                tos_clients.generate_signed_media_download_url(object_key)
                if object_key else None
            ),
            "image_url": (
                tos_clients.generate_signed_media_download_url(cover_object_key)
                if cover_object_key else None
            ),
        })

    payload = {
        "mode": "create",
        "product_name": product.get("name") or "",
        "texts": texts,
        "product_links": product_links,
        "videos": videos,
        "source": 0,
        "level": int(product.get("importance") or 3),
        "author": "蔡靖华",
        "push_admin": "蔡靖华",
        "roas": 1.6,
        "platforms": ["tiktok"],
        "selling_point": product.get("selling_points") or "",
        "tags": [],
    }
    return jsonify(payload)
