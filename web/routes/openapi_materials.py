"""素材信息开放接口。

- 使用 ``X-API-Key`` 校验请求，密钥从 ``llm_provider_configs.openapi_materials`` 读取
- 按 ``product_code`` 聚合返回产品基础信息、主图、文案和视频素材
- 主图 / 视频 / 视频封面的下载地址均为本地素材服务地址
"""
from __future__ import annotations

from flask import Blueprint, request

from appcore import medias, openapi_materials as openapi_materials_store, pushes
from appcore.link_check_locale import detect_target_language_from_url
from appcore.llm_provider_configs import get_provider_config
from appcore.openapi_auth import validate_openapi_key
from web.services.openapi_materials_listing import (
    LIST_PAGE_SIZE_MAX as _LIST_PAGE_SIZE_MAX,
    build_materials_list_response as _build_materials_list_response,
)
from web.services.openapi_materials_serializers import (
    build_material_detail_response as _build_material_detail_response,
    media_download_url as _media_download_url,
)
from web.services.openapi_link_check import (
    LinkCheckBootstrapError as _LinkCheckBootstrapError,
    build_link_check_bootstrap_response as _build_link_check_bootstrap_response,
)
from web.services.openapi_shopify_localizer import (
    ShopifyLocalizerBootstrapError as _ShopifyLocalizerBootstrapError,
    build_shopify_localizer_bootstrap_response as _build_shopify_localizer_bootstrap_response,
    build_shopify_localizer_domains_response as _build_shopify_localizer_domains_response,
    build_shopify_localizer_task_claim_response as _build_shopify_localizer_task_claim_response,
    build_shopify_localizer_task_complete_response as _build_shopify_localizer_task_complete_response,
    build_shopify_localizer_task_fail_response as _build_shopify_localizer_task_fail_response,
    build_shopify_localizer_task_heartbeat_response as _build_shopify_localizer_task_heartbeat_response,
)
from web.services.openapi_push_items import (
    build_mark_failed_response as _build_mark_failed_response,
    build_mark_pushed_response as _build_mark_pushed_response,
    build_material_push_payload as _build_material_push_payload,
    build_push_item_payload_response as _build_push_item_payload_response,
    filter_push_items_by_status as _filter_push_items_by_status,
    paginate_push_items as _paginate_push_items,
    serialize_push_item as _serialize_push_item,
    serialize_push_item_rows as _serialize_push_item_rows,
)
from web.services.openapi_responses import (
    build_openapi_error_response,
    build_openapi_payload_response,
    openapi_flask_response,
)

bp = Blueprint("openapi_materials", __name__, url_prefix="/openapi/materials")
push_bp = Blueprint("openapi_push_items", __name__, url_prefix="/openapi/push-items")
link_check_bp = Blueprint("openapi_link_check", __name__, url_prefix="/openapi/link-check")
shopify_localizer_bp = Blueprint(
    "openapi_shopify_localizer",
    __name__,
    url_prefix="/openapi/medias/shopify-image-localizer",
)


_OPENAPI_OPERATOR_USER_ID = 0  # 外部 OpenAPI 调用方无用户上下文，用 0 代表 system


def _openapi_payload_response(payload: dict, status_code: int = 200):
    return openapi_flask_response(build_openapi_payload_response(payload, status_code))


def _openapi_error_response(error: str, status_code: int, **extra):
    return openapi_flask_response(build_openapi_error_response(error, status_code, **extra))


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
    # 公开语言列表，桌面工具启动时无 api key 也能拉到，避免首次配置前 fallback 到默认5语言。
    return _openapi_payload_response({"items": medias.list_shopify_localizer_languages()})


@shopify_localizer_bp.route("/domains", methods=["GET"])
def shopify_localizer_domains():
    # 公开域名列表，桌面工具启动时无 api key 也能拉到，避免首次配置前 fallback 到默认单域名。
    return _openapi_payload_response(_build_shopify_localizer_domains_response())


