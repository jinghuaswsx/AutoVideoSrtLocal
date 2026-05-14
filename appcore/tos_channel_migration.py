from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import tempfile
from pathlib import Path
from typing import Any

import config
from appcore import infra_credentials, tos_backup_references, tos_backup_restore, tos_backup_storage


@dataclass(frozen=True)
class TosChannelConfig:
    code: str
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    region: str
    bucket: str
    public_endpoint: str
    private_endpoint: str


def _cfg_text(data: dict[str, Any], key: str, default: str = "") -> str:
    value = data.get(key)
    text = str(value or "").strip()
    return text or default


def load_tos_channel_config(code: str = "tos_wj") -> TosChannelConfig:
    selected = (code or "").strip()
    if selected not in infra_credentials.TOS_CHANNEL_CODES:
        raise ValueError(f"unknown TOS channel: {code}")

    cred = infra_credentials.get_config(selected)
    if cred is None or not cred.enabled:
        raise RuntimeError(f"TOS channel {selected} is not configured")

    data = dict(cred.config)
    endpoint = _cfg_text(data, "endpoint", config.TOS_ENDPOINT or "tos-cn-shanghai.volces.com")
    public_endpoint = _cfg_text(data, "public_endpoint", endpoint)
    private_endpoint = _cfg_text(data, "private_endpoint", config.TOS_PRIVATE_ENDPOINT or public_endpoint)
    target = TosChannelConfig(
        code=selected,
        access_key=_cfg_text(data, "access_key"),
        secret_key=_cfg_text(data, "secret_key"),
        region=_cfg_text(data, "region", config.TOS_REGION or "cn-shanghai"),
        bucket=_cfg_text(data, "bucket"),
        public_endpoint=public_endpoint,
        private_endpoint=private_endpoint,
    )
    missing = [
        name
        for name in ("access_key", "secret_key", "bucket", "region", "public_endpoint")
        if not getattr(target, name)
    ]
    if missing:
        raise RuntimeError(f"TOS channel {selected} missing required fields: {', '.join(missing)}")
    return target


def _build_target_client(target: TosChannelConfig):
    tos_backup_storage.ensure_tos_direct_no_proxy()
    import tos

    return tos.TosClientV2(
        ak=target.access_key,
        sk=target.secret_key,
        endpoint=target.public_endpoint,
        region=target.region,
        max_retry_count=3,
        connection_time=10,
        socket_timeout=30,
    )


def _target_object_exists(client, target: TosChannelConfig, object_key: str) -> bool:
    try:
        client.head_object(target.bucket, object_key)
    except Exception:
        return False
    return True


def _target_object_keys(client, target: TosChannelConfig, prefix: str) -> list[str]:
    marker = ""
    keys: list[str] = []
    while True:
        result = client.list_objects(target.bucket, prefix=prefix, marker=marker)
        contents = getattr(result, "contents", None) or []
        for item in contents:
            key = getattr(item, "key", None) or (item.get("key") if isinstance(item, dict) else "")
            if key:
                keys.append(str(key))
        truncated = bool(getattr(result, "is_truncated", False))
        marker = str(getattr(result, "next_marker", "") or "")
        if not truncated or not marker:
            break
    return keys


