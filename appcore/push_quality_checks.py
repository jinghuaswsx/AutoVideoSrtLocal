from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from appcore import llm_client, local_media_storage, medias, pushes, tos_clients
from appcore.db import execute, query_one

log = logging.getLogger(__name__)

USE_CASE_CODE = "push_quality.check"
PROVIDER = "openrouter"
MODEL = "google/gemini-3.1-flash-lite-preview"
_CHECK_STATUSES = {"passed", "warning", "failed", "error"}

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS media_push_quality_checks (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  item_id INT NOT NULL,
  product_id INT NOT NULL,
  lang VARCHAR(16) NOT NULL,
  attempt_source ENUM('auto','manual') NOT NULL DEFAULT 'auto',
  status ENUM('running','passed','warning','failed','error') NOT NULL DEFAULT 'running',
  copy_fingerprint CHAR(64) NOT NULL,
  cover_fingerprint CHAR(64) NOT NULL,
  video_fingerprint CHAR(64) NOT NULL,
  copy_result_json JSON NULL,
  cover_result_json JSON NULL,
  video_result_json JSON NULL,
  summary VARCHAR(500) DEFAULT NULL,
  failed_reasons JSON NULL,
  provider VARCHAR(32) NOT NULL DEFAULT 'openrouter',
  model VARCHAR(128) NOT NULL DEFAULT 'google/gemini-3.1-flash-lite-preview',
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_push_quality_attempt (
    item_id,
    copy_fingerprint,
    cover_fingerprint,
    video_fingerprint,
    attempt_source
  ),
  KEY idx_push_quality_item_created (item_id, created_at),
  KEY idx_push_quality_status (status, created_at),
  KEY idx_push_quality_product_lang (product_id, lang)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


@dataclass(frozen=True)
class QualityFingerprints:
    copy_fingerprint: str
    cover_fingerprint: str
    video_fingerprint: str


def ensure_table() -> None:
    execute(_TABLE_SQL)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _lang(item: dict | None) -> str:
    return str((item or {}).get("lang") or "").strip().lower()


def build_fingerprints(item: dict, product: dict | None = None) -> QualityFingerprints:
    lang = _lang(item)
    product_id = int((item or {}).get("product_id") or (product or {}).get("id") or 0)
    item_id = int((item or {}).get("id") or 0)
    copy_payload = pushes.resolve_localized_text_payload(item) or {}
    return QualityFingerprints(
        copy_fingerprint=_hash_payload({
            "kind": "copy",
            "product_id": product_id,
            "lang": lang,
            "title": copy_payload.get("title") or "",
            "message": copy_payload.get("message") or "",
            "description": copy_payload.get("description") or "",
        }),
        cover_fingerprint=_hash_payload({
            "kind": "cover",
            "item_id": item_id,
            "lang": lang,
            "cover_object_key": str((item or {}).get("cover_object_key") or ""),
        }),
        video_fingerprint=_hash_payload({
            "kind": "video",
            "item_id": item_id,
            "lang": lang,
            "object_key": str((item or {}).get("object_key") or ""),
        }),
    )


def _loads_json(value: Any, fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _normalize_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    item["copy_result"] = _loads_json(item.pop("copy_result_json", None), {})
    item["cover_result"] = _loads_json(item.pop("cover_result_json", None), {})
    item["video_result"] = _loads_json(item.pop("video_result_json", None), {})
    item["failed_reasons"] = _loads_json(item.get("failed_reasons"), [])
    for key in ("started_at", "finished_at", "created_at", "updated_at"):
        value = item.get(key)
        if isinstance(value, datetime):
            item[key] = value.isoformat()
    return item


def find_reusable_auto_result(
    item_id: int,
    copy_fingerprint: str,
    cover_fingerprint: str,
    video_fingerprint: str,
) -> dict[str, Any] | None:
    ensure_table()
    row = query_one(
        "SELECT * FROM media_push_quality_checks "
        "WHERE item_id=%s AND copy_fingerprint=%s AND cover_fingerprint=%s "
        "AND video_fingerprint=%s AND attempt_source=%s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (
            int(item_id),
            copy_fingerprint,
            cover_fingerprint,
            video_fingerprint,
            "auto",
        ),
    )
    return _normalize_row(row)


def latest_for_item(item_id: int) -> dict[str, Any] | None:
    ensure_table()
    row = query_one(
        "SELECT * FROM media_push_quality_checks "
        "WHERE item_id=%s ORDER BY created_at DESC, id DESC LIMIT 1",
        (int(item_id),),
    )
    return _normalize_row(row)


def has_reusable_auto_result_for_item(item: dict, product: dict | None = None) -> bool:
    fingerprints = build_fingerprints(item, product)
    existing = find_reusable_auto_result(
        int((item or {}).get("id") or 0),
        fingerprints.copy_fingerprint,
        fingerprints.cover_fingerprint,
        fingerprints.video_fingerprint,
    )
    return bool(existing)


def find_reusable_copy_result(
    product_id: int,
    lang: str,
    copy_fingerprint: str,
) -> dict[str, Any] | None:
    ensure_table()
    row = query_one(
        "SELECT copy_result_json, provider, model, created_at "
        "FROM media_push_quality_checks "
        "WHERE product_id=%s AND lang=%s AND copy_fingerprint=%s "
        "AND copy_result_json IS NOT NULL AND status<>'running' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (int(product_id), str(lang or "").strip().lower(), copy_fingerprint),
    )
    if not row:
        return None
    result = _loads_json(row.get("copy_result_json"), {})
    if not isinstance(result, dict):
        return None
    result = dict(result)
    result["reused"] = True
    if row.get("provider"):
        result.setdefault("provider", row.get("provider"))
    if row.get("model"):
        result.setdefault("model", row.get("model"))
    return result


def evaluate_item(item_id: int, *, source: str = "auto") -> dict[str, Any]:
    source = "manual" if source == "manual" else "auto"
    item = medias.get_item(int(item_id))
    if not item:
        return {"status": "error", "error": "item_not_found", "item_id": int(item_id)}
    product = medias.get_product(int(item.get("product_id") or 0))
    if not product:
        return {"status": "error", "error": "product_not_found", "item_id": int(item_id)}

    fingerprints = build_fingerprints(item, product)
    if source == "auto":
        existing = find_reusable_auto_result(
            int(item_id),
            fingerprints.copy_fingerprint,
            fingerprints.cover_fingerprint,
            fingerprints.video_fingerprint,
        )
        if existing:
            return existing

    check_id = _record_running(item, product, fingerprints, source=source)
    try:
        result = run_three_checks(item, product, fingerprints)
    except Exception as exc:
        log.exception("push quality check failed item_id=%s", item_id)
        result = {
            "status": "error",
            "summary": "大模型检查失败",
            "failed_reasons": [str(exc)[:300] or exc.__class__.__name__],
            "copy_result": {"status": "error", "summary": "未完成"},
            "cover_result": {"status": "error", "summary": "未完成"},
            "video_result": {"status": "error", "summary": "未完成"},
        }
    return _record_finish(check_id, result)


def _record_running(
    item: dict,
    product: dict,
    fingerprints: QualityFingerprints,
    *,
    source: str,
) -> int:
    ensure_table()
    return int(execute(
        "INSERT INTO media_push_quality_checks "
        "(item_id, product_id, lang, attempt_source, status, copy_fingerprint, "
        "cover_fingerprint, video_fingerprint, provider, model, started_at) "
        "VALUES (%s, %s, %s, %s, 'running', %s, %s, %s, %s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE updated_at=NOW(), id=LAST_INSERT_ID(id)",
        (
            int(item["id"]),
            int(product["id"]),
            _lang(item),
            "manual" if source == "manual" else "auto",
            fingerprints.copy_fingerprint,
            fingerprints.cover_fingerprint,
            fingerprints.video_fingerprint,
            PROVIDER,
            MODEL,
        ),
    ))


def _record_finish(check_id: int, result: dict[str, Any]) -> dict[str, Any]:
    ensure_table()
    status = _coerce_status(result.get("status"))
    failed_reasons = result.get("failed_reasons") or []
    if not isinstance(failed_reasons, list):
        failed_reasons = [str(failed_reasons)]
    execute(
        "UPDATE media_push_quality_checks SET status=%s, summary=%s, failed_reasons=%s, "
        "copy_result_json=%s, cover_result_json=%s, video_result_json=%s, "
        "finished_at=NOW(), updated_at=NOW() WHERE id=%s",
        (
            status,
            str(result.get("summary") or "")[:500],
            json.dumps(failed_reasons, ensure_ascii=False),
            json.dumps(result.get("copy_result") or {}, ensure_ascii=False),
            json.dumps(result.get("cover_result") or {}, ensure_ascii=False),
            json.dumps(result.get("video_result") or {}, ensure_ascii=False),
            int(check_id),
        ),
    )
    row = query_one(
        "SELECT * FROM media_push_quality_checks WHERE id=%s",
        (int(check_id),),
    )
    return _normalize_row(row) or {"id": int(check_id), **result, "status": status}


def _coerce_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    return status if status in _CHECK_STATUSES else "error"


def aggregate_status(results: list[dict[str, Any]]) -> str:
    statuses = [_coerce_status((item or {}).get("status")) for item in results]
    if any(status == "error" for status in statuses):
        return "error"
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "warning" for status in statuses):
        return "warning"
    return "passed"


