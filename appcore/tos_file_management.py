"""TOS 文件管理服务。

核心功能
--------
* 收集受保护业务文件清单
* 按模块分类统计
* 扫描并持久化文件映射状态
* 触发 TOS 通道同步/干运行
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from appcore.db import execute, query, query_one
from appcore import tos_backup_references
from appcore import tos_backup_storage
from appcore import tos_channel_migration


# -----------------------------------------------------------------------------
# 模块分类映射
# -----------------------------------------------------------------------------
SOURCE_MODULES = {
    "project_video": ("projects", "项目源视频", "source_video"),
    "media_item": ("media_items", "素材库视频", "video"),
    "media_item_cover": ("media_items", "素材库视频", "cover"),
    "product_cover": ("product_images", "产品封面/详情图", "cover"),
    "legacy_product_cover": ("product_images", "产品封面/详情图", "cover"),
    "product_detail_image": ("product_images", "产品封面/详情图", "detail_image"),
    "raw_source_video": ("raw_sources", "原始素材", "video"),
    "raw_source_cover": ("raw_sources", "原始素材", "cover"),
    "raw_source_translation_cover": ("raw_sources", "原始素材翻译封面", "cover"),
}

SOURCE_PRIORITY = [
    "project_video",
    "media_item",
    "media_item_cover",
    "product_cover",
    "legacy_product_cover",
    "product_detail_image",
    "raw_source_video",
    "raw_source_cover",
    "raw_source_translation_cover",
]


# -----------------------------------------------------------------------------
# 数据类
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class TosFileInventoryRow:
    module_code: str
    module_name: str
    file_type: str
    source_labels: tuple[str, ...]
    source_object_keys: tuple[str, ...]
    local_path: str
    local_path_hash: str
    local_exists: bool
    local_size_bytes: int
    backup_object_key: str
    target_channel_code: str
    target_bucket: str
    target_object_key: str
    target_exists: bool
    target_size_bytes: int
    sync_status: str
    last_error: str = ""


# -----------------------------------------------------------------------------
# 内部工具
# -----------------------------------------------------------------------------
def _local_path_hash(local_path: str) -> str:
    normalized = local_path.replace("\\", "/").strip("/")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _classify_ref(ref: tos_backup_references.ProtectedFileRef) -> tuple[str, str, str]:
    for source in SOURCE_PRIORITY:
        if source in ref.sources:
            return SOURCE_MODULES[source]
    # 默认兜底
    return ("unknown", "未知文件", "unknown")


def _head_target_object(client, bucket: str, object_key: str) -> dict[str, Any]:
    try:
        result = client.head_object(bucket, object_key)
        size = getattr(result, "content_length", 0) or 0
        return {"exists": True, "size_bytes": int(size)}
    except Exception:
        return {"exists": False, "size_bytes": 0}


# -----------------------------------------------------------------------------
# 清单构建
# -----------------------------------------------------------------------------
def build_inventory_rows(target_channel_code: str) -> list[TosFileInventoryRow]:
    """构建清单行，但不持久化。"""
    target = tos_channel_migration.load_tos_channel_config(target_channel_code)
    client = tos_channel_migration._build_target_client(target)
    refs = tos_backup_references.collect_protected_file_refs()
    rows = []

    for ref in refs:
        module_code, module_name, file_type = _classify_ref(ref)
        local_path = Path(ref.local_path)
        local_exists = local_path.is_file()
        local_size_bytes = local_path.stat().st_size if local_exists else 0
        backup_object_key = tos_backup_storage.backup_object_key_for_local_path(local_path)
        target_object_key = backup_object_key

        head_result = _head_target_object(client, target.bucket, target_object_key)
        target_exists = head_result["exists"]
        target_size_bytes = head_result["size_bytes"]

        if not local_exists:
            sync_status = "missing_local"
        elif not target_exists:
            sync_status = "missing_target"
        else:
            sync_status = "synced"

        row = TosFileInventoryRow(
            module_code=module_code,
            module_name=module_name,
            file_type=file_type,
            source_labels=ref.sources,
            source_object_keys=ref.object_keys,
            local_path=str(local_path),
            local_path_hash=_local_path_hash(str(local_path)),
            local_exists=local_exists,
            local_size_bytes=local_size_bytes,
            backup_object_key=backup_object_key,
            target_channel_code=target_channel_code,
            target_bucket=target.bucket,
            target_object_key=target_object_key,
            target_exists=target_exists,
            target_size_bytes=target_size_bytes,
            sync_status=sync_status,
            last_error="",
        )
        rows.append(row)

    return rows


def summarize_inventory(rows: list[TosFileInventoryRow]) -> dict[str, Any]:
    """汇总清单统计。"""
    total_files = len(rows)
    total_bytes = sum(r.local_size_bytes for r in rows)
    local_missing_count = sum(1 for r in rows if not r.local_exists)
    target_missing_count = sum(1 for r in rows if r.local_exists and not r.target_exists)
    failed_count = sum(1 for r in rows if r.sync_status == "failed")

    modules: dict[str, dict[str, Any]] = {}
    for row in rows:
        mod = modules.setdefault(row.module_code, {
            "module_code": row.module_code,
            "module_name": row.module_name,
            "file_count": 0,
            "total_bytes": 0,
            "target_existing_count": 0,
            "target_existing_bytes": 0,
            "missing_count": 0,
            "failed_count": 0,
        })
        mod["file_count"] += 1
        mod["total_bytes"] += row.local_size_bytes
        if row.target_exists:
            mod["target_existing_count"] += 1
            mod["target_existing_bytes"] += row.target_size_bytes
        if row.local_exists and not row.target_exists:
            mod["missing_count"] += 1
        if row.sync_status == "failed":
            mod["failed_count"] += 1

    module_list = sorted(modules.values(), key=lambda m: m["module_code"])

    return {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "local_missing_count": local_missing_count,
        "target_missing_count": target_missing_count,
        "failed_count": failed_count,
        "modules": module_list,
    }


# -----------------------------------------------------------------------------
# 扫描持久化
# -----------------------------------------------------------------------------
def upsert_mapping(row: TosFileInventoryRow, scan_run_id: int | None = None) -> None:
    """插入或更新单条文件映射。"""
    execute(
        "INSERT INTO tos_file_mappings "
        "  (scan_run_id, module_code, module_name, file_type, source_labels_json, "
        "   source_object_keys_json, local_path, local_path_hash, local_exists, "
        "   local_size_bytes, backup_object_key, target_channel_code, target_bucket, "
        "   target_object_key, target_exists, target_size_bytes, sync_status, last_error) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  scan_run_id = VALUES(scan_run_id), "
        "  module_code = VALUES(module_code), "
        "  module_name = VALUES(module_name), "
        "  file_type = VALUES(file_type), "
        "  source_labels_json = VALUES(source_labels_json), "
        "  source_object_keys_json = VALUES(source_object_keys_json), "
        "  local_exists = VALUES(local_exists), "
        "  local_size_bytes = VALUES(local_size_bytes), "
        "  target_bucket = VALUES(target_bucket), "
        "  target_exists = VALUES(target_exists), "
        "  target_size_bytes = VALUES(target_size_bytes), "
        "  sync_status = VALUES(sync_status), "
        "  last_error = VALUES(last_error), "
        "  last_seen_at = CURRENT_TIMESTAMP",
        (
            scan_run_id,
            row.module_code,
            row.module_name,
            row.file_type,
            json.dumps(row.source_labels, ensure_ascii=False),
            json.dumps(row.source_object_keys, ensure_ascii=False),
            row.local_path,
            row.local_path_hash,
            int(row.local_exists),
            row.local_size_bytes,
            row.backup_object_key,
            row.target_channel_code,
            row.target_bucket,
            row.target_object_key,
            int(row.target_exists),
            row.target_size_bytes,
            row.sync_status,
            row.last_error,
        ),
    )


def run_inventory_scan(target_channel_code: str, triggered_by: int | None = None) -> dict[str, Any]:
    """运行完整的清单扫描并持久化。"""
    # 插入扫描运行记录
    scan_run_id = execute(
        "INSERT INTO tos_file_scan_runs "
        "  (target_channel_code, status, triggered_by) "
        "VALUES (%s, %s, %s)",
        (target_channel_code, "running", triggered_by),
    )

    try:
        rows = build_inventory_rows(target_channel_code)
        summary = summarize_inventory(rows)

        for row in rows:
            upsert_mapping(row, scan_run_id=scan_run_id)

        # 更新扫描运行记录
        execute(
            "UPDATE tos_file_scan_runs "
            "SET status = %s, target_bucket = %s, total_files = %s, total_bytes = %s, "
            "    local_missing_count = %s, target_missing_count = %s, failed_count = %s, "
            "    module_summary_json = %s, finished_at = CURRENT_TIMESTAMP "
            "WHERE id = %s",
            (
                "success",
                rows[0].target_bucket if rows else "",
                summary["total_files"],
                summary["total_bytes"],
                summary["local_missing_count"],
                summary["target_missing_count"],
                summary["failed_count"],
                json.dumps(summary, ensure_ascii=False),
                scan_run_id,
            ),
        )

        return {"scan_run_id": scan_run_id, "summary": summary}
    except Exception as e:
        execute(
            "UPDATE tos_file_scan_runs "
            "SET status = %s, error_message = %s, finished_at = CURRENT_TIMESTAMP "
            "WHERE id = %s",
            ("failed", str(e), scan_run_id),
        )
        raise


# -----------------------------------------------------------------------------
# 最新摘要查询
# -----------------------------------------------------------------------------
def latest_scan_summary(target_channel_code: str) -> dict[str, Any] | None:
    """获取最新扫描的摘要。"""
    row = query_one(
        "SELECT id, status, target_bucket, total_files, total_bytes, "
        "       local_missing_count, target_missing_count, failed_count, "
        "       module_summary_json, started_at, finished_at "
        "FROM tos_file_scan_runs "
        "WHERE target_channel_code = %s "
        "ORDER BY started_at DESC LIMIT 1",
        (target_channel_code,),
    )
    if not row:
        return None

    module_summary = {}
    raw = row.get("module_summary_json")
    if raw:
        try:
            module_summary = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            pass

    return {
        "scan_run_id": row["id"],
        "status": row["status"],
        "target_bucket": row["target_bucket"],
        "total_files": row["total_files"],
        "total_bytes": row["total_bytes"],
        "local_missing_count": row["local_missing_count"],
        "target_missing_count": row["target_missing_count"],
        "failed_count": row["failed_count"],
        "modules": module_summary.get("modules", []),
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


# -----------------------------------------------------------------------------
# 文件映射列表查询
# -----------------------------------------------------------------------------
@dataclass(frozen=True)
class TosFileFilters:
    target_channel_code: str = "tos_wj"
    module_code: str | None = None
    sync_status: str | None = None
    q: str | None = None
    page: int = 1
    page_size: int = 50


def list_mappings(filters: TosFileFilters) -> dict[str, Any]:
    """分页查询文件映射列表。"""
    where_parts = ["target_channel_code = %s"]
    params: list[Any] = [filters.target_channel_code]

    if filters.module_code:
        where_parts.append("module_code = %s")
        params.append(filters.module_code)

    if filters.sync_status:
        where_parts.append("sync_status = %s")
        params.append(filters.sync_status)

    if filters.q:
        where_parts.append("local_path LIKE %s")
        params.append(f"%{filters.q}%")

    where_clause = " AND ".join(where_parts)

    offset = (filters.page - 1) * filters.page_size

    rows = query(
        "SELECT id, module_code, module_name, file_type, source_labels_json, "
        "       local_path, local_exists, local_size_bytes, target_object_key, "
        "       target_exists, target_size_bytes, sync_status, last_error, "
        "       last_seen_at, last_synced_at "
        "FROM tos_file_mappings "
        f"WHERE {where_clause} "
        "ORDER BY module_code, local_path "
        "LIMIT %s OFFSET %s",
        params + [filters.page_size, offset],
    )

    count_row = query_one(
        f"SELECT COUNT(*) AS total FROM tos_file_mappings WHERE {where_clause}",
        params,
    )
    total = int(count_row["total"]) if count_row else 0

    items = []
    for row in rows:
        source_labels = []
        try:
            raw = row.get("source_labels_json")
            if raw:
                source_labels = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, json.JSONDecodeError):
            pass
        items.append({
            "id": row["id"],
            "module_code": row["module_code"],
            "module_name": row["module_name"],
            "file_type": row["file_type"],
            "source_labels": source_labels,
            "local_path": row["local_path"],
            "local_exists": bool(int(row.get("local_exists", 0))),
            "local_size_bytes": int(row.get("local_size_bytes", 0)),
            "target_object_key": row["target_object_key"],
            "target_exists": bool(int(row.get("target_exists", 0))),
            "target_size_bytes": int(row.get("target_size_bytes", 0)),
            "sync_status": row["sync_status"],
            "last_error": row.get("last_error") or "",
            "last_seen_at": row.get("last_seen_at"),
            "last_synced_at": row.get("last_synced_at"),
        })

    return {
        "items": items,
        "total": total,
        "page": filters.page,
        "page_size": filters.page_size,
    }


# -----------------------------------------------------------------------------
# 同步操作
# -----------------------------------------------------------------------------
def run_channel_sync(
    target_channel_code: str,
    dry_run: bool = True,
    module_code: str | None = None,
    triggered_by: int | None = None,
) -> dict[str, Any]:
    """运行通道同步。"""
    if module_code:
        # 模块级同步暂未实现
        raise NotImplementedError("module sync not implemented")

    sync_run_id = execute(
        "INSERT INTO tos_file_sync_runs "
        "  (target_channel_code, dry_run, status, triggered_by) "
        "VALUES (%s, %s, %s, %s)",
        (target_channel_code, int(dry_run), "running", triggered_by),
    )

    try:
        result = tos_channel_migration.run_channel_backup(
            target_code=target_channel_code,
            files=True,
            db_dump=False,
            dry_run=dry_run,
        )

        files_result = result.get("files", {})
        files_checked = files_result.get("files_checked", 0)
        uploaded_count = int(files_result.get("actions", {}).get("uploaded", 0) if dry_run else 0)
        skipped_existing_count = int(files_result.get("actions", {}).get("skipped_existing", 0))
        failed_count = int(files_result.get("failed", 0))
        bytes_uploaded = 0

        target_bucket = files_result.get("target_bucket", "")

        execute(
            "UPDATE tos_file_sync_runs "
            "SET status = %s, target_bucket = %s, files_checked = %s, "
            "    uploaded_count = %s, skipped_existing_count = %s, "
            "    failed_count = %s, bytes_uploaded = %s, summary_json = %s, "
            "    finished_at = CURRENT_TIMESTAMP "
            "WHERE id = %s",
            (
                "success" if failed_count == 0 else "failed",
                target_bucket,
                files_checked,
                uploaded_count,
                skipped_existing_count,
                failed_count,
                bytes_uploaded,
                json.dumps(result, ensure_ascii=False),
                sync_run_id,
            ),
        )

        return {"sync_run_id": sync_run_id, "result": result}
    except Exception as e:
        execute(
            "UPDATE tos_file_sync_runs "
            "SET status = %s, error_message = %s, finished_at = CURRENT_TIMESTAMP "
            "WHERE id = %s",
            ("failed", str(e), sync_run_id),
        )
        raise


# -----------------------------------------------------------------------------
# 定时任务入口
# -----------------------------------------------------------------------------
def run_scheduled_inventory_scan(*, scheduled_for=None) -> dict[str, Any]:
    """定时任务入口：扫描默认通道（tos_wj）的清单。"""
    return run_inventory_scan("tos_wj")


def register(scheduler) -> None:
    """向调度器注册任务。"""
    from appcore import scheduled_tasks
    scheduled_tasks.add_controlled_job(
        scheduler,
        "tos_file_inventory_scan",
        run_scheduled_inventory_scan,
        "cron",
        hour=5,
        minute=0,
        id="tos_file_inventory_scan",
        replace_existing=True,
        max_instances=1,
    )
