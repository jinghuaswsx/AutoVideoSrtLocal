"""mk-import Blueprint — A 子系统：明空选品自动入素材库 API。"""
from __future__ import annotations

from flask import Blueprint, current_app, request
from flask_login import current_user, login_required

from appcore import mk_import as mk_import_svc
from appcore.users import ensure_translation_work_user
from web.services.mk_import import (
    build_mk_import_admin_required_response,
    build_mk_import_bad_payload_response,
    build_mk_import_check_empty_response,
    build_mk_import_check_response,
    build_mk_import_db_failed_response,
    build_mk_import_download_failed_response,
    build_mk_import_duplicate_response,
    build_mk_import_invalid_translator_response,
    build_mk_import_storage_failed_response,
    build_mk_import_success_response,
    build_mk_import_too_many_filenames_response,
    mk_import_flask_response,
)
from web.services.material_evaluation_trigger import (
    trigger_material_evaluation,
)

bp = Blueprint("mk_import", __name__, url_prefix="/mk-import")


def _is_admin() -> bool:
    return getattr(current_user, "role", "") in ("admin", "superadmin") or \
        getattr(current_user, "is_admin", False)


def _trigger_material_evaluation(
    *,
    product_id: int,
    media_item_id: int | None,
    force: bool,
    manual: bool,
    product_url_override: str | None = None,
) -> bool:
    return trigger_material_evaluation(
        product_id=product_id,
        media_item_id=media_item_id,
        force=force,
        manual=manual,
        product_url_override=product_url_override,
        user_id=int(getattr(current_user, "id", 0) or 0) or None,
        entrypoint="mk_import.video",
    )


@bp.route("/check", methods=["GET", "POST"])
@login_required
def check():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        raw_filenames = payload.get("filenames") or []
        filenames = [str(f).strip() for f in raw_filenames if str(f).strip()] if isinstance(raw_filenames, list) else []
    else:
        raw = (request.args.get("filenames") or "").strip()
        if not raw:
            return mk_import_flask_response(build_mk_import_check_empty_response())
        filenames = [f.strip() for f in raw.split(",") if f.strip()]
    if not filenames:
        return mk_import_flask_response(build_mk_import_check_empty_response())
    if len(filenames) > 100:
        return mk_import_flask_response(
            build_mk_import_too_many_filenames_response(max_filenames=100)
        )

    imported = mk_import_svc.list_imported_filenames(filenames)
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
    product_owner_id = payload.get("product_owner_id", payload.get("translator_id"))
    if not meta or (product_owner_id is not None and not isinstance(product_owner_id, int)):
        return mk_import_flask_response(build_mk_import_bad_payload_response())
    if product_owner_id is not None:
        try:
            ensure_translation_work_user(product_owner_id)
        except ValueError as e:
            return mk_import_flask_response(build_mk_import_invalid_translator_response(e))
    try:
        result = mk_import_svc.import_mk_video(
            mk_video_metadata=meta,
            translator_id=int(product_owner_id) if product_owner_id is not None else None,
            actor_user_id=int(current_user.id),
        )
        product_id = int(result.get("media_product_id") or 0)
        item_id = int(result.get("media_item_id") or 0)
        if product_id and item_id:
            try:
                _trigger_material_evaluation(
                    product_id=product_id,
                    media_item_id=item_id,
                    force=True,
                    manual=False,
                    product_url_override=str(meta.get("product_link") or "").strip() or None,
                )
            except Exception:
                current_app.logger.exception(
                    "trigger material evaluation after mk import failed product_id=%s item_id=%s",
                    product_id,
                    item_id,
                )
        return mk_import_flask_response(build_mk_import_success_response(result))
    except ValueError as e:
        return mk_import_flask_response(build_mk_import_bad_payload_response(str(e)))
    except mk_import_svc.DuplicateError as e:
        return mk_import_flask_response(build_mk_import_duplicate_response(e))
    except mk_import_svc.DownloadError as e:
        return mk_import_flask_response(build_mk_import_download_failed_response(e))
    except mk_import_svc.StorageError as e:
        return mk_import_flask_response(build_mk_import_storage_failed_response(e))
    except mk_import_svc.DBError as e:
        return mk_import_flask_response(build_mk_import_db_failed_response(e))