def _check_error_result(label: str, exc: Exception) -> dict[str, Any]:
    summary = str(exc)[:300] or exc.__class__.__name__
    return {
        "status": "error",
        "is_clean": False,
        "summary": summary,
        "issues": [summary],
        "checked_at": datetime.now(UTC).isoformat(),
        "provider": PROVIDER,
        "model": MODEL,
        "label": label,
    }


def run_three_checks(
    item: dict,
    product: dict,
    fingerprints: QualityFingerprints | None = None,
) -> dict[str, Any]:
    fingerprints = fingerprints or build_fingerprints(item, product)
    try:
        copy_result = find_reusable_copy_result(
            int((item or {}).get("product_id") or (product or {}).get("id") or 0),
            _lang(item),
            fingerprints.copy_fingerprint,
        )
        if copy_result is None:
            copy_payload = pushes.resolve_localized_text_payload(item)
            copy_result = check_copy(item, product, copy_payload)
    except Exception as exc:
        log.exception("push quality copy check failed item_id=%s", item.get("id"))
        copy_result = _check_error_result("文案", exc)
    try:
        cover_result = check_cover(item, product)
    except Exception as exc:
        log.exception("push quality cover check failed item_id=%s", item.get("id"))
        cover_result = _check_error_result("封面图", exc)
    try:
        video_result = check_video(item, product)
    except Exception as exc:
        log.exception("push quality video check failed item_id=%s", item.get("id"))
        video_result = _check_error_result("视频", exc)
    results = [copy_result, cover_result, video_result]
    status = aggregate_status(results)
    failed_reasons: list[str] = []
    for label, result in (
        ("文案", copy_result),
        ("封面图", cover_result),
        ("视频", video_result),
    ):
        if _coerce_status(result.get("status")) in {"failed", "warning", "error"}:
            summary = str(result.get("summary") or "").strip()
            failed_reasons.append(f"{label}: {summary or result.get('status')}")
    summary = "三项检查通过" if status == "passed" else "；".join(failed_reasons)[:500]
    return {
        "status": status,
        "summary": summary,
        "failed_reasons": failed_reasons,
        "copy_result": copy_result,
        "cover_result": cover_result,
        "video_result": video_result,
    }


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "is_clean", "summary", "issues"],
        "properties": {
            "status": {"type": "string", "enum": ["passed", "warning", "failed"]},
            "is_clean": {"type": "boolean"},
            "summary": {"type": "string"},
            "issues": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }


