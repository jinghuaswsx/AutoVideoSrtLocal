"""mk-import Blueprint — A 子系统：明空选品自动入素材库 API。"""
from __future__ import annotations

from flask import Blueprint, request
from flask_login import current_user, login_required

from appcore import mk_import as mk_import_svc
from web.services.mk_import import (
    build_mk_import_admin_required_response,
    build_mk_import_bad_payload_response,
    build_mk_import_check_empty_response,
    build_mk_import_check_response,
    build_mk_import_db_failed_response,
    build_mk_import_download_failed_response,
    build_mk_import_duplicate_response,
    build_mk_import_storage_failed_response,
    build_mk_import_success_response,
    build_mk_import_too_many_filenames_response,
    mk_import_flask_response,
)

bp = Blueprint("mk_import", __name__, url_prefix="/mk-import")


def _is_admin() -> bool:
    return getattr(current_user, "role", "") in ("admin", "superadmin") or \
        getattr(current_user, "is_admin", False)


@bp.route("/check", methods=["GET"])
@login_required
def check():
    raw = (request.args.get("filenames") or "").strip()
    if not raw:
        return mk_import_flask_response(build_mk_import_check_empty_response())
    filenames = [f.strip() for f in raw.split(",") if f.strip()]
    if len(filenames) > 100:
        return mk_import_flask_response(
            build_mk_import_too_many_filenames_response(max_filenames=100)
        )

    from appcore.db import query_all
    rows = query_all(
        "SELECT filename FROM media_items "
        "WHERE filename IN (" + ",".join(["%s"] * len(filenames)) + ") "
        "AND deleted_at IS NULL",
        tuple(filenames),
    )
    imported = {r["filename"] for r in rows}
    return mk_import_flask_response(
        build_mk_import_check_response(filenames=filenames, imported=imported)
    )


@bp.route("/video", methods=["POST"])
@login_required
def import_video():
    if not _is_admin():
        return mk_import_flask_response(build_mk_import_admin_required_response())
    payload = request.get_json(silent=True) or {}
    meta = payload.get("mk_video_metadata") or {}
    translator_id = payload.get("translator_id")
    if not meta or not isinstance(translator_id, int):
        return mk_import_flask_response(build_mk_import_bad_payload_response())
    try:
        result = mk_import_svc.import_mk_video(
            mk_video_metadata=meta,
            translator_id=int(translator_id),
            actor_user_id=int(current_user.id),
        )
        return mk_import_flask_response(build_mk_import_success_response(result))
    except mk_import_svc.DuplicateError as e:
        return mk_import_flask_response(build_mk_import_duplicate_response(e))
    except mk_import_svc.DownloadError as e:
        return mk_import_flask_response(build_mk_import_download_failed_response(e))
    except mk_import_svc.StorageError as e:
        return mk_import_flask_response(build_mk_import_storage_failed_response(e))
    except mk_import_svc.DBError as e:
        return mk_import_flask_response(build_mk_import_db_failed_response(e))