@shopify_localizer_bp.route("/bootstrap", methods=["POST"])
def shopify_localizer_bootstrap():
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)

    body = request.get_json(silent=True) or {}
    try:
        payload = _build_shopify_localizer_bootstrap_response(
            body,
            is_valid_language_fn=medias.is_valid_language,
            get_product_by_code_fn=medias.get_product_by_code,
            resolve_shopify_product_id_fn=medias.resolve_shopify_product_id,
            list_reference_images_for_lang_fn=medias.list_reference_images_for_lang,
            get_language_name_fn=medias.get_language_name,
            media_download_url_fn=_media_download_url,
        )
    except _ShopifyLocalizerBootstrapError as exc:
        error_payload = {"error": exc.error}
        if exc.message:
            error_payload["message"] = exc.message
        return _openapi_payload_response(error_payload, exc.status_code)
    return _openapi_payload_response(payload)


@shopify_localizer_bp.route("/shopify-id", methods=["POST"])
def shopify_localizer_save_shopify_id():
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)

    body = request.get_json(silent=True) or {}
    product_code = str(body.get("product_code") or "").strip().lower()
    domain = str(body.get("domain") or "").strip().lower()
    shopify_product_id = str(body.get("shopify_product_id") or "").strip()
    if not product_code or not domain or not shopify_product_id:
        return _openapi_error_response("missing product_code, domain or shopify_product_id", 400)
    if not shopify_product_id.isdigit():
        return _openapi_error_response("shopify_product_id must be numeric", 400)

    product = medias.get_product_by_code(product_code)
    if not product:
        return _openapi_error_response("product not found", 404)

    saved = medias.save_shopify_product_id_for_domain(int(product["id"]), domain, shopify_product_id)
    return _openapi_payload_response({
        "ok": True,
        "saved": saved,
        "product_id": int(product["id"]),
        "domain": domain,
        "shopify_product_id": shopify_product_id,
    })


@shopify_localizer_bp.route("/tasks/claim", methods=["POST"])
def shopify_localizer_task_claim():
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)
    body = request.get_json(silent=True) or {}
    return _openapi_payload_response(_build_shopify_localizer_task_claim_response(body))


@shopify_localizer_bp.route("/tasks/<int:task_id>/heartbeat", methods=["POST"])
def shopify_localizer_task_heartbeat(task_id: int):
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)
    body = request.get_json(silent=True) or {}
    return _openapi_payload_response(_build_shopify_localizer_task_heartbeat_response(task_id, body))


@shopify_localizer_bp.route("/tasks/<int:task_id>/complete", methods=["POST"])
def shopify_localizer_task_complete(task_id: int):
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)
    body = request.get_json(silent=True) or {}
    return _openapi_payload_response(_build_shopify_localizer_task_complete_response(task_id, body))


@shopify_localizer_bp.route("/tasks/<int:task_id>/fail", methods=["POST"])
def shopify_localizer_task_fail(task_id: int):
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)
    body = request.get_json(silent=True) or {}
    return _openapi_payload_response(_build_shopify_localizer_task_fail_response(task_id, body))


@bp.route("/<product_code>", methods=["GET"])
def get_material(product_code: str):
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)

    product = medias.get_product_by_code((product_code or "").strip().lower())
    if not product:
        return _openapi_error_response("product not found", 404)

    return _openapi_payload_response(_build_material_detail_response(product))


@bp.route("/<product_code>/push-payload", methods=["GET"])
def build_push_payload(product_code: str):
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)

    lang = (request.args.get("lang") or "").strip().lower()
    if not lang:
        return _openapi_error_response("missing lang", 400)

    code = (product_code or "").strip().lower()
    product = medias.get_product_by_code(code)
    if not product:
        return _openapi_error_response("product not found", 404)

    try:
        payload = _build_material_push_payload(product, lang=lang, product_code=code)
    except pushes.ProductNotListedError as exc:
        return _openapi_error_response(str(exc), 409)
    except (pushes.CopywritingMissingError, pushes.CopywritingParseError) as exc:
        return _openapi_error_response(str(exc), 409, code="copywriting_not_ready")
    return _openapi_payload_response(payload)


