"""Service responses for mk import routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import jsonify


@dataclass(frozen=True)
class MkImportResponse:
    payload: dict[str, Any]
    status_code: int


def mk_import_flask_response(result: MkImportResponse):
    return jsonify(result.payload), result.status_code


def build_mk_import_check_empty_response() -> MkImportResponse:
    return MkImportResponse({"imported": [], "missing": []}, 200)


def build_mk_import_too_many_filenames_response(*, max_filenames: int) -> MkImportResponse:
    return MkImportResponse({"error": "too_many_filenames", "max": max_filenames}, 400)


def build_mk_import_check_response(
    *,
    filenames: list[str],
    imported: set[str],
) -> MkImportResponse:
    return MkImportResponse(
        {
            "imported": sorted(imported),
            "missing": sorted(set(filenames) - imported),
        },
        200,
    )


def build_mk_import_admin_required_response() -> MkImportResponse:
    return MkImportResponse({"error": "admin_required"}, 403)


def build_mk_import_bad_payload_response() -> MkImportResponse:
    return MkImportResponse({"error": "bad_payload"}, 400)


def build_mk_import_success_response(result: dict) -> MkImportResponse:
    return MkImportResponse(result, 200)


def build_mk_import_duplicate_response(exc: Exception) -> MkImportResponse:
    return MkImportResponse({"error": "duplicate_filename", "detail": str(exc)}, 422)


def build_mk_import_download_failed_response(exc: Exception) -> MkImportResponse:
    return MkImportResponse({"error": "download_failed", "detail": str(exc)}, 502)


def build_mk_import_storage_failed_response(exc: Exception) -> MkImportResponse:
    return MkImportResponse({"error": "storage_failed", "detail": str(exc)}, 500)


def build_mk_import_db_failed_response(exc: Exception) -> MkImportResponse:
    return MkImportResponse({"error": "db_failed", "detail": str(exc)}, 500)
