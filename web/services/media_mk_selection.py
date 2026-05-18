from __future__ import annotations

from dataclasses import dataclass
import hashlib
import mimetypes
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlparse
from typing import Callable, Mapping, Sequence

import requests
from flask import Response, jsonify, send_file


_MK_CREDENTIALS_MISSING_ERROR = "明空凭据未配置，请先在设置页同步 wedev 凭据"
_DEFAULT_MAX_MK_VIDEO_BYTES = 2 * 1024 * 1024 * 1024
_DEFAULT_MK_SELECTION_SNAPSHOT = "2026-04-23"
_RJC_SUFFIX_RE = re.compile(r"[-_]?rjc$", re.I)


class MkCredentialsMissingError(RuntimeError):
    pass


@dataclass(frozen=True)
class MkSelectionResponse:
    payload: dict
    status_code: int


@dataclass(frozen=True)
class MkDetailResponse:
    payload: dict
    status_code: int


@dataclass(frozen=True)
class MkMediaProxyResponse:
    status_code: int
    payload: dict | None = None
    content: bytes = b""
    content_type: str | None = None
    cache_control: str | None = None


@dataclass(frozen=True)
class MkVideoProxyResponse:
    status_code: int
    payload: dict | None = None
    local_path: object | None = None
    mimetype: str | None = None


def build_mk_json_flask_response(result: MkSelectionResponse | MkDetailResponse):
    return jsonify(result.payload), result.status_code


def build_mk_admin_required_response() -> MkSelectionResponse:
    return MkSelectionResponse({"error": "\u4ec5\u7ba1\u7406\u5458\u53ef\u8bbf\u95ee"}, 403)


def build_mk_selection_refresh_response() -> MkSelectionResponse:
    return MkSelectionResponse(
        {
            "ok": False,
            "error": "not_implemented",
            "message": "\u660e\u7a7a\u9009\u54c1\u5237\u65b0\u540e\u53f0\u4efb\u52a1\u5c1a\u672a\u5b9e\u73b0",
        },
        501,
    )


def normalize_mk_media_path(raw_path: str) -> str:
    path = (raw_path or "").strip().replace("\\", "/")
    if path.startswith(("http://", "https://")):
        return ""
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if path.startswith("medias/"):
        path = path[len("medias/"):]
    if not path or ".." in path.split("/"):
        return ""
    return path


def build_mk_video_cache_object_key(media_path: str, *, cache_prefix: str) -> str:
    digest = hashlib.sha256(media_path.encode("utf-8")).hexdigest()
    ext = Path(media_path).suffix.lower()
    if ext not in {".mp4", ".mov", ".m4v", ".webm"}:
        ext = ".mp4"
    return f"{cache_prefix}/{digest}{ext}"


def guess_mk_video_type(
    media_path: str,
    *,
    guess_type_fn: Callable[[str], tuple[str | None, str | None]] = mimetypes.guess_type,
) -> str | None:
    guessed_type = (guess_type_fn(media_path)[0] or "").split(";")[0].strip()
    if guessed_type and not guessed_type.startswith("video/"):
        return None
    return guessed_type