@link_check_bp.route("/bootstrap", methods=["POST"])
def bootstrap_link_check():
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)

    body = request.get_json(silent=True) or {}
    try:
        payload = _build_link_check_bootstrap_response(
            body.get("target_url"),
            detect_target_language_fn=detect_target_language_from_url,
        )
    except _LinkCheckBootstrapError as exc:
        return _openapi_error_response(exc.error, exc.status_code)
    return _openapi_payload_response(payload)


@bp.route("", methods=["GET"], strict_slashes=False)
def list_materials():
    """产品列表，供 AutoPush 子项目拉清单。"""
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)

    return _openapi_payload_response(
        _build_materials_list_response(
            page_raw=request.args.get("page") or "1",
            page_size_raw=request.args.get("page_size") or "20",
            q=request.args.get("q") or "",
            archived_raw=request.args.get("archived") or "0",
            query_fn=openapi_materials_store.query_material_rows,
        )
    )


# ================================================================
# /openapi/push-items —— 素材 × 语种 级的推送视图 + 写回接口。
# 供 AutoPush 本地子项目使用，复用 appcore/pushes.py 的 helper。
# ================================================================


def _push_api_key_valid() -> bool:
    return _api_key_valid("push:write")


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
        return _openapi_error_response("invalid api key", 401)

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

    all_items = _serialize_push_item_rows(
        rows,
        query_one_fn=openapi_materials_store.query_one_material_row,
    )
    all_items = _filter_push_items_by_status(all_items, status_filter)
    total = len(all_items)
    items = _paginate_push_items(all_items, page=page, page_size=page_size)

    return _openapi_payload_response(
        {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@push_bp.route("/<int:item_id>", methods=["GET"])
def get_push_item(item_id: int):
    """单条素材详情 + 状态，AutoPush 推送前的确认用。"""
    if not _push_api_key_valid():
        return _openapi_error_response("invalid api key", 401)
    item = medias.get_item(item_id)
    if not item:
        return _openapi_error_response("item not found", 404)
    product = medias.get_product(item["product_id"])
    if not product:
        return _openapi_error_response("product not found", 404)
    return _openapi_payload_response(
        _serialize_push_item(
            item,
            product,
            query_one_fn=openapi_materials_store.query_one_material_row,
        )
    )


@push_bp.route("/by-keys", methods=["GET"], strict_slashes=False)
def get_push_item_payload_by_keys():
    """按 (product_id, lang, filename) 三元组精确定位素材并返回推送 payload。

    同一产品同一语种下可能有多条视频素材，必须带 filename 才能唯一匹配。
    依赖索引 idx_product_lang_filename。
    """
    if not _push_api_key_valid():
        return _openapi_error_response("invalid api key", 401)

    try:
        product_id = int(request.args.get("product_id") or 0)
    except (TypeError, ValueError):
        product_id = 0
    lang = (request.args.get("lang") or "").strip()
    filename = (request.args.get("filename") or "").strip()
    if not product_id or not lang or not filename:
        return _openapi_error_response(
            "missing params",
            400,
            required=["product_id", "lang", "filename"],
        )

    item = medias.find_item_by_keys(product_id, lang, filename)
    if not item:
        return _openapi_error_response("item not found", 404)
    product = medias.get_product(product_id)
    if not product:
        return _openapi_error_response("product not found", 404)

    try:
        response_payload = _build_push_item_payload_response(
            item,
            product,
            query_one_fn=openapi_materials_store.query_one_material_row,
        )
    except pushes.ProductNotListedError as exc:
        return _openapi_error_response(str(exc), 409, code="product_not_listed")
    except (pushes.CopywritingMissingError, pushes.CopywritingParseError) as exc:
        return _openapi_error_response(str(exc), 409, code="copywriting_not_ready")
    return _openapi_payload_response(response_payload)


@push_bp.route("/<int:item_id>/mark-pushed", methods=["POST"])
def mark_pushed(item_id: int):
    """AutoPush 推送成功后写回。"""
    if not _push_api_key_valid():
        return _openapi_error_response("invalid api key", 401)
    item = medias.get_item(item_id)
    if not item:
        return _openapi_error_response("item not found", 404)
    body = request.get_json(silent=True) or {}
    response_payload = _build_mark_pushed_response(
        item_id,
        body,
        operator_user_id=_OPENAPI_OPERATOR_USER_ID,
    )
    return _openapi_payload_response(response_payload)


@push_bp.route("/<int:item_id>/mark-failed", methods=["POST"])
def mark_failed(item_id: int):
    """AutoPush 推送失败后写回。"""
    if not _push_api_key_valid():
        return _openapi_error_response("invalid api key", 401)
    item = medias.get_item(item_id)
    if not item:
        return _openapi_error_response("item not found", 404)
    body = request.get_json(silent=True) or {}
    response_payload = _build_mark_failed_response(
        item_id,
        body,
        operator_user_id=_OPENAPI_OPERATOR_USER_ID,
    )
    return _openapi_payload_response(response_payload)


@shopify_localizer_bp.route("/ai-listing/tasks", methods=["GET"])
def shopify_localizer_list_ai_listing_tasks():
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)
    
    from appcore import db
    tasks = db.query(
        "SELECT id, product_code, source_link, transit_link, target_store_domain, pricing_ratio, pricing_offset, generated_title "
        "FROM ai_listing_tasks "
        "WHERE status = 'completed' AND (shopify_product_id IS NULL OR shopify_product_id = '') "
        "ORDER BY id DESC"
    )
    return _openapi_payload_response({"tasks": tasks})


@shopify_localizer_bp.route("/ai-listing/tasks/<int:task_id>", methods=["GET"])
def shopify_localizer_get_ai_listing_task(task_id: int):
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)
    
    from appcore import db
    task = db.query_one("SELECT * FROM ai_listing_tasks WHERE id = %s", (task_id,))
    if not task:
        return _openapi_error_response("task not found", 404)
        
    assets = db.query(
        "SELECT id, asset_type, original_url, transformed_url, ai_classification, is_selected, sort_order "
        "FROM ai_listing_assets "
        "WHERE task_id = %s "
        "ORDER BY sort_order ASC, id ASC",
        (task_id,)
    )
    
    import json
    skus = []
    if task["generated_skus_json"]:
        try:
            skus = json.loads(task["generated_skus_json"])
        except Exception:
            skus = []
            
    base_url = request.url_root.rstrip('/')
    processed_assets = []
    for asset in assets:
        img_key = asset["transformed_url"] or asset["original_url"]
        download_url = asset["original_url"]
        if img_key and not img_key.startswith("http"):
            download_url = f"{base_url}/medias/obj/{img_key}"
            
        processed_assets.append({
            "id": asset["id"],
            "asset_type": asset["asset_type"],
            "original_url": asset["original_url"],
            "transformed_url": asset["transformed_url"],
            "download_url": download_url,
            "ai_classification": asset["ai_classification"],
            "is_selected": asset["is_selected"],
            "sort_order": asset["sort_order"]
        })
        
    return _openapi_payload_response({
        "task": dict(task),
        "assets": processed_assets,
        "skus": skus
    })


@shopify_localizer_bp.route("/ai-listing/tasks/<int:task_id>/success", methods=["POST"])
def shopify_localizer_ai_listing_task_success(task_id: int):
    if not _api_key_valid():
        return _openapi_error_response("invalid api key", 401)
        
    body = request.get_json(silent=True) or {}
    shopify_product_id = str(body.get("shopify_product_id") or "").strip()
    if not shopify_product_id:
        return _openapi_error_response("missing shopify_product_id", 400)
        
    from appcore import db
    task = db.query_one("SELECT id FROM ai_listing_tasks WHERE id = %s", (task_id,))
    if not task:
        return _openapi_error_response("task not found", 404)
        
    import datetime
    db.execute(
        "UPDATE ai_listing_tasks SET shopify_product_id = %s, shopify_published_at = %s WHERE id = %s",
        (shopify_product_id, datetime.datetime.now(), task_id)
    )
    return _openapi_payload_response({"ok": True})