def _normalize_model_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict) and "json" in raw:
        raw = raw.get("json")
    elif isinstance(raw, dict) and "text" in raw and not {"status", "summary"} & set(raw):
        raw = raw.get("text")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {"status": "warning", "is_clean": False, "summary": raw[:200], "issues": [raw[:200]]}
    if not isinstance(raw, dict):
        raw = {}
    status = _coerce_status(raw.get("status"))
    if status == "error":
        status = "warning"
    issues = raw.get("issues") or []
    if not isinstance(issues, list):
        issues = [str(issues)]
    return {
        "status": status,
        "is_clean": bool(raw.get("is_clean")) if "is_clean" in raw else status == "passed",
        "summary": str(raw.get("summary") or "").strip()[:300],
        "issues": [str(item).strip() for item in issues if str(item).strip()][:8],
        "checked_at": datetime.now(UTC).isoformat(),
        "provider": PROVIDER,
        "model": MODEL,
    }


def _language_label(item: dict) -> str:
    lang = _lang(item)
    try:
        name = medias.get_language_name(lang)
    except Exception:
        name = lang
    return f"{name}({lang})" if name and name != lang else lang


def check_copy(item: dict, product: dict, copy_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not copy_payload:
        return {
            "status": "failed",
            "is_clean": False,
            "summary": "缺少当前语种文案",
            "issues": ["缺少当前语种文案"],
            "provider": PROVIDER,
            "model": MODEL,
        }
    lang_label = _language_label(item)
    prompt = (
        "请检查跨境电商素材推送前的小语种文案是否纯净。"
        f"目标语种：{lang_label}。\n"
        f"商品：{product.get('name') or ''} / {product.get('product_code') or ''}\n"
        "要求：标题、文案、描述必须主要是目标语种内容；不得夹杂无关英文、中文、乱码、模板残留或明显错语种内容。"
        "品牌名、商品型号、URL、单位和不可翻译专有名词可以保留。"
        "请只返回 JSON。"
        f"\n待检查文案：{json.dumps(copy_payload, ensure_ascii=False)}"
    )
    response = llm_client.invoke_chat(
        USE_CASE_CODE,
        messages=[
            {
                "role": "system",
                "content": "你是小语种本地化质检员，只输出符合 schema 的 JSON。",
            },
            {"role": "user", "content": prompt},
        ],
        user_id=product.get("user_id"),
        project_id=f"push-quality-item-{item.get('id')}",
        response_format={"type": "json_schema", "json_schema": {"name": "push_copy_quality", "schema": _response_schema()}},
        temperature=0.1,
        max_tokens=1024,
        provider_override=PROVIDER,
        model_override=MODEL,
    )
    return _normalize_model_result(response)


def _materialize_media(object_key: str) -> Path:
    key = str(object_key or "").strip()
    if not key:
        raise ValueError("object_key required")
    local_path = local_media_storage.local_path_for(key)
    if local_path.is_file():
        return local_path
    if local_media_storage.exists(key):
        local_media_storage.download_to(key, local_path)
        if local_path.is_file():
            return local_path
    local_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tos_clients.download_media_file(key, str(local_path))
        if local_path.is_file():
            return local_path
    except Exception:
        log.debug("download media object to cache failed: %s", key, exc_info=True)
    fallback_dir = Path(tempfile.gettempdir()) / "autovideosrt_push_quality"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    fallback = fallback_dir / f"{uuid.uuid4().hex}{Path(key).suffix or '.bin'}"
    tos_clients.download_media_file(key, str(fallback))
    return fallback


def _mime_for_path(path: Path) -> str:
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def _visual_prompt(kind: str, item: dict, product: dict) -> str:
    return (
        f"请检查这个{kind}是否适合作为目标语种 {_language_label(item)} 的推送素材。"
        f"商品：{product.get('name') or ''} / {product.get('product_code') or ''}。"
        "重点判断画面中文字、字幕、贴纸、包装或水印是否夹杂无关英文、中文、乱码、错语种内容，"
        "以及画面是否明显与商品或该语种市场无关。品牌名、商品型号和少量不可翻译专有名词可接受。"
        "请只输出 JSON。"
    )


def check_cover(item: dict, product: dict) -> dict[str, Any]:
    object_key = str((item or {}).get("cover_object_key") or "").strip()
    if not object_key:
        return {
            "status": "failed",
            "is_clean": False,
            "summary": "缺少封面图",
            "issues": ["缺少封面图"],
            "provider": PROVIDER,
            "model": MODEL,
        }
    path = _materialize_media(object_key)
    response = llm_client.invoke_generate(
        USE_CASE_CODE,
        prompt=_visual_prompt("封面图", item, product),
        media=[path],
        user_id=product.get("user_id"),
        project_id=f"push-quality-cover-{item.get('id')}",
        response_schema=_response_schema(),
        temperature=0.1,
        max_output_tokens=1024,
        provider_override=PROVIDER,
        model_override=MODEL,
        billing_extra={"media_kind": "image", "mime_type": _mime_for_path(path)},
    )
    return _normalize_model_result(response)


def _make_video_clip_5s(source: Path | str, *, item_id: int) -> Path:
    src = Path(source)
    out_dir = Path(tempfile.gettempdir()) / "autovideosrt_push_quality_clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{int(item_id)}_{uuid.uuid4().hex}_5s.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        "0",
        "-i",
        str(src),
        "-t",
        "5",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "1",
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
    if result.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 0:
        return out_path
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")[:300]
    raise RuntimeError(f"ffmpeg clip failed: {stderr or result.returncode}")


def check_video(item: dict, product: dict) -> dict[str, Any]:
    object_key = str((item or {}).get("object_key") or "").strip()
    if not object_key:
        return {
            "status": "failed",
            "is_clean": False,
            "summary": "缺少视频素材",
            "issues": ["缺少视频素材"],
            "provider": PROVIDER,
            "model": MODEL,
        }
    source = _materialize_media(object_key)
    clip = _make_video_clip_5s(source, item_id=int(item.get("id") or 0))
    response = llm_client.invoke_generate(
        USE_CASE_CODE,
        prompt=_visual_prompt("视频前 5 秒", item, product),
        media=[clip],
        user_id=product.get("user_id"),
        project_id=f"push-quality-video-{item.get('id')}",
        response_schema=_response_schema(),
        temperature=0.1,
        max_output_tokens=1024,
        provider_override=PROVIDER,
        model_override=MODEL,
        billing_extra={"media_kind": "video", "clip_seconds": 5, "mime_type": _mime_for_path(clip)},
    )
    return _normalize_model_result(response)
