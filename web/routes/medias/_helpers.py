from __future__ import annotations

import os
import re
import requests
from datetime import datetime
from functools import lru_cache
from urllib.parse import urlparse

from flask import jsonify
from flask_login import current_user

from appcore import local_media_storage, medias, object_keys, task_state
from appcore.db import query as db_query
from appcore.gemini_image import coerce_image_model
from appcore.material_filename_rules import (
    validate_initial_material_filename,
    validate_material_filename,
    validate_video_filename_no_spaces,
)
from web.routes import image_translate as image_translate_routes
from appcore import image_translate_settings as its


_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15MB
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,126}[a-z0-9]$")
_PRODUCT_CODE_SUFFIX = "-rjc"
_PRODUCT_CODE_SUFFIX_ERROR = "Product ID 必须以 -RJC 结尾"
_DATE_PREFIX_RE = re.compile(r"^\d{4}\.\d{2}\.\d{2}-")
_RAW_SOURCE_TITLE_DATE_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})$")
_DETAIL_IMAGES_STATIC_MAX = task_state.IMAGE_TRANSLATE_MAX_ITEMS
_DETAIL_IMAGES_GIF_MAX = 20
_DETAIL_IMAGES_MAX_DOWNLOAD_CANDIDATES = _DETAIL_IMAGES_STATIC_MAX + _DETAIL_IMAGES_GIF_MAX
_DETAIL_IMAGE_LIMITS = {
    "static": _DETAIL_IMAGES_STATIC_MAX,
    "gif": _DETAIL_IMAGES_GIF_MAX,
}
_DETAIL_IMAGE_KIND_LABELS = {
    "static": "static detail images",
    "gif": "GIF detail images",
}

_DETAIL_IMAGES_ARCHIVE_COUNTRY_PREFIXES = {
    "de": "德国",
    "fr": "法国",
    "es": "西班牙",
    "it": "意大利",
    "ja": "日本",
    "pt": "葡萄牙",
    "nl": "荷兰",
    "sv": "瑞典",
    "fi": "芬兰",
}


def _material_evaluation_message(result: dict) -> str:
    status = str((result or {}).get("status") or "")
    if status == "evaluated":
        return "AI 评估完成"
    if status == "failed":
        return str(result.get("error") or "AI 评估请求失败")
    if status == "missing_languages":
        return "未配置可评估语种，暂不能进行 AI 评估"
    if status == "missing_product_link":
        return "缺少英文商品链接，暂不能进行 AI 评估"
    if status == "product_link_unavailable":
        detail = str(result.get("error") or "").strip()
        url = str(result.get("product_url") or "").strip()
        suffix = f"：{detail}" if detail else ""
        return f"商品链接不可用或返回 404，暂不能进行 AI 评估{suffix}" + (f"（{url}）" if url else "")
    if status == "missing_cover":
        return "缺少英文封面图，暂不能进行 AI 评估"
    if status == "missing_cover_file":
        return "商品主图本地文件不存在或为空，暂不能进行 AI 评估"
    if status == "missing_video":
        return "缺少英文视频素材，暂不能进行 AI 评估"
    if status == "missing_video_file":
        return "英文视频本地文件不存在或为空，暂不能进行 AI 评估"
    if status == "product_missing":
        return "商品不存在或已删除"
    if status == "auto_attempt_limit_reached":
        return "自动评估已尝试过一次，后续请人工触发处理"
    if status == "already_evaluated":
        return "该商品已有 AI 评估结果"
    if status == "running":
        return "该商品正在评估中，请稍后再试"
    return "AI 评估未执行"


def _parse_lang(body: dict, default: str = "en") -> tuple[str | None, str | None]:
    """Return (lang, error). When validation fails, return (None, error)."""
    lang = (body.get("lang") or default).strip().lower()
    if not medias.is_valid_language(lang):
        return None, f"涓嶆敮鎸佺殑璇: {lang}"
    return lang, None


def _resolve_upload_user_id(user_id: int | None = None) -> int | None:
    if user_id is not None:
        return int(user_id)
    try:
        resolved = getattr(current_user, "id", None)
    except Exception:
        resolved = None
    return int(resolved) if resolved is not None else None