def copy_protected_files_to_channel(
    *,
    target_code: str = "tos_wj",
    dry_run: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    target = load_tos_channel_config(target_code)
    client = _build_target_client(target)
    refs = tos_backup_references.collect_protected_file_refs()
    actions: Counter[str] = Counter()
    errors: list[dict[str, str]] = []

    for ref in refs:
        object_key = tos_backup_storage.backup_object_key_for_local_path(ref.local_path)
        try:
            if not dry_run:
                result = tos_backup_storage.ensure_local_copy_for_local_path(ref.local_path)
                if result is not None and result.action == "failed":
                    actions["failed"] += 1
                    errors.append({
                        "local_path": ref.local_path,
                        "object_key": object_key,
                        "error": result.error,
                    })
                    continue
            local_path = Path(ref.local_path)
            if not local_path.is_file():
                actions["failed"] += 1
                errors.append({
                    "local_path": ref.local_path,
                    "object_key": object_key,
                    "error": "local file missing",
                })
                continue
            if _target_object_exists(client, target, object_key) and not overwrite:
                actions["skipped_existing"] += 1
                continue
            if dry_run:
                actions["would_upload"] += 1
                continue
            client.put_object_from_file(target.bucket, object_key, str(local_path))
            actions["uploaded"] += 1
        except Exception as exc:
            actions["failed"] += 1
            errors.append({
                "local_path": ref.local_path,
                "object_key": object_key,
                "error": str(exc),
            })

    return {
        "target_code": target.code,
        "target_bucket": target.bucket,
        "files_checked": len(refs),
        "actions": dict(actions),
        "failed": int(actions.get("failed", 0)),
        "errors": errors[:20],
    }


def build_mysqldump_target_key(source_key: str, *, target_prefix: str = "mysqldump") -> str:
    prefix = (target_prefix or "mysqldump").strip("/") or "mysqldump"
    source_prefix = tos_backup_storage.db_backup_prefix().rstrip("/") + "/"
    if source_key.startswith(source_prefix):
        rest = source_key[len(source_prefix):].lstrip("/")
    else:
        rest = Path(source_key).name
    env = (config.TOS_BACKUP_ENV or "test").strip("/") or "test"
    return f"{prefix}/{env}/{rest}"


def mysqldump_target_prefix(*, target_prefix: str = "mysqldump") -> str:
    prefix = (target_prefix or "mysqldump").strip("/") or "mysqldump"
    env = (config.TOS_BACKUP_ENV or "test").strip("/") or "test"
    return f"{prefix}/{env}/"


def cleanup_channel_mysql_dumps(
    *,
    target_code: str = "tos_wj",
    target_prefix: str = "mysqldump",
    keep_count: int = 7,
    dry_run: bool = False,
) -> dict[str, Any]:
    target = load_tos_channel_config(target_code)
    client = _build_target_client(target)
    prefix = mysqldump_target_prefix(target_prefix=target_prefix)
    keys = sorted(_target_object_keys(client, target, prefix))
    limit = max(int(keep_count), 0)
    expired = keys[:-limit] if limit else keys
    deleted: list[str] = []
    if not dry_run:
        for key in expired:
            client.delete_object(target.bucket, key)
            deleted.append(key)

    summary: dict[str, Any] = {
        "target_code": target.code,
        "target_bucket": target.bucket,
        "prefix": prefix,
        "dumps_scanned": len(keys),
        "dumps_deleted": len(deleted),
    }
    if dry_run:
        summary["would_delete"] = expired
    else:
        summary["deleted"] = deleted
    return summary


def _download_source_object_to_temp(
    source_key: str,
    *,
    output_dir: str | Path | None = None,
) -> Path:
    base = Path(output_dir) if output_dir is not None else Path(tempfile.gettempdir()) / "autovideosrt-tos-channel-backup"
    base.mkdir(parents=True, exist_ok=True)
    local_path = base / Path(source_key).name
    tos_backup_storage.download_to_file(source_key, local_path)
    return local_path


def copy_latest_mysql_dump_to_channel(
    *,
    target_code: str = "tos_wj",
    target_prefix: str = "mysqldump",
    output_dir: str | Path | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    source_key = tos_backup_restore.latest_db_dump_key()
    if not source_key:
        raise RuntimeError("no source TOS DB dump found")

    target = load_tos_channel_config(target_code)
    client = _build_target_client(target)
    target_key = build_mysqldump_target_key(source_key, target_prefix=target_prefix)
    if _target_object_exists(client, target, target_key) and not overwrite:
        return {
            "target_code": target.code,
            "target_bucket": target.bucket,
            "source_object_key": source_key,
            "target_object_key": target_key,
            "action": "skipped_existing",
        }
    if dry_run:
        return {
            "target_code": target.code,
            "target_bucket": target.bucket,
            "source_object_key": source_key,
            "target_object_key": target_key,
            "action": "would_upload",
        }

    local_path = _download_source_object_to_temp(source_key, output_dir=output_dir)
    client.put_object_from_file(target.bucket, target_key, str(local_path))
    return {
        "target_code": target.code,
        "target_bucket": target.bucket,
        "source_object_key": source_key,
        "target_object_key": target_key,
        "local_file": str(local_path),
        "bytes": local_path.stat().st_size if local_path.exists() else 0,
        "action": "uploaded",
    }


def run_channel_backup(
    *,
    target_code: str = "tos_wj",
    files: bool = True,
    db_dump: bool = True,
    mysql_prefix: str = "mysqldump",
    mysql_retention_count: int = 7,
    output_dir: str | Path | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "target_code": target_code,
        "dry_run": bool(dry_run),
        "overwrite": bool(overwrite),
    }
    if files:
        summary["files"] = copy_protected_files_to_channel(
            target_code=target_code,
            dry_run=dry_run,
            overwrite=overwrite,
        )
    if db_dump:
        summary["db_dump"] = copy_latest_mysql_dump_to_channel(
            target_code=target_code,
            target_prefix=mysql_prefix,
            output_dir=output_dir,
            dry_run=dry_run,
            overwrite=overwrite,
        )
        summary["db_dump_retention"] = cleanup_channel_mysql_dumps(
            target_code=target_code,
            target_prefix=mysql_prefix,
            keep_count=mysql_retention_count,
            dry_run=dry_run,
        )

    failed_files = int(((summary.get("files") or {}).get("failed") or 0) if isinstance(summary.get("files"), dict) else 0)
    summary["status"] = "failed" if failed_files else "success"
    if failed_files:
        summary["error_message"] = f"WJ channel file copy failed for {failed_files} protected files"
    return summary