def _parse_bounded_int(
    args: Mapping[str, str],
    name: str,
    *,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw_value = args.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(name) from exc
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _resolve_mk_selection_snapshot(
    args: Mapping[str, str],
    *,
    db_query_fn: Callable[[str, list], list[dict]],
) -> str:
    explicit_snapshot = (args.get("snapshot") or "").strip()
    if explicit_snapshot:
        return explicit_snapshot
    try:
        rows = db_query_fn(
            "SELECT MAX(snapshot_date) AS snapshot_date FROM dianxiaomi_rankings",
            [],
        )
    except Exception:
        return _DEFAULT_MK_SELECTION_SNAPSHOT
    value = rows[0].get("snapshot_date") if rows else None
    if not value:
        return _DEFAULT_MK_SELECTION_SNAPSHOT
    return str(value)[:10]


def _trim_text(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit]


def _strip_rjc(value: str) -> str:
    return _RJC_SUFFIX_RE.sub("", str(value or "").strip()).lower()


def _product_handle(value: str) -> str:
    parsed = urlparse(value or "")
    parts = [part for part in parsed.path.split("/") if part]
    if "products" not in parts:
        return ""
    index = parts.index("products")
    if index + 1 >= len(parts):
        return ""
    return _strip_rjc(parts[index + 1])


def _link_tail(value: str) -> str:
    return _product_handle(value)


def _float_value(value: object, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    multiplier = 1.0
    if "万" in text:
        multiplier = 10000.0
        text = text.replace("万", "")
    elif "千" in text:
        multiplier = 1000.0
        text = text.replace("千", "")
    text = (
        text.replace("CNY", "")
        .replace("USD", "")
        .replace("$", "")
        .replace("¥", "")
        .strip()
    )
    try:
        return float(text) * multiplier
    except (TypeError, ValueError):
        return default


def _int_value(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _visible_mk_video_rows(item: dict) -> list[dict]:
    out = []
    for raw in item.get("videos") or []:
        if not isinstance(raw, dict) or raw.get("hidden"):
            continue
        path = normalize_mk_media_path(str(raw.get("path") or ""))
        if not path:
            continue
        video = {
            "name": _trim_text(raw.get("name"), 260),
            "path": path,
            "image_path": normalize_mk_media_path(str(raw.get("image_path") or "")),
            "spends": _float_value(raw.get("spends")),
            "spends_text": str(raw.get("spends") or "").strip(),
            "ads_count": _int_value(raw.get("ads_count")),
            "author": _trim_text(raw.get("author"), 80),
            "upload_time": _trim_text(raw.get("upload_time"), 64),
            "duration_seconds": _float_value(raw.get("duration_seconds") or raw.get("duration")),
        }
        out.append(video)
    out.sort(key=lambda row: (float(row.get("spends") or 0), int(row.get("ads_count") or 0)), reverse=True)
    return out


def _select_mk_product(items: list[dict], handle: str) -> tuple[dict | None, list[dict]]:
    best: tuple[tuple[float, int, int, int, int], dict, list[dict]] | None = None
    for item in items:
        if not isinstance(item, dict):
            continue
        videos = _visible_mk_video_rows(item)
        if not videos:
            continue
        exact = any(_link_tail(str(link)) == handle for link in (item.get("product_links") or []))
        total_spends = sum(float(video.get("spends") or 0) for video in videos)
        total_ads = sum(int(video.get("ads_count") or 0) for video in videos)
        score = (1 if exact else 0, int(total_spends), total_ads, len(videos), _int_value(item.get("id")))
        if best is None or score > best[0]:
            best = (score, item, videos)
    if best is None:
        return None, []
    return best[1], best[2]


def build_mk_selection_response(
    args: Mapping[str, str],
    *,
    ranking_columns_fn: Callable[[], Sequence[str] | set[str]],
    db_query_fn: Callable[[str, list], list[dict]],
) -> MkSelectionResponse:
    keyword = (args.get("keyword") or "").strip()
    try:
        page_num = _parse_bounded_int(args, "page", default=1, minimum=1)
        page_size = _parse_bounded_int(args, "page_size", default=50, minimum=10, maximum=100)
    except ValueError as exc:
        return MkSelectionResponse(
            {
                "error": "invalid_pagination",
                "message": f"{exc.args[0]} must be an integer",
            },
            400,
        )
    offset = (page_num - 1) * page_size
    snapshot = _resolve_mk_selection_snapshot(args, db_query_fn=db_query_fn)

    ranking_columns = ranking_columns_fn()
    has_mk_product_id = "mk_product_id" in ranking_columns
    has_mk_product_name = "mk_product_name" in ranking_columns
    has_mk_total_spends = "mk_total_spends" in ranking_columns
    has_mk_video_count = "mk_video_count" in ranking_columns
    has_mk_total_ads = "mk_total_ads" in ranking_columns

    where = "dr.snapshot_date = %s"
    params: list = [snapshot]

    if keyword:
        keyword_clauses = ["dr.product_name LIKE %s"]
        params.append(f"%{keyword}%")
        if has_mk_product_name:
            keyword_clauses.append("dr.mk_product_name LIKE %s")
            params.append(f"%{keyword}%")
        where += " AND (" + " OR ".join(keyword_clauses) + ")"

    mk_product_id_select = "dr.mk_product_id" if has_mk_product_id else "NULL AS mk_product_id"
    mk_product_name_select = "dr.mk_product_name" if has_mk_product_name else "NULL AS mk_product_name"
    mk_total_spends_select = "dr.mk_total_spends" if has_mk_total_spends else "0 AS mk_total_spends"
    mk_video_count_select = "dr.mk_video_count" if has_mk_video_count else "0 AS mk_video_count"
    mk_total_ads_select = "dr.mk_total_ads" if has_mk_total_ads else "0 AS mk_total_ads"
    order_by = "dr.mk_total_spends DESC, dr.rank_position ASC" if has_mk_total_spends else "dr.rank_position ASC"

    count_row = db_query_fn(
        f"SELECT COUNT(*) AS cnt FROM dianxiaomi_rankings dr WHERE {where}",
        params,
    )
    total = count_row[0]["cnt"] if count_row else 0

    rows = db_query_fn(
        f"""
        SELECT
            dr.rank_position, dr.product_id AS shopify_id,
            dr.product_name, dr.product_url,
            dr.store, dr.sales_count, dr.order_count,
            dr.revenue_main, dr.revenue_split,
            {mk_product_id_select}, {mk_product_name_select},
            {mk_total_spends_select}, {mk_video_count_select}, {mk_total_ads_select},
            dr.media_product_id,
            mp.name AS mp_name, mp.product_code AS mp_code
        FROM dianxiaomi_rankings dr
        LEFT JOIN media_products mp ON dr.media_product_id = mp.id
        WHERE {where}
        ORDER BY {order_by}
        LIMIT %s OFFSET %s
        """,
        params + [page_size, offset],
    )

    items = []
    for row in rows:
        items.append({
            "rank": row["rank_position"],
            "shopify_id": row["shopify_id"],
            "product_name": row["product_name"],
            "product_url": row["product_url"],
            "store": row["store"],
            "sales_count": row["sales_count"],
            "order_count": row["order_count"],
            "revenue_main": row["revenue_main"],
            "revenue_split": row["revenue_split"],
            "mk_product_id": row["mk_product_id"],
            "mk_product_name": row["mk_product_name"],
            "mk_total_spends": float(row["mk_total_spends"] or 0),
            "mk_video_count": row["mk_video_count"] or 0,
            "mk_total_ads": row["mk_total_ads"] or 0,
            "media_product_id": row["media_product_id"],
            "mp_name": row["mp_name"],
            "mp_code": row["mp_code"],
        })

    return MkSelectionResponse(
        {"items": items, "total": total, "page": page_num, "page_size": page_size},
        200,
    )


def build_mk_video_materials_response(
    args: Mapping[str, str],
    *,
    db_query_fn: Callable[[str, list], list[dict]],
    build_headers_fn: Callable[[], dict],
    get_base_url_fn: Callable[[], str],
    http_get_fn=requests.get,
) -> MkSelectionResponse:
    headers = build_headers_fn()
    if "Authorization" not in headers and "Cookie" not in headers:
        return MkSelectionResponse({"error": _MK_CREDENTIALS_MISSING_ERROR}, 500)

    try:
        page_num = _parse_bounded_int(args, "page", default=1, minimum=1)
        page_size = _parse_bounded_int(args, "page_size", default=24, minimum=1, maximum=60)
        max_videos = _parse_bounded_int(args, "max_videos_per_product", default=3, minimum=1, maximum=5)
    except ValueError as exc:
        return MkSelectionResponse(
            {
                "error": "invalid_pagination",
                "message": f"{exc.args[0]} must be an integer",
            },
            400,
        )
    offset = (page_num - 1) * page_size
    snapshot = _resolve_mk_selection_snapshot(args, db_query_fn=db_query_fn)
    keyword = (args.get("keyword") or "").strip()
    where = "dr.snapshot_date = %s"
    params: list = [snapshot]
    if keyword:
        where += " AND (dr.product_name LIKE %s OR dr.product_url LIKE %s)"
        params.extend([f"%{keyword}%", f"%{keyword}%"])

    count_row = db_query_fn(
        f"SELECT COUNT(*) AS cnt FROM dianxiaomi_rankings dr WHERE {where}",
        params,
    )
    total_products = int((count_row[0].get("cnt") if count_row else 0) or 0)
    rows = db_query_fn(
        f"""
        SELECT
            dr.rank_position, dr.product_id AS shopify_id,
            dr.product_name, dr.product_url,
            dr.store, dr.sales_count, dr.order_count,
            dr.revenue_main
        FROM dianxiaomi_rankings dr
        WHERE {where}
        ORDER BY dr.rank_position ASC
        LIMIT %s OFFSET %s
        """,
        params + [page_size, offset],
    )

    base_url = (get_base_url_fn() or "https://os.wedev.vip").rstrip("/")
    stats = {
        "source_products": len(rows),
        "mk_searches": 0,
        "mk_no_handle": 0,
        "mk_no_match": 0,
        "mk_request_failed": 0,
        "videos": 0,
    }
    out = []
    for row in rows:
        handle = _product_handle(str(row.get("product_url") or ""))
        if not handle:
            stats["mk_no_handle"] += 1
            continue
        try:
            stats["mk_searches"] += 1
            response = http_get_fn(
                f"{base_url}/api/marketing/medias",
                params={"page": 1, "q": handle, "source": "", "level": "", "show_attention": 0},
                headers=headers,
                timeout=20,
            )
            data = response.json() or {}
        except Exception:
            stats["mk_request_failed"] += 1
            continue
        if data.get("is_guest") is True or str(data.get("message") or "").startswith("登录"):
            return MkSelectionResponse({"error": "明空登录已失效，请重新同步 wedev 凭据"}, 401)

        products = [item for item in ((data.get("data") or {}).get("items") or []) if isinstance(item, dict)]
        mk_product, videos = _select_mk_product(products, handle)
        if not mk_product:
            stats["mk_no_match"] += 1
            continue
        for index, video in enumerate(videos[:max_videos], start=1):
            metadata = dict(video)
            metadata.update({
                "mk_id": mk_product.get("id"),
                "product_name": mk_product.get("product_name") or "",
                "product_link": (mk_product.get("product_links") or [row.get("product_url") or ""])[0],
                "main_image": mk_product.get("main_image") or mk_product.get("image") or "",
                "product_code": handle,
            })
            out.append({
                "id": f"{mk_product.get('id') or handle}-{index}-{hashlib.sha1(str(video.get('path') or '').encode('utf-8')).hexdigest()[:10]}",
                "product_handle": handle,
                "rank_position": row.get("rank_position"),
                "shopify_id": row.get("shopify_id"),
                "product_name": row.get("product_name") or "",
                "product_url": row.get("product_url") or "",
                "store": row.get("store") or "",
                "sales_count": row.get("sales_count") or 0,
                "order_count": row.get("order_count") or 0,
                "revenue_main": row.get("revenue_main") or "",
                "mk_product_id": mk_product.get("id"),
                "mk_product_name": mk_product.get("product_name") or "",
                "mk_product_link": (mk_product.get("product_links") or [None])[0],
                "main_image": mk_product.get("main_image") or mk_product.get("image") or "",
                "video_name": video.get("name") or "",
                "video_path": video.get("path") or "",
                "video_image_path": video.get("image_path") or "",
                "video_spends": float(video.get("spends") or 0),
                "video_spends_text": video.get("spends_text") or "",
                "video_ads_count": int(video.get("ads_count") or 0),
                "video_author": video.get("author") or "",
                "video_upload_time": video.get("upload_time") or "",
                "video_duration_seconds": video.get("duration_seconds") or 0,
                "mk_video_metadata": metadata,
            })
    stats["videos"] = len(out)
    return MkSelectionResponse(
        {
            "items": out,
            "stats": stats,
            "page": page_num,
            "page_size": page_size,
            "snapshot": snapshot,
            "total_products": total_products,
            "has_more_products": offset + len(rows) < total_products,
        },
        200,
    )


def build_mk_detail_response(
    mk_id: int,
    *,
    build_headers_fn: Callable[[], dict],
    get_base_url_fn: Callable[[], str],
    is_login_expired_fn: Callable[[dict], bool],
    http_get_fn=requests.get,
) -> MkDetailResponse:
    headers = build_headers_fn()
    if "Authorization" not in headers and "Cookie" not in headers:
        return MkDetailResponse(
            {"error": "明空凭据未配置，请先在设置页同步 wedev 凭据"},
            500,
        )
    base_url = get_base_url_fn()
    try:
        resp = http_get_fn(
            f"{base_url}/api/marketing/medias/{mk_id}",
            headers=headers,
            timeout=15,
        )
        data = resp.json()
    except Exception as exc:
        return MkDetailResponse({"error": str(exc)}, 502)

    if is_login_expired_fn(data):
        return MkDetailResponse(
            {"error": "明空登录已失效，请重新同步 wedev 凭据"},
            401,
        )
    return MkDetailResponse(data, resp.status_code)


def build_mk_media_proxy_response(
    media_path: str,
    *,
    build_headers_fn: Callable[[], dict],
    get_base_url_fn: Callable[[], str],
    http_get_fn=requests.get,
) -> MkMediaProxyResponse:
    headers = build_headers_fn()
    headers.pop("Content-Type", None)
    headers["Accept"] = "image/*,*/*;q=0.8"
    if "Authorization" not in headers and "Cookie" not in headers:
        return MkMediaProxyResponse(
            status_code=500,
            payload={"error": "明空凭据未配置，请先在设置页同步 wedev 凭据"},
        )
    url = f"{get_base_url_fn()}/medias/{quote(media_path, safe='/')}"
    try:
        resp = http_get_fn(url, headers=headers, timeout=20)
    except Exception as exc:
        return MkMediaProxyResponse(status_code=502, payload={"error": str(exc)})

    if resp.status_code >= 400:
        return MkMediaProxyResponse(status_code=resp.status_code)

    content_type = (
        (resp.headers.get("content-type") or "").split(";")[0].strip()
        or mimetypes.guess_type(media_path)[0]
        or "application/octet-stream"
    )
    return MkMediaProxyResponse(
        status_code=resp.status_code,
        content=resp.content,
        content_type=content_type,
        cache_control="private, max-age=3600",
    )


def build_mk_media_proxy_flask_response(result: MkMediaProxyResponse):
    if result.payload is not None:
        return jsonify(result.payload), result.status_code
    if result.status_code >= 400 and not result.content:
        return ("", result.status_code)

    proxied = Response(result.content, status=result.status_code, content_type=result.content_type)
    if result.cache_control:
        proxied.headers["Cache-Control"] = result.cache_control
    return proxied


def cache_mk_video(
    media_path: str,
    *,
    cache_object_key_fn: Callable[[str], str],
    storage_exists_fn: Callable[[str], bool],
    build_headers_fn: Callable[[], dict],
    get_base_url_fn: Callable[[], str],
    safe_local_path_for_fn: Callable[[str], object],
    max_bytes: int = _DEFAULT_MAX_MK_VIDEO_BYTES,
    http_get_fn=requests.get,
) -> str:
    object_key = cache_object_key_fn(media_path)
    if storage_exists_fn(object_key):
        return object_key

    headers = build_headers_fn()
    if "Authorization" not in headers and "Cookie" not in headers:
        raise MkCredentialsMissingError()
    headers.pop("Content-Type", None)
    headers["Accept"] = "video/*,*/*;q=0.8"
    url = f"{get_base_url_fn()}/medias/{quote(media_path, safe='/')}"
    resp = http_get_fn(url, headers=headers, timeout=60, stream=True)
    try:
        if resp.status_code >= 400:
            http_error = requests.HTTPError(f"mk video HTTP {resp.status_code}")
            http_error.response = resp
            raise http_error
        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type and not content_type.startswith("video/"):
            raise ValueError(f"明空返回的不是视频文件: {content_type}")
        declared_size = int(resp.headers.get("content-length") or 0)
        if declared_size > max_bytes:
            raise ValueError("明空视频过大，超过 2GB")

        destination = safe_local_path_for_fn(object_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="mk_video_", dir=str(destination.parent))
        total = 0
        try:
            with os.fdopen(fd, "wb") as handle:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("明空视频过大，超过 2GB")
                    handle.write(chunk)
            os.replace(temp_name, destination)
        finally:
            if os.path.exists(temp_name):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    return object_key


def build_mk_video_proxy_response(
    media_path: str,
    guessed_type: str,
    *,
    cache_video_fn: Callable[[str], str],
    safe_local_path_for_fn: Callable[[str], object],
    guess_type_fn: Callable[[str], tuple[str | None, str | None]] = mimetypes.guess_type,
) -> MkVideoProxyResponse:
    try:
        object_key = cache_video_fn(media_path)
    except MkCredentialsMissingError:
        return MkVideoProxyResponse(status_code=500, payload={"error": _MK_CREDENTIALS_MISSING_ERROR})
    except ValueError as exc:
        return MkVideoProxyResponse(status_code=400, payload={"error": str(exc)})
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None) or 502
        return MkVideoProxyResponse(status_code=status)
    except requests.RequestException as exc:
        return MkVideoProxyResponse(status_code=502, payload={"error": str(exc)})

    mimetype = guess_type_fn(object_key)[0] or guessed_type or "video/mp4"
    try:
        local_path = safe_local_path_for_fn(object_key)
    except ValueError:
        return MkVideoProxyResponse(status_code=404)
    return MkVideoProxyResponse(status_code=200, local_path=local_path, mimetype=mimetype)


def build_mk_video_proxy_flask_response(result: MkVideoProxyResponse):
    if result.payload is not None:
        return jsonify(result.payload), result.status_code
    if result.local_path is None:
        return ("", result.status_code)
    return send_file(
        str(result.local_path),
        mimetype=result.mimetype,
        conditional=True,
    )