def _download_image_to_local_media(
    url: str, pid: int, prefix: str, *, user_id: int | None = None
) -> tuple[str, bytes, str] | tuple[None, None, str]:
    """Download an image from URL and store it in local media storage."""
    if not url:
        return None, None, "url required"
    upload_user_id = _resolve_upload_user_id(user_id)
    if upload_user_id is None:
        return None, None, "missing upload user"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, None, "only http/https links are supported"
    try:
        resp = requests.get(url, timeout=20, stream=True,
                            headers={"User-Agent": "Mozilla/5.0 AutoVideoSrt-Importer"})
        resp.raise_for_status()
        ct = (resp.headers.get("content-type") or "image/jpeg").split(";")[0].strip().lower()
        if not ct.startswith("image/"):
            return None, None, f"闈炲浘鐗囩被鍨? {ct}"
        data = b""
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            data += chunk
            if len(data) > _MAX_IMAGE_BYTES:
                return None, None, "image too large (>15MB)"
    except requests.RequestException as e:
        return None, None, f"涓嬭浇澶辫触: {e}"

    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(ct, ".jpg")
    name_from_url = os.path.basename(parsed.path or "") or "from_url"
    filename = f"{prefix}_{name_from_url}"
    if not filename.endswith(ext):
        filename += ext
    object_key = object_keys.build_media_object_key(upload_user_id, pid, filename)
    local_media_storage.write_bytes(object_key, data)
    return object_key, data, ext


def _validate_product_code(code: str) -> tuple[bool, str | None]:
    if not code:
        return False, "浜у搧 ID 蹇呭～"
    if not code.endswith(_PRODUCT_CODE_SUFFIX):
        return False, _PRODUCT_CODE_SUFFIX_ERROR
    if not _SLUG_RE.match(code):
        return False, "浜у搧 ID 鍙兘浣跨敤灏忓啓瀛楁瘝銆佹暟瀛楀拰杩炲瓧绗︼紝闀垮害 3-128锛屼笖棣栧熬涓嶈兘鏄繛瀛楃"
    return True, None


@lru_cache(maxsize=1)
def _dianxiaomi_rankings_columns() -> frozenset[str]:
    rows = db_query("SHOW COLUMNS FROM dianxiaomi_rankings")
    return frozenset(
        str(row.get("Field") or "").strip()
        for row in rows
        if row.get("Field")
    )


def _language_name_map() -> dict[str, str]:
    return {
        str(row.get("code") or "").strip().lower(): str(row.get("name_zh") or "").strip()
        for row in medias.list_languages()
        if str(row.get("code") or "").strip()
    }


def _validate_material_filename_for_product(
    filename: str,
    product: dict,
    lang: str,
    *,
    initial_upload: bool = False,
):
    validator = validate_initial_material_filename if initial_upload else validate_material_filename
    result = validator(
        filename,
        (product or {}).get("name") or "",
        lang,
        _language_name_map(),
    )
    if result.ok:
        return result, None
    return result, (
        jsonify({
            "error": "filename_invalid",
            "message": "文件名不符合命名规范",
            "details": list(result.errors),
            "effective_lang": result.effective_lang,
            "suggested_filename": result.suggested_filename,
        }),
        400,
    )


def _client_filename_basename(value) -> str:
    return os.path.basename(str(value or "").replace("\\", "/"))


def _raw_source_filename_error_response(filename: str):
    details = list(validate_video_filename_no_spaces(filename))
    return (
        jsonify({
            "error": "raw_source_filename_invalid",
            "message": "文件名不能包含空格",
            "details": details,
            "uploaded_filename": filename,
        }),
        400,
    )


def _check_filename_prefix(filename: str, product: dict) -> str | None:
    """轻量校验：只检查文件名以 YYYY.MM.DD-{产品名}- 开头，其余不限制。"""
    product_name = (product or {}).get("name") or ""
    if not product_name:
        return None
    if not _DATE_PREFIX_RE.match(filename):
        return '文件名必须以 "YYYY.MM.DD-" 开头'
    date_len = 10
    rest = filename[date_len + 1:]  # skip date + "-"
    if not rest.startswith(product_name + "-"):
        return f'日期之后必须紧跟 "{product_name}-"'
    return None


def _suggest_raw_source_title(product: dict) -> str | None:
    product_name = ((product or {}).get("name") or "").strip()
    if not product_name:
        return None
    today = datetime.now().strftime("%Y.%m.%d")
    return f"{today}-{product_name}-原始视频.mp4"


def _validate_raw_source_display_name(title: str, product: dict) -> list[str]:
    value = (title or "").strip()
    product_name = ((product or {}).get("name") or "").strip()
    errors: list[str] = []

    if not product_name:
        return ["当前产品尚未加载，请重试"]
    if not value:
        return ["名称不能为空，格式为 YYYY.MM.DD-产品名-xxxxxx.mp4"]

    if not value.lower().endswith(".mp4"):
        errors.append("名称必须以 .mp4 结尾")

    if len(value) < 11 or value[10] != "-":
        errors.append('名称必须以 "YYYY.MM.DD-" 开头')
        return errors

    date_str = value[:10]
    match = _RAW_SOURCE_TITLE_DATE_RE.match(date_str)
    if not match:
        errors.append(f'日期段 "{date_str}" 格式必须是 YYYY.MM.DD')
    else:
        year, month, day = (int(part) for part in match.groups())
        try:
            parsed = datetime(year, month, day)
        except ValueError:
            parsed = None
        if parsed is None or parsed.strftime("%Y.%m.%d") != date_str:
            errors.append(f'日期 "{date_str}" 不是合法日期')

    expected_prefix = f"{date_str}-{product_name}-"
    if not value.startswith(expected_prefix):
        errors.append(f'日期之后必须紧跟 "{product_name}-"')
        return errors

    tail = value[len(expected_prefix):]
    if tail.lower().endswith(".mp4"):
        tail = tail[:-4]
    if not tail.strip():
        errors.append("产品名之后的描述不能为空")
    return errors


