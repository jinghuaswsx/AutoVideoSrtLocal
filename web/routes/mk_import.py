"""mk-import Blueprint — A 子系统：明空选品自动入素材库 API。"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from appcore import mk_import as mk_import_svc

bp = Blueprint("mk_import", __name__, url_prefix="/mk-import")


def _is_admin() -> bool:
    return getattr(current_user, "role", "") in ("admin", "superadmin") or \
        getattr(current_user, "is_admin", False)


@bp.route("/check", methods=["GET"])
@login_required
def check():
    raw = (request.args.get("filenames") or "").strip()
    if not raw:
        return jsonify({"imported": [], "missing": []})
    filenames = [f.strip() for f in raw.split(",") if f.strip()]
    if len(filenames) > 100:
        return jsonify({"error": "too_many_filenames", "max": 100}), 400

    from appcore.db import query_all
    rows = query_all(
        "SELECT filename FROM media_items "
        "WHERE filename IN (" + ",".join(["%s"] * len(filenames)) + ") "
        "AND deleted_at IS NULL",
        tuple(filenames),
    )
    imported = {r["filename"] for r in rows}
    return jsonify({
        "imported": sorted(imported),
        "missing": sorted(set(filenames) - imported),
    })


@bp.route("/video", methods=["POST"])
@login_required
def import_video():
    if not _is_admin():
        return jsonify({"error": "admin_required"}), 403
    payload = request.get_json(silent=True) or {}
    meta = payload.get("mk_video_metadata") or {}
    translator_id = payload.get("translator_id")
    if not meta or not isinstance(translator_id, int):
        return jsonify({"error": "bad_payload"}), 400
    try:
        result = mk_import_svc.import_mk_video(
            mk_video_metadata=meta,
            translator_id=int(translator_id),
            actor_user_id=int(current_user.id),
        )
        return jsonify(result)
    except mk_import_svc.DuplicateError as e:
        return jsonify({"error": "duplicate_filename", "detail": str(e)}), 422
    except mk_import_svc.DownloadError as e:
        return jsonify({"error": "download_failed", "detail": str(e)}), 502
    except mk_import_svc.StorageError as e:
        return jsonify({"error": "storage_failed", "detail": str(e)}), 500
    except mk_import_svc.DBError as e:
        return jsonify({"error": "db_failed", "detail": str(e)}), 500
