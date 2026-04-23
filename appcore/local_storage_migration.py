from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping

import config
from appcore.db import query
from appcore import tos_clients


_MEDIA_STORE_PREFIX = "media_store"


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _dedupe_sorted(values: list[str] | set[str]) -> list[str]:
    return sorted({value for value in values if value})


def _looks_remote_url(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith("http://") or lowered.startswith("https://")


def _looks_logical_key(value: str) -> bool:
    prefixes = {
        "uploads",
        "artifacts",
        config.TOS_PREFIX.strip("/"),
        config.TOS_BROWSER_UPLOAD_PREFIX.strip("/"),
        config.TOS_FINAL_ARTIFACT_PREFIX.strip("/"),
    }
    path = value.replace("\\", "/").lstrip("/")
    if not path:
        return False
    head = path.split("/", 1)[0]
    return head in {prefix for prefix in prefixes if prefix}


def _looks_local_path(value: str) -> bool:
    if not value or _looks_remote_url(value):
        return False
    path = Path(value)
    if path.is_absolute():
        return True
    normalized = value.replace("\\", "/").lstrip("/")
    if normalized.startswith("output/") or normalized.startswith(f"{_MEDIA_STORE_PREFIX}/"):
        return True
    return bool(path.suffix) and not _looks_logical_key(normalized)


def _collect_local_strings(payload: Any) -> set[str]:
    results: set[str] = set()
    if isinstance(payload, str):
        value = payload.strip()
        if _looks_local_path(value):
            results.add(value)
        return results
    if isinstance(payload, Mapping):
        for value in payload.values():
            results.update(_collect_local_strings(value))
        return results
    if isinstance(payload, (list, tuple, set)):
        for value in payload:
            results.update(_collect_local_strings(value))
    return results


def _resolve_result_targets(state: Mapping[str, Any], artifact_kind: str) -> list[str]:
    candidates: list[str] = []
    top_level_key = f"{artifact_kind}_path"
    top_level_value = _clean_text(state.get(top_level_key))
    if _looks_local_path(top_level_value):
        candidates.append(top_level_value)

    for container_name in ("result", "exports"):
        container = state.get(container_name)
        if not isinstance(container, Mapping):
            continue
        value = container.get(artifact_kind)
        candidates.extend(_collect_local_strings(value))

    return _dedupe_sorted(candidates)


def _build_project_logical_key_targets(state: Mapping[str, Any]) -> dict[str, list[str]]:
    targets: dict[str, list[str]] = {}

    source_tos_key = _clean_text(state.get("source_tos_key"))
    video_path = _clean_text(state.get("video_path"))
    if source_tos_key:
        targets[source_tos_key] = [video_path] if _looks_local_path(video_path) else []

    result_tos_key = _clean_text(state.get("result_tos_key"))
    if result_tos_key:
        targets[result_tos_key] = _resolve_result_targets(state, "result_video")

    tos_uploads = state.get("tos_uploads") or {}
    if isinstance(tos_uploads, Mapping):
        for slot, payload in tos_uploads.items():
            tos_key = ""
            if isinstance(payload, Mapping):
                tos_key = _clean_text(payload.get("tos_key"))
            elif isinstance(payload, str):
                tos_key = payload.strip()
            if not tos_key:
                continue
            artifact_kind = str(slot).split(":", 1)[-1].strip()
            targets[tos_key] = _resolve_result_targets(state, artifact_kind)

    items = state.get("items") or []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, Mapping):
                continue
            for field in ("src_tos_key", "dst_tos_key"):
                logical_key = _clean_text(item.get(field))
                if not logical_key:
                    continue
                targets.setdefault(logical_key, [f"{_MEDIA_STORE_PREFIX}/{logical_key.lstrip('/')}"])

    return {key: value for key, value in sorted(targets.items())}


def collect_project_refs(task_id: str, state: Mapping[str, Any]) -> dict[str, Any]:
    del task_id
    local_paths: set[str] = set()
    for key, value in state.items():
        if not str(key).endswith("_path"):
            continue
        text = _clean_text(value)
        if _looks_local_path(text):
            local_paths.add(text)

    for container_name in ("result", "exports"):
        local_paths.update(_collect_local_strings(state.get(container_name)))

    logical_key_targets = _build_project_logical_key_targets(state)
    logical_keys = list(logical_key_targets)

    return {
        "local_paths": _dedupe_sorted(local_paths),
        "logical_keys": _dedupe_sorted(logical_keys),
        "logical_key_targets": logical_key_targets,
    }


def collect_media_refs(row: Mapping[str, Any]) -> dict[str, Any]:
    logical_keys = _dedupe_sorted([
        _clean_text(row.get("object_key")),
        _clean_text(row.get("cover_object_key")),
        _clean_text(row.get("video_object_key")),
    ])

    relative_paths = _dedupe_sorted([
        _clean_text(row.get("thumbnail_path")),
    ])

    logical_key_targets = {
        key: [f"{_MEDIA_STORE_PREFIX}/{key.lstrip('/')}"]
        for key in logical_keys
    }

    return {
        "logical_keys": logical_keys,
        "relative_paths": relative_paths,
        "logical_key_targets": logical_key_targets,
    }