def _list_raw_source_allowed_english_filenames(product_id: int) -> list[str]:
    rows = medias.list_items(product_id, lang="en") or []
    rows = sorted(
        rows,
        key=lambda row: (
            row.get("created_at") if isinstance(row.get("created_at"), datetime) else datetime.max,
            int(row.get("id") or 0),
        ),
    )
    seen: set[str] = set()
    filenames: list[str] = []
    for row in rows:
        filename = _client_filename_basename(row.get("filename"))
        if not filename or filename in seen:
            continue
        seen.add(filename)
        filenames.append(filename)
    return filenames


def _product_not_listed_response():
    return jsonify({
        "error": "product_not_listed",
        "message": "产品已下架，不能执行该操作",
    }), 409


def _ensure_product_listed(product: dict | None):
    if not medias.is_product_listed(product):
        return _product_not_listed_response()
    return None


def _start_image_translate_runner(task_id: str, user_id: int) -> bool:
    return image_translate_routes.start_image_translate_runner(task_id, user_id)


def _default_image_translate_model_id() -> str:
    channel = "aistudio"
    try:
        channel = its.get_channel()
    except Exception:
        pass
    try:
        return its.get_default_model(channel)
    except Exception:
        return coerce_image_model("", channel=channel)


def probe_media_info_safe(path: str) -> dict:
    try:
        from pipeline.ffutil import probe_media_info

        return probe_media_info(path) or {}
    except Exception:
        return {}


def _detail_images_archive_product_code(product: dict, pid: int) -> str:
    raw_code = str((product or {}).get("product_code") or "").strip()
    return re.sub(r"[^A-Za-z0-9_-]+", "-", raw_code).strip("-") or f"product-{pid}"


def _detail_images_is_gif(row: dict) -> bool:
    return medias.detail_image_is_gif(row)


def _detail_image_kind_from_payload(item: dict) -> str:
    content_type = str((item or {}).get("content_type") or "").split(";")[0].strip().lower()
    raw_key = str(
        (item or {}).get("object_key")
        or (item or {}).get("filename")
        or (item or {}).get("source_url")
        or ""
    ).strip().lower()
    path = urlparse(raw_key).path.lower()
    if content_type == "image/gif" or path.endswith(".gif") or raw_key.endswith(".gif"):
        return "gif"
    return "static"


def _detail_image_kind_from_download_ext(ext: str | None) -> str:
    return "gif" if str(ext or "").lower() == ".gif" else "static"


def _detail_image_empty_counts() -> dict[str, int]:
    return {"static": 0, "gif": 0}


def _detail_image_existing_counts(pid: int, lang: str) -> dict[str, int]:
    counts = _detail_image_empty_counts()
    for row in medias.list_detail_images(pid, lang):
        kind = "gif" if _detail_images_is_gif(row) else "static"
        counts[kind] += 1
    return counts


def _detail_image_incoming_counts(items: list[dict]) -> dict[str, int]:
    counts = _detail_image_empty_counts()
    for item in items:
        counts[_detail_image_kind_from_payload(item)] += 1
    return counts


def _detail_image_limit_error(
    pid: int,
    lang: str,
    incoming_items: list[dict],
    *,
    existing_counts: dict[str, int] | None = None,
) -> str | None:
    existing = existing_counts if existing_counts is not None else _detail_image_existing_counts(pid, lang)
    incoming = _detail_image_incoming_counts(incoming_items)
    for kind, max_count in _DETAIL_IMAGE_LIMITS.items():
        label = _DETAIL_IMAGE_KIND_LABELS[kind]
        if incoming[kind] > max_count:
            return f"too many {label} (max {max_count})"
        current = int(existing.get(kind) or 0)
        total = current + incoming[kind]
        if total > max_count:
            return (
                f"too many {label} "
                f"(max {max_count}, current {current}, incoming {incoming[kind]})"
            )
    return None


def _detail_images_archive_part(value: str, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    return re.sub(r'[\\/:*?"<>|]+', "-", text).strip("-") or fallback


def _detail_images_archive_basename(product: dict, pid: int, lang: str) -> str:
    base_code = _detail_images_archive_product_code(product, pid)
    archive_name = f"{base_code}_{lang}_detail-images"
    country_prefix = _DETAIL_IMAGES_ARCHIVE_COUNTRY_PREFIXES.get((lang or "").strip().lower())
    return f"{country_prefix}-{archive_name}" if country_prefix else archive_name
