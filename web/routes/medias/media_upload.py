"""本地媒体上传与对象代理路由。

由 ``web.routes.medias`` package 在 PR 2.16 抽出；行为不变。
"""
from __future__ import annotations

from flask import abort, request
from flask_login import current_user, login_required

from appcore import local_media_storage
from web.services.media_object_access import (
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
    access = routes._validate_private_media_object_access(request.args.get("object_key"))
    if access.not_found:
        abort(404)
    object_key = access.object_key
    routes._audit_media_item_access(routes.medias.find_item_by_object_key(object_key))
    return _send_media_object(object_key)


@bp.route("/obj/<path:object_key>")
def public_media_object(object_key: str):
    access = _routes()._validate_public_media_object_access(object_key)
    if access.not_found:
        abort(404)
    return _send_media_object(access.object_key)