def _resolve_existing_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path(config.BASE_DIR) / value


def _resolve_media_path(value: str, output_dir: str | Path | None = None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    root = Path(output_dir) if output_dir is not None else Path(config.OUTPUT_DIR)
    if value.replace("\\", "/").startswith(f"{_MEDIA_STORE_PREFIX}/"):
        return root / Path(value)
    return root / Path(value)


def _is_media_logical_key(logical_key: str) -> bool:
    normalized = logical_key.replace("\\", "/").lstrip("/")
    parts = normalized.split("/")
    return len(parts) >= 2 and parts[1] == "medias"


def _download_logical_key(logical_key: str, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _is_media_logical_key(logical_key):
        return tos_clients.download_media_file(logical_key, destination)
    return tos_clients.download_file(logical_key, str(destination))


def _format_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _materialize_logical_key_targets(
    logical_key: str,
    targets: list[str],
    *,
    resolver,
) -> dict[str, Any]:
    resolved_targets = [resolver(target) for target in _dedupe_sorted(targets)]
    if not resolved_targets:
        return {
            "logical_key": logical_key,
            "downloaded": False,
            "targets": [],
        }

    source_path = next((path for path in resolved_targets if path.exists()), None)
    downloaded = False
    if source_path is None:
        source_path = resolved_targets[0]
        _download_logical_key(logical_key, source_path)
        downloaded = True

    for target in resolved_targets:
        if target == source_path or target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)

    return {
        "logical_key": logical_key,
        "downloaded": downloaded,
        "targets": [str(path) for path in resolved_targets],
    }


def _materialize_logical_key_targets_best_effort(
    logical_key: str,
    targets: list[str],
    *,
    resolver,
) -> dict[str, Any]:
    try:
        return _materialize_logical_key_targets(logical_key, targets, resolver=resolver)
    except Exception as exc:
        resolved_targets: list[str] = []
        for target in _dedupe_sorted(targets):
            try:
                resolved_targets.append(str(resolver(target)))
            except Exception as resolver_exc:
                resolved_targets.append(f"{target} ({_format_error(resolver_exc)})")
        return {
            "logical_key": logical_key,
            "downloaded": False,
            "targets": resolved_targets,
            "error": _format_error(exc),
        }


def verify_project_row(task_id: str, state: Mapping[str, Any]) -> dict[str, Any]:
    refs = collect_project_refs(task_id, state)
    missing_local_paths = [
        path for path in refs["local_paths"]
        if not _resolve_existing_path(path).exists()
    ]
    missing_logical_keys = [
        logical_key
        for logical_key, targets in refs["logical_key_targets"].items()
        if not targets or not any(_resolve_existing_path(target).exists() for target in targets)
    ]
    return {
        "task_id": task_id,
        "ok": not missing_local_paths and not missing_logical_keys,
        "local_paths": refs["local_paths"],
        "logical_keys": refs["logical_keys"],
        "missing_local_paths": missing_local_paths,
        "missing_logical_keys": missing_logical_keys,
    }


def verify_media_row(row: Mapping[str, Any], output_dir: str | Path | None = None) -> dict[str, Any]:
    refs = collect_media_refs(row)
    missing_relative_paths = [
        path for path in refs["relative_paths"]
        if not _resolve_media_path(path, output_dir=output_dir).exists()
    ]
    missing_logical_keys = [
        logical_key
        for logical_key, targets in refs["logical_key_targets"].items()
        if not targets or not any(_resolve_media_path(target, output_dir=output_dir).exists() for target in targets)
    ]
    return {
        "media_id": row.get("id"),
        "source": _clean_text(row.get("source")),
        "ok": not missing_relative_paths and not missing_logical_keys,
        "relative_paths": refs["relative_paths"],
        "logical_keys": refs["logical_keys"],
        "missing_relative_paths": missing_relative_paths,
        "missing_logical_keys": missing_logical_keys,
    }


def materialize_project_row(task_id: str, state: Mapping[str, Any]) -> dict[str, Any]:
    refs = collect_project_refs(task_id, state)
    materialized = [
        _materialize_logical_key_targets_best_effort(
            logical_key,
            targets,
            resolver=_resolve_existing_path,
        )
        for logical_key, targets in refs["logical_key_targets"].items()
    ]
    report = verify_project_row(task_id, state)
    report["downloaded_keys"] = [
        item["logical_key"]
        for item in materialized
        if item["downloaded"]
    ]
    report["materialization_errors"] = [
        item
        for item in materialized
        if item.get("error")
    ]
    report["materialized"] = materialized
    report["ok"] = bool(report.get("ok")) and not report["materialization_errors"]
    return report


def materialize_media_row(
    row: Mapping[str, Any],
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    refs = collect_media_refs(row)
    materialized = [
        _materialize_logical_key_targets_best_effort(
            logical_key,
            targets,
            resolver=lambda target: _resolve_media_path(target, output_dir=output_dir),
        )
        for logical_key, targets in refs["logical_key_targets"].items()
    ]
    report = verify_media_row(row, output_dir=output_dir)
    report["downloaded_keys"] = [
        item["logical_key"]
        for item in materialized
        if item["downloaded"]
    ]
    report["materialization_errors"] = [
        item
        for item in materialized
        if item.get("error")
    ]
    report["materialized"] = materialized
    report["ok"] = bool(report.get("ok")) and not report["materialization_errors"]
    return report


def _parse_state_json(raw: object) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    text = _clean_text(raw)
    if not text:
        return {}
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def build_project_report(row: Mapping[str, Any]) -> dict[str, Any]:
    state = _parse_state_json(row.get("state_json"))
    refs = collect_project_refs(_clean_text(row.get("id")), state)
    return {
        "task_id": _clean_text(row.get("id")),
        "status": _clean_text(row.get("status")),
        "local_paths": refs["local_paths"],
        "logical_keys": refs["logical_keys"],
        "logical_key_targets": refs["logical_key_targets"],
    }


def build_media_report(row: Mapping[str, Any]) -> dict[str, Any]:
    refs = collect_media_refs(row)
    return {
        "media_id": row.get("id"),
        "source": _clean_text(row.get("source")),
        "relative_paths": refs["relative_paths"],
        "logical_keys": refs["logical_keys"],
        "logical_key_targets": refs["logical_key_targets"],
    }


def load_project_rows(*, only_active: bool = False, limit: int = 0) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, status, state_json FROM projects "
        "WHERE deleted_at IS NULL"
    )
    args: list[Any] = []
    if only_active:
        sql += " AND status NOT IN ('done', 'error', 'expired')"
    sql += " ORDER BY created_at DESC, id DESC"
    if limit > 0:
        sql += " LIMIT %s"
        args.append(int(limit))
    return query(sql, tuple(args))


def load_media_rows(*, limit: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(query(
        "SELECT id, 'media_item' AS source, object_key, cover_object_key, "
        "thumbnail_path, '' AS video_object_key "
        "FROM media_items WHERE deleted_at IS NULL"
    ))
    rows.extend(query(
        "SELECT product_id AS id, 'product_cover' AS source, object_key, '' AS cover_object_key, "
        "'' AS thumbnail_path, '' AS video_object_key "
        "FROM media_product_covers"
    ))
    rows.extend(query(
        "SELECT id, 'legacy_product_cover' AS source, cover_object_key AS object_key, "
        "'' AS cover_object_key, '' AS thumbnail_path, '' AS video_object_key "
        "FROM media_products WHERE deleted_at IS NULL"
    ))
    rows.extend(query(
        "SELECT id, 'product_detail_image' AS source, object_key, '' AS cover_object_key, "
        "'' AS thumbnail_path, '' AS video_object_key "
        "FROM media_product_detail_images WHERE deleted_at IS NULL"
    ))
    rows.extend(query(
        "SELECT id, 'raw_source' AS source, '' AS object_key, cover_object_key, "
        "'' AS thumbnail_path, video_object_key "
        "FROM media_raw_sources WHERE deleted_at IS NULL"
    ))
    rows = sorted(rows, key=lambda row: (_clean_text(row.get("source")), _clean_text(row.get("id"))))
    if limit > 0:
        return rows[:limit]
    return rows


def verify_all_references(
    *,
    only_active: bool = False,
    project_limit: int = 0,
    media_limit: int = 0,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    project_rows = load_project_rows(only_active=only_active, limit=project_limit)
    media_rows = load_media_rows(limit=media_limit)

    projects = [
        verify_project_row(_clean_text(row.get("id")), _parse_state_json(row.get("state_json")))
        for row in project_rows
    ]
    media = [
        verify_media_row(row, output_dir=output_dir)
        for row in media_rows
    ]

    summary = {
        "projects_checked": len(projects),
        "media_checked": len(media),
        "missing_local_paths": sum(len(item["missing_local_paths"]) for item in projects),
        "missing_logical_keys": (
            sum(len(item["missing_logical_keys"]) for item in projects)
            + sum(len(item["missing_logical_keys"]) for item in media)
        ),
        "missing_relative_paths": sum(len(item["missing_relative_paths"]) for item in media),
    }
    ok = (
        summary["missing_local_paths"] == 0
        and summary["missing_logical_keys"] == 0
        and summary["missing_relative_paths"] == 0
    )
    return {
        "ok": ok,
        "checked": len(projects) + len(media),
        "projects": projects,
        "media": media,
        "summary": summary,
    }
