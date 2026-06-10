"""本地媒体上传与对象代理路由。

由 ``web.routes.medias`` package 在 PR 2.16 抽出；行为不变。
"""
from __future__ import annotations

from flask import abort, request, Response
from flask_login import current_user, login_required
import requests
from urllib.parse import unquote

from appcore import local_media_storage
from web.services.media_object_access import (
    build_private_media_object_proxy_response as _build_private_media_object_proxy_response_impl,
    build_public_media_object_proxy_response as _build_public_media_object_proxy_response_impl,
    media_object_proxy_flask_response as _media_object_proxy_flask_response_impl,
    validate_private_media_object_access as _validate_private_media_object_access_impl,
    validate_public_media_object_access as _validate_public_media_object_access_impl,
)
from web.services.media_local_upload import complete_local_media_upload

from . import bp
from ._helpers import (
    _local_upload_guard,
    _local_upload_reservations,
)


def _routes():
    from web.routes import medias as routes
    return routes


def _send_media_object(object_key):
    return _routes()._send_media_object(object_key)


def _write_local_media_stream(object_key, stream):
    return local_media_storage.write_stream(object_key, stream)


def _validate_private_media_object_access(object_key):
    return _validate_private_media_object_access_impl(
        object_key,
        safe_local_path_for_fn=local_media_storage.safe_local_path_for,
    )


def _validate_public_media_object_access(object_key):
    return _validate_public_media_object_access_impl(object_key)


def _build_private_media_object_proxy_response(object_key):
    routes = _routes()
    return _build_private_media_object_proxy_response_impl(
        object_key,
        validate_access_fn=routes._validate_private_media_object_access,
        find_item_by_object_key_fn=routes.medias.find_item_by_object_key,
    )


def _build_public_media_object_proxy_response(object_key):
    return _build_public_media_object_proxy_response_impl(
        object_key,
        validate_access_fn=_routes()._validate_public_media_object_access,
    )


def _media_object_proxy_flask_response(result):
    return _media_object_proxy_flask_response_impl(
        result,
        send_media_object_fn=_send_media_object,
    )


@bp.route("/api/local-media-upload/<upload_id>", methods=["PUT"])
@login_required
def api_local_media_upload(upload_id: str):
    outcome = complete_local_media_upload(
        upload_id,
        user_id=current_user.id,
        stream=request.stream,
        reservations=_local_upload_reservations,
        reservation_guard=_local_upload_guard,
        write_stream_fn=_write_local_media_stream,
    )
    if outcome.not_found:
        abort(404)
    return ("", outcome.status_code)


@bp.route("/object", methods=["GET"])
@login_required
def media_object_proxy():
    routes = _routes()
    result = routes._build_private_media_object_proxy_response(request.args.get("object_key"))
    if result.not_found:
        abort(404)
    routes._audit_media_item_access(result.audit_item)
    return _media_object_proxy_flask_response(result)


@bp.route("/obj/<path:object_key>")
def public_media_object(object_key: str):
    result = _routes()._build_public_media_object_proxy_response(object_key)
    if result.not_found:
        abort(404)
    return _media_object_proxy_flask_response(result)


@bp.route("/external-image", methods=["GET"])
@login_required
def external_image_proxy():
    """Proxy external images (like Shopify CDN) to avoid connection failure in local client."""
    url = request.args.get("url") or ""
    url = unquote(url).strip()
    if not url:
        abort(400, "Missing url parameter")
    if not url.startswith(("http://", "https://")):
        abort(400, "Invalid url protocol")
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "image/*,*/*;q=0.8"
            },
            timeout=15,
            stream=True
        )
        if resp.status_code >= 400:
            abort(resp.status_code)
        content_type = resp.headers.get("content-type") or "image/jpeg"
        def generate():
            for chunk in resp.iter_content(chunk_size=1024 * 64):
                yield chunk
        proxied = Response(generate(), status=resp.status_code, content_type=content_type)
        proxied.headers["Cache-Control"] = "public, max-age=86400"  # Cache for 1 day
        return proxied
    except Exception as exc:
        abort(502, f"Failed to proxy image: {exc}")

