"""本地媒体上传与对象代理路由。

由 ``web.routes.medias`` package 在 PR 2.16 抽出；行为不变。
"""
from __future__ import annotations

from flask import abort, request
from flask_login import current_user, login_required

from appcore import local_media_storage

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


@bp.route("/api/local-media-upload/<upload_id>", methods=["PUT"])
@login_required
def api_local_media_upload(upload_id: str):
    with _local_upload_guard:
        reservation = _local_upload_reservations.get(upload_id)
    if not reservation or int(reservation.get("user_id") or 0) != int(current_user.id):
        abort(404)
    local_media_storage.write_stream(reservation["object_key"], request.stream)
    return ("", 204)


@bp.route("/object", methods=["GET"])
@login_required
def media_object_proxy():
    object_key = (request.args.get("object_key") or "").strip()
    if not object_key:
        abort(404)
    return _send_media_object(object_key)


@bp.route("/obj/<path:object_key>")
def public_media_object(object_key: str):
    key = (object_key or "").strip()
    # 最低限度的防护：禁止 path traversal 和空值
    if not key or ".." in key.split("/") or key.startswith("/"):
        abort(404)
    # 项目内合法 object_key 命名空间（local_media_storage 已做 traversal 校验）：
    #   <uid>/medias/<pid>/<filename>              -- 原始素材 / 封面 / raw_sources
    #   artifacts/<variant>/<uid>/<tid>/<file>    -- 产物（image_translate 译图/译封面等）
    #   uploads/<variant>/<uid>/<tid>/<file>      -- 上传源文件
    parts = key.split("/")
    if len(parts) < 3:
        abort(404)
    if not (parts[1] == "medias" or parts[0] in ("artifacts", "uploads")):
        abort(404)
    return _send_media_object(key)
