"""素材信息开放接口。

- 使用 ``X-API-Key`` 校验请求，密钥从 ``llm_provider_configs.openapi_materials`` 读取
- 按 ``product_code`` 聚合返回产品基础信息、主图、文案和视频素材
- 主图 / 视频 / 视频封面的下载地址均为本地素材服务地址
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from flask import Blueprint, jsonify, request

from appcore import medias, pushes, shopify_image_tasks
from appcore.link_check_locale import detect_target_language_from_url
from appcore.db import query, query_one
from appcore.llm_provider_configs import get_provider_config
from appcore.openapi_auth import validate_openapi_key
from web.services.openapi_materials_serializers import (
    group_copywritings as _group_copywritings,
    iso_or_none as _iso_or_none,
    media_download_url as _media_download_url,
    normalize_target_url as _normalize_target_url,
    serialize_cover_map as _serialize_cover_map,
    serialize_items as _serialize_items,
    serialize_product as _serialize_product,
    serialize_shopify_image_task as _serialize_shopify_image_task,
)

bp = Blueprint("openapi_materials", __name__, url_prefix="/openapi/materials")
push_bp = Blueprint("openapi_push_items", __name__, url_prefix="/openapi/push-items")
link_check_bp = Blueprint("openapi_link_check", __name__, url_prefix="/openapi/link-check")
shopify_localizer_bp = Blueprint(
    "openapi_shopify_localizer",
    __name__,
    url_prefix="/openapi/medias/shopify-image-localizer",
)


_LIST_PAGE_SIZE_MAX = 100
_OPENAPI_OPERATOR_USER_ID = 0  # 外部 OpenAPI 调用方无用户上下文，用 0 代表 system
def _api_key_valid(required_scope: str = "materials:read") -> bool:
    cfg = get_provider_config("openapi_materials")
    provided = (request.headers.get("X-API-Key") or "").strip()
    return bool(
        validate_openapi_key(
            provided,
            (cfg.api_key if cfg else "") or "",
            required_scope=required_scope,
        )
    )


@shopify_localizer_bp.route("/languages", methods=["GET"])
def shopify_localizer_languages():
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    return jsonify({"items": medias.list_shopify_localizer_languages()})


@shopify_localizer_bp.route("/bootstrap", methods=["POST"])
def shopify_localizer_bootstrap():
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    body = request.get_json(silent=True) or {}
    product_code = str(body.get("product_code") or "").strip().lower()
    lang = str(body.get("lang") or "").strip().lower()
    if not product_code or not lang:
        return jsonify({"error": "missing product_code or lang"}), 400
    if not medias.is_valid_language(lang):
        return jsonify({"error": "invalid lang"}), 400
    if lang == "en":
        # en 是源语言；作为本地化目标无语义，避免下游把英文图覆盖回英文自身
        return jsonify({
            "error": "invalid_target_lang",
            "message": "英文为源语言，不能作为图片本地化目标语言。",
        }), 400

    product = medias.get_product_by_code(product_code)
    if not product:
        return jsonify({"error": "product not found"}), 404

    shopify_product_id_override = str(body.get("shopify_product_id") or "").strip()
    shopify_product_id = shopify_product_id_override or medias.resolve_shopify_product_id(int(product["id"]))
    if not shopify_product_id:
        return jsonify({
            "error": "shopify_product_id_missing",
            "message": "未找到 Shopify ID。请先到产品编辑页最底部填写 Shopify ID 后，再执行图片本地化工具。",
        }), 409

    # 本接口只服务详情图本地化流程，cover 归 EZ Product Image 另走流程，统一过滤掉
    reference_images = [
        item for item in medias.list_reference_images_for_lang(int(product["id"]), "en")
        if item.get("kind") == "detail" and item.get("object_key")
    ]
    localized_images = [
        item for item in medias.list_reference_images_for_lang(int(product["id"]), lang)
        if item.get("kind") == "detail" and item.get("object_key")
    ]
    if not reference_images:
        return jsonify({"error": "english references not ready"}), 409
    if not localized_images:
        return jsonify({"error": "localized images not ready"}), 409

    def _serialize(item: dict) -> dict:
        return {
            "id": item.get("id"),
            "kind": item.get("kind"),
            "filename": item.get("filename"),
            "url": _media_download_url(item.get("object_key")),
        }

    return jsonify({
        "product": {
            "id": product.get("id"),
            "product_code": product.get("product_code"),
            "shopify_product_id": shopify_product_id,
            "name": product.get("name"),
        },
        "language": {
            "code": lang,
            "name_zh": medias.get_language_name(lang),
            "shop_locale": lang,
            "folder_code": lang,
        },
        "reference_images": [_serialize(item) for item in reference_images],
        "localized_images": [_serialize(item) for item in localized_images],
    })


@shopify_localizer_bp.route("/tasks/claim", methods=["POST"])
def shopify_localizer_task_claim():
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    worker_id = str(body.get("worker_id") or "").strip() or "unknown-worker"
    try:
        lock_seconds = int(body.get("lock_seconds") or 900)
    except (TypeError, ValueError):
        lock_seconds = 900
    task = shopify_image_tasks.claim_next_task(worker_id, lock_seconds=lock_seconds)
    return jsonify({"task": _serialize_shopify_image_task(task)})


@shopify_localizer_bp.route("/tasks/<int:task_id>/heartbeat", methods=["POST"])
def shopify_localizer_task_heartbeat(task_id: int):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    worker_id = str(body.get("worker_id") or "").strip()
    try:
        lock_seconds = int(body.get("lock_seconds") or 900)
    except (TypeError, ValueError):
        lock_seconds = 900
    updated = shopify_image_tasks.heartbeat_task(task_id, worker_id, lock_seconds)
    return jsonify({"ok": bool(updated)})


@shopify_localizer_bp.route("/tasks/<int:task_id>/complete", methods=["POST"])
def shopify_localizer_task_complete(task_id: int):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    status = shopify_image_tasks.complete_task(task_id, body.get("result") or {})
    return jsonify({"ok": True, "status": status})


@shopify_localizer_bp.route("/tasks/<int:task_id>/fail", methods=["POST"])
def shopify_localizer_task_fail(task_id: int):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    body = request.get_json(silent=True) or {}
    status = shopify_image_tasks.fail_task(
        task_id,
        str(body.get("error_code") or "worker_failed"),
        str(body.get("error_message") or ""),
        body.get("result") or {},
    )
    return jsonify({"ok": True, "status": status})


@bp.route("/<product_code>", methods=["GET"])
def get_material(product_code: str):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    product = medias.get_product_by_code((product_code or "").strip().lower())
    if not product:
        return jsonify({"error": "product not found"}), 404

    product_id = product["id"]
    covers = medias.get_product_covers(product_id)
    copywritings = medias.list_copywritings(product_id)
    items = medias.list_items(product_id)

    return jsonify({
        "product": _serialize_product(product),
        "covers": _serialize_cover_map(covers),
        "copywritings": _group_copywritings(copywritings),
        "items": _serialize_items(items),
        "storage_backend": "local",
    })


@bp.route("/<product_code>/push-payload", methods=["GET"])
def build_push_payload(product_code: str):
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    lang = (request.args.get("lang") or "").strip().lower()
    if not lang:
        return jsonify({"error": "missing lang"}), 400

    code = (product_code or "").strip().lower()
    product = medias.get_product_by_code(code)
    if not product:
        return jsonify({"error": "product not found"}), 404
    if not medias.is_product_listed(product):
        return jsonify({"error": "product_not_listed"}), 409

    product_id = product["id"]
    items = medias.list_items(product_id, lang)

    product_links = (
        [f"https://newjoyloo.com/{lang}/products/{code}"]
        if lang != "en" else []
    )

    try:
        texts = pushes.resolve_push_texts(product_id)
    except (pushes.CopywritingMissingError, pushes.CopywritingParseError) as exc:
        return jsonify({
            "error": str(exc),
            "code": "copywriting_not_ready",
        }), 409

    videos = []
    for it in items:
        object_key = it.get("object_key")
        cover_object_key = it.get("cover_object_key")
        videos.append({
            "name": it.get("display_name") or it.get("filename") or "",
            "size": int(it.get("file_size") or 0),
            "width": 1080,
            "height": 1920,
            "url": (
                _media_download_url(object_key)
            ),
            "image_url": (
                _media_download_url(cover_object_key)
            ),
        })

    payload = {
        "mode": "create",
        "product_name": product.get("name") or "",
        "texts": texts,
        "product_links": product_links,
        "videos": videos,
        "source": 0,
        "level": int(product.get("importance") or 3),
        "author": "蔡靖华",
        "push_admin": "蔡靖华",
        "roas": 1.6,
        "platforms": ["tiktok"],
        "selling_point": product.get("selling_points") or "",
        "tags": [],
    }
    return jsonify(payload)


@link_check_bp.route("/bootstrap", methods=["POST"])
def bootstrap_link_check():
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    body = request.get_json(silent=True) or {}
    target_url = (body.get("target_url") or "").strip()
    if not target_url or not target_url.lower().startswith(("http://", "https://")):
        return jsonify({"error": "invalid target_url"}), 400

    normalized_url = _normalize_target_url(target_url)
    enabled_languages = {
        (row.get("code") or "").strip().lower()
        for row in (medias.list_languages() or [])
        if row and row.get("enabled", 1)
    }
    target_language = detect_target_language_from_url(target_url, enabled_languages)
    if not target_language:
        return jsonify({"error": "language not detected"}), 409

    product = medias.find_product_for_link_check_url(target_url, target_language)
    if not product:
        return jsonify({"error": "product not found"}), 404

    raw_reference_images = medias.list_reference_images_for_lang(int(product["id"]), target_language)
    if not raw_reference_images:
        return jsonify({"error": "references not ready"}), 409

    reference_images = []
    for item in raw_reference_images:
        object_key = (item.get("object_key") or "").strip()
        if not object_key:
            continue
        reference_images.append({
            "id": item.get("id"),
            "kind": item.get("kind"),
            "filename": item.get("filename"),
            "download_url": _media_download_url(object_key),
            "storage_backend": "local",
        })
    if not reference_images:
        return jsonify({"error": "references not ready"}), 409

    return jsonify({
        "product": {
            "id": product.get("id"),
            "product_code": product.get("product_code"),
            "name": product.get("name"),
        },
        "target_language": target_language,
        "target_language_name": medias.get_language_name(target_language),
        "matched_by": product.get("_matched_by"),
        "normalized_url": normalized_url,
        "reference_images": reference_images,
    })


def _parse_archived_filter(raw: str) -> int | None:
    """Return 0/1 to filter, None for 'all'."""
    value = (raw or "").strip().lower()
    if value == "all":
        return None
    if value == "1":
        return 1
    # 默认只看未归档
    return 0


def _batch_cover_langs(product_ids: list[int]) -> dict[int, list[str]]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT product_id, lang, object_key FROM media_product_covers "
        f"WHERE product_id IN ({placeholders})",
        tuple(product_ids),
    )
    out: dict[int, list[str]] = defaultdict(list)
    for row in rows or []:
        if row.get("object_key"):
            out[int(row["product_id"])].append(row.get("lang") or "en")
    return out


def _batch_copywriting_langs(product_ids: list[int]) -> dict[int, list[str]]:
    if not product_ids:
        return {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT DISTINCT product_id, lang FROM media_copywritings "
        f"WHERE product_id IN ({placeholders})",
        tuple(product_ids),
    )
    out: dict[int, list[str]] = defaultdict(list)
    for row in rows or []:
        out[int(row["product_id"])].append(row.get("lang") or "en")
    return out


def _batch_item_lang_counts(product_ids: list[int]) -> tuple[dict[int, dict[str, int]], dict[int, int]]:
    if not product_ids:
        return {}, {}
    placeholders = ",".join(["%s"] * len(product_ids))
    rows = query(
        f"SELECT product_id, lang, COUNT(*) AS c FROM media_items "
        f"WHERE deleted_at IS NULL AND product_id IN ({placeholders}) "
        f"GROUP BY product_id, lang",
        tuple(product_ids),
    )
    per_lang: dict[int, dict[str, int]] = defaultdict(dict)
    totals: dict[int, int] = defaultdict(int)
    for row in rows or []:
        pid = int(row["product_id"])
        lang = row.get("lang") or "en"
        cnt = int(row.get("c") or 0)
        per_lang[pid][lang] = cnt
        totals[pid] += cnt
    return per_lang, totals


@bp.route("", methods=["GET"], strict_slashes=False)
def list_materials():
    """产品列表，供 AutoPush 子项目拉清单。"""
    if not _api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(_LIST_PAGE_SIZE_MAX, int(request.args.get("page_size") or 20)))
    except (TypeError, ValueError):
        page_size = 20

    q = (request.args.get("q") or "").strip()
    archived = _parse_archived_filter(request.args.get("archived") or "0")

    where = ["deleted_at IS NULL"]
    args: list[Any] = []
    if archived is not None:
        where.append("archived=%s")
        args.append(archived)
    if q:
        where.append("(name LIKE %s OR product_code LIKE %s)")
        like = f"%{q}%"
        args.extend([like, like])
    where_sql = " AND ".join(where)

    total_row = query(
        f"SELECT COUNT(*) AS c FROM media_products WHERE {where_sql}",
        tuple(args),
    )
    total = int((total_row[0] if total_row else {}).get("c") or 0)

    offset = (page - 1) * page_size
    rows = query(
        f"SELECT id, product_code, name, archived, ad_supported_langs, "
        f"       created_at, updated_at "
        f"FROM media_products WHERE {where_sql} "
        f"ORDER BY updated_at DESC, id DESC LIMIT %s OFFSET %s",
        tuple(args + [page_size, offset]),
    )

    product_ids = [int(r["id"]) for r in rows or []]
    cover_map = _batch_cover_langs(product_ids)
    copy_map = _batch_copywriting_langs(product_ids)
    item_lang_map, item_total_map = _batch_item_lang_counts(product_ids)

    items = []
    for row in rows or []:
        pid = int(row["id"])
        items.append({
            "id": pid,
            "product_code": row.get("product_code"),
            "name": row.get("name"),
            "archived": bool(row.get("archived")),
            "ad_supported_langs": row.get("ad_supported_langs") or "",
            "created_at": _iso_or_none(row.get("created_at")),
            "updated_at": _iso_or_none(row.get("updated_at")),
            "cover_langs": sorted(cover_map.get(pid, [])),
            "copywriting_langs": sorted(copy_map.get(pid, [])),
            "item_langs": item_lang_map.get(pid, {}),
            "total_items": item_total_map.get(pid, 0),
        })

    return jsonify({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    })


# ================================================================
# /openapi/push-items —— 素材 × 语种 级的推送视图 + 写回接口。
# 供 AutoPush 本地子项目使用，复用 appcore/pushes.py 的 helper。
# ================================================================


def _push_api_key_valid() -> bool:
    return _api_key_valid("push:write")


def _serialize_push_item(item: dict, product: dict) -> dict:
    """把 media_items × media_products 行序列化为 AutoPush 列表的一行。"""
    readiness = pushes.compute_readiness(item, product)
    status = pushes.compute_status(item, product)
    latest_push = None
    latest_id = item.get("latest_push_id")
    if latest_id:
        row = query_one(
            "SELECT status, error_message, created_at "
            "FROM media_push_logs WHERE id=%s",
            (latest_id,),
        )
        if row:
            latest_push = {
                "status": row.get("status"),
                "error_message": row.get("error_message"),
                "created_at": _iso_or_none(row.get("created_at")),
            }
    cover_key = item.get("cover_object_key")
    return {
        "item_id": item["id"],
        "product_id": item.get("product_id"),
        "product_code": product.get("product_code"),
        "product_name": product.get("name"),
        "listing_status": medias.normalize_listing_status(product.get("listing_status")),
        "lang": item.get("lang") or "en",
        "filename": item.get("filename"),
        "display_name": item.get("display_name") or item.get("filename"),
        "file_size": item.get("file_size"),
        "duration_seconds": item.get("duration_seconds"),
        "cover_url": (
            _media_download_url(cover_key)
        ),
        "status": status,
        "readiness": readiness,
        "pushed_at": _iso_or_none(item.get("pushed_at")),
        "latest_push": latest_push,
        "created_at": _iso_or_none(item.get("created_at")),
    }


@push_bp.route("", methods=["GET"], strict_slashes=False)
def list_push_items():
    """素材 × 语种级的扁平列表。

    Query:
      - page (int, default 1)
      - page_size (int, default 20, max 100)
      - q (string, 可选, 按 product name/code + 素材 filename 模糊)
      - status (string, 可选, 状态过滤: production / pending / pushed / failed, 多个用逗号)
      - lang (string, 可选, 语种过滤, 多个用逗号)
    """
    if not _push_api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = max(1, min(_LIST_PAGE_SIZE_MAX, int(request.args.get("page_size") or 20)))
    except (TypeError, ValueError):
        page_size = 20

    q = (request.args.get("q") or "").strip()
    lang_param = (request.args.get("lang") or "").strip()
    status_param = (request.args.get("status") or "").strip()
    lang_filter = [s for s in (lang_param.split(",") if lang_param else []) if s]
    status_filter = [s for s in (status_param.split(",") if status_param else []) if s]

    # status 计算需要在 Python 层（compute_status），无法下推 SQL。
    # 策略：先用 lang/q 把 DB 数据拉出来（不在 DB 分页），compute_status 后按
    # status 过滤，再在内存里分页。total 始终为状态过滤后的最终数，保证分页一致。
    rows, _db_total = pushes.list_items_for_push(
        langs=lang_filter or None,
        keyword="",
        product_term=q,
        offset=0,
        limit=10000,
    )

    all_items: list[dict] = []
    for row in rows:
        item_shape = dict(row)
        product_shape = {
            "id": row.get("product_id"),
            "name": row.get("product_name"),
            "product_code": row.get("product_code"),
            "ad_supported_langs": row.get("ad_supported_langs"),
            "shopify_image_status_json": row.get("shopify_image_status_json"),
            "selling_points": row.get("selling_points"),
            "importance": row.get("importance"),
            "listing_status": row.get("listing_status"),
        }
        all_items.append(_serialize_push_item(item_shape, product_shape))

    if status_filter:
        all_items = [it for it in all_items if it["status"] in status_filter]

    total = len(all_items)
    start = (page - 1) * page_size
    end = start + page_size
    items = all_items[start:end]

    return jsonify({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    })


@push_bp.route("/<int:item_id>", methods=["GET"])
def get_push_item(item_id: int):
    """单条素材详情 + 状态，AutoPush 推送前的确认用。"""
    if not _push_api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    item = medias.get_item(item_id)
    if not item:
        return jsonify({"error": "item not found"}), 404
    product = medias.get_product(item["product_id"])
    if not product:
        return jsonify({"error": "product not found"}), 404
    return jsonify(_serialize_push_item(item, product))


@push_bp.route("/by-keys", methods=["GET"], strict_slashes=False)
def get_push_item_payload_by_keys():
    """按 (product_id, lang, filename) 三元组精确定位素材并返回推送 payload。

    同一产品同一语种下可能有多条视频素材，必须带 filename 才能唯一匹配。
    依赖索引 idx_product_lang_filename。
    """
    if not _push_api_key_valid():
        return jsonify({"error": "invalid api key"}), 401

    try:
        product_id = int(request.args.get("product_id") or 0)
    except (TypeError, ValueError):
        product_id = 0
    lang = (request.args.get("lang") or "").strip()
    filename = (request.args.get("filename") or "").strip()
    if not product_id or not lang or not filename:
        return jsonify({
            "error": "missing params",
            "required": ["product_id", "lang", "filename"],
        }), 400

    item = medias.find_item_by_keys(product_id, lang, filename)
    if not item:
        return jsonify({"error": "item not found"}), 404
    product = medias.get_product(product_id)
    if not product:
        return jsonify({"error": "product not found"}), 404

    try:
        payload = pushes.build_item_payload(item, product)
    except pushes.ProductNotListedError as exc:
        return jsonify({
            "error": str(exc),
            "code": "product_not_listed",
        }), 409
    except (pushes.CopywritingMissingError, pushes.CopywritingParseError) as exc:
        return jsonify({
            "error": str(exc),
            "code": "copywriting_not_ready",
        }), 409
    localized_text = pushes.resolve_localized_text_payload(item)
    localized_texts_request = pushes.build_localized_texts_request(item)
    return jsonify({
        "item_id": item["id"],
        "mk_id": product.get("mk_id"),
        "item": _serialize_push_item(item, product),
        "payload": payload,
        "localized_text": localized_text,
        "localized_texts_request": localized_texts_request,
    })


@push_bp.route("/<int:item_id>/mark-pushed", methods=["POST"])
def mark_pushed(item_id: int):
    """AutoPush 推送成功后写回。"""
    if not _push_api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    item = medias.get_item(item_id)
    if not item:
        return jsonify({"error": "item not found"}), 404
    body = request.get_json(silent=True) or {}
    payload = body.get("request_payload") or {}
    response_body = body.get("response_body")
    log_id = pushes.record_push_success(
        item_id=item_id,
        operator_user_id=_OPENAPI_OPERATOR_USER_ID,
        payload=payload,
        response_body=response_body,
    )
    return jsonify({"ok": True, "log_id": log_id})


@push_bp.route("/<int:item_id>/mark-failed", methods=["POST"])
def mark_failed(item_id: int):
    """AutoPush 推送失败后写回。"""
    if not _push_api_key_valid():
        return jsonify({"error": "invalid api key"}), 401
    item = medias.get_item(item_id)
    if not item:
        return jsonify({"error": "item not found"}), 404
    body = request.get_json(silent=True) or {}
    payload = body.get("request_payload") or {}
    response_body = body.get("response_body")
    error_message = body.get("error_message")
    log_id = pushes.record_push_failure(
        item_id=item_id,
        operator_user_id=_OPENAPI_OPERATOR_USER_ID,
        payload=payload,
        error_message=error_message,
        response_body=response_body,
    )
    return jsonify({"ok": True, "log_id": log_id})
