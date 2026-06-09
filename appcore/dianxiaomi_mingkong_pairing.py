"""DXM03 procurement pairing helpers for the Mingkong SKU workbench."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from appcore.browser_automation_lock import browser_automation_lock
from appcore import mingkong_product_library


log = logging.getLogger(__name__)

DXM_BASE_URL = "https://www.dianxiaomi.com"
DEFAULT_DXM02_CDP_URL = "http://127.0.0.1:9223"
DEFAULT_DXM03_CDP_URL = "http://127.0.0.1:9225"
DEFAULT_DXM03_FULL_CID = "1750880-"
DEFAULT_SUBPROCESS_TIMEOUT_SECONDS = 420

DXM_PRODUCT_API = "/api/dxmCommodityProduct/pageList.json"
DXM_VIEW_COMMODITY_API = "/api/dxmCommodityProduct/viewDxmCommodityProduct.json"
DXM_ADD_COMMODITY_API = "/api/dxmCommodityProduct/addCommodityProduct.json"
DXM_ADD_COMMODITY_GROUP_API = "/api/dxmCommodityProduct/addCommodityProductGroup.json"
DXM_ADD_COMMODITY_BZ_API = "/api/dxmCommodityProduct/addCommProBz.json"
DXM_UPDATE_SOURCE_URL_API = "/api/dxmCommodityProduct/updateUrl.json"
DXM_CHILD_SKU_INFO_API = "/api/dxmCommodityProduct/getChildSkuInfo.json"
PAIR_LIST_API = "/api/dxmAlibabaProductPair/alibabaProductPairPageList.json"
PAIR_SOURCE_SYNC_API = "/api/dxmAlibabaProductPair/asnycAlibabaByDxmProSourceUrlOpt.json"
PAIR_CHECK_API = "/api/dxmAlibabaProductPair/getCheckPairOpt.json"
PAIR_CONFIRM_API = "/api/dxmAlibabaProductPair/confirmPairOpt.json"


class DianxiaomiPairingError(RuntimeError):
    """Raised when DXM03 cannot be reached or returns an unexpected response."""


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _subprocess_timeout_seconds() -> int:
    raw = os.getenv("MINGKONG_PAIRING_SUBPROCESS_TIMEOUT_SECONDS") or ""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SUBPROCESS_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_SUBPROCESS_TIMEOUT_SECONDS


def _run_pairing_subprocess(operation: str, payload: dict[str, Any]) -> Any:
    project_root = _project_root()
    timeout_seconds = _subprocess_timeout_seconds()
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        str(project_root)
        if not env.get("PYTHONPATH")
        else f"{project_root}{os.pathsep}{env['PYTHONPATH']}"
    )
    with tempfile.TemporaryDirectory(prefix="mingkong-pairing-") as tmpdir:
        output_path = Path(tmpdir) / "result.json"
        command = [
            sys.executable,
            "-m",
            "appcore.dianxiaomi_mingkong_pairing",
            "--operation",
            operation,
            "--output",
            str(output_path),
        ]
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(payload, ensure_ascii=False, default=str),
                text=True,
                capture_output=True,
                cwd=project_root,
                env=env,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise DianxiaomiPairingError(
                f"{operation} subprocess timed out after {timeout_seconds}s"
            ) from exc

        envelope: dict[str, Any] = {}
        if output_path.exists():
            try:
                envelope = json.loads(output_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise DianxiaomiPairingError(
                    f"{operation} subprocess returned invalid JSON"
                ) from exc

        if completed.returncode != 0:
            stderr = (completed.stderr or "").strip()
            detail = envelope.get("error") or stderr or f"exit {completed.returncode}"
            raise DianxiaomiPairingError(f"{operation} subprocess failed: {detail}")
        if not envelope:
            stdout = (completed.stdout or "").strip()
            detail = stdout or "empty subprocess result"
            raise DianxiaomiPairingError(f"{operation} subprocess failed: {detail}")
        if not envelope.get("ok"):
            detail = envelope.get("error") or envelope.get("message") or "unknown error"
            raise DianxiaomiPairingError(f"{operation} subprocess failed: {detail}")
        return envelope.get("result")


def _pairing_subprocess_entrypoint(operation: str, payload: dict[str, Any]) -> Any:
    if operation == "replicate":
        return _replicate_mingkong_skus_to_dxm03_impl(
            payload.get("product") or {},
            payload.get("sku_rows") or [],
            selections=payload.get("selections"),
            cdp_url=payload.get("cdp_url"),
            source_cdp_url=payload.get("source_cdp_url"),
        )
    raise DianxiaomiPairingError(f"unsupported pairing subprocess operation: {operation}")


def _run_playwright_operation(
    label: str,
    operation: Callable[[], Any],
    *,
    force_isolated_thread: bool | None = None,
) -> Any:
    if force_isolated_thread is None:
        use_isolated_thread = True
    else:
        use_isolated_thread = bool(force_isolated_thread)

    if not use_isolated_thread:
        return operation()

    log.info(
        "%s: running Playwright sync operation on a worker thread",
        label,
    )
    with ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="mingkong-dxm-cdp",
    ) as executor:
        return executor.submit(operation).result()


def dxm03_cdp_url() -> str:
    return (
        os.getenv("DXM03_DIANXIAOMI_CDP_URL")
        or os.getenv("DIANXIAOMI_DXM03_CDP_URL")
        or DEFAULT_DXM03_CDP_URL
    )


def dxm02_cdp_url() -> str:
    return (
        os.getenv("DXM02_DIANXIAOMI_CDP_URL")
        or os.getenv("MINGKONG_DIANXIAOMI_CDP_URL")
        or os.getenv("DIANXIAOMI_DXM02_CDP_URL")
        or DEFAULT_DXM02_CDP_URL
    )


def dxm03_default_full_cid() -> str:
    return os.getenv("DXM03_DEFAULT_FULL_CID") or DEFAULT_DXM03_FULL_CID


def normalize_1688_offer_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"offer/(\d+)", text)
    if match:
        return match.group(1)
    return text if text.isdigit() else ""


def _purchase_url_for_offer(offer_id: str, fallback: str = "") -> str:
    offer_id = normalize_1688_offer_id(offer_id)
    if offer_id:
        return f"https://detail.1688.com/offer/{offer_id}.html"
    return str(fallback or "").strip()


def _normalize_image_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("/productimage/"):
        return f"http://productimage-1251220924.picgz.myqcloud.com{text}"
    return text


def _stringify_form(payload: dict[str, Any]) -> dict[str, str]:
    return {key: "" if value is None else str(value) for key, value in payload.items()}


def _ensure_success(payload: dict[str, Any], action: str) -> None:
    code = payload.get("code")
    if code not in (0, "0", None):
        raise DianxiaomiPairingError(
            f"{action} failed: {payload.get('msg') or payload.get('message') or code}"
        )


def _post_form(
    ctx,
    path: str,
    payload: dict[str, Any],
    *,
    account_label: str = "DXM03",
) -> dict[str, Any]:
    response = ctx.request.post(
        f"{DXM_BASE_URL}{path}",
        form=_stringify_form(payload),
        timeout=30000,
    )
    text = response.text()
    if response.status >= 400:
        raise DianxiaomiPairingError(f"{account_label} HTTP {response.status}: {text[:200]}")
    try:
        data = response.json()
    except Exception as exc:
        raise DianxiaomiPairingError(f"{account_label} returned non-JSON: {text[:200]}") from exc
    if not isinstance(data, dict):
        raise DianxiaomiPairingError(f"{account_label} returned invalid JSON payload")
    return data


def _dxm_product_payload(sku: str, *, search_type: int = 1) -> dict[str, Any]:
    return {
        "pageNo": 1,
        "pageSize": 100,
        "searchType": int(search_type),
        "searchValue": sku,
        "saleMode": -1,
        "productMode": -1,
        "productPxId": 1,
        "productPxSxId": 0,
        "fullCid": "",
        "productSearchType": 1,
        "productGroupLxId": 1,
    }


def _pair_list_payload(sku: str) -> dict[str, Any]:
    return {
        "pageNo": 1,
        "pageSize": 20,
        "status": "",
        "searchType": 1,
        "searchValue": sku,
        "searchMode": 1,
    }


def _normalize_commodity_item(item: dict[str, Any], *, sku: str | None = None) -> dict[str, Any]:
    group_state = int(item.get("groupState") or 0)
    return {
        "id": str(item.get("id") or "").strip(),
        "parent_id": str(item.get("parentId") or "").strip(),
        "sku": str(sku or item.get("sku") or "").strip(),
        "sku_code": str(item.get("skuCode") or "").strip(),
        "product_sku": str(item.get("productSku") or item.get("goodsSku") or "").strip(),
        "name": str(item.get("name") or "").strip(),
        "name_en": str(item.get("nameEn") or "").strip(),
        "spu": str(item.get("spu") or "").strip(),
        "image_url": _normalize_image_url(item.get("imgUrl")),
        "source_url": str(item.get("sourceUrl") or "").strip(),
        "relation_flag": bool(item.get("relationFlag")),
        "group_state": group_state,
        "is_combo": group_state == 1,
        "raw": item,
    }


def _iter_commodity_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    page = ((payload.get("data") or {}).get("page") or {})
    items: list[dict[str, Any]] = []
    for group in page.get("list") or []:
        items.extend(group.get("dxmCommodityProductList") or [])
    return [item for item in items if isinstance(item, dict)]


def _search_commodity(ctx, sku: str) -> dict[str, Any] | None:
    payload = _post_form(ctx, DXM_PRODUCT_API, _dxm_product_payload(sku))
    _ensure_success(payload, "search commodity")
    for item in _iter_commodity_items(payload):
        if str(item.get("sku") or "").strip() == sku:
            return _normalize_commodity_item(item, sku=sku)
    return None


def _search_commodity_by_sku_code(ctx, sku_code: str) -> dict[str, Any] | None:
    clean = str(sku_code or "").strip()
    if not clean:
        return None
    for search_type in (1, 2, 3, 4):
        payload = _post_form(
            ctx,
            DXM_PRODUCT_API,
            _dxm_product_payload(clean, search_type=search_type),
        )
        _ensure_success(payload, "search commodity by sku code")
        for item in _iter_commodity_items(payload):
            if str(item.get("skuCode") or "").strip() == clean:
                return _normalize_commodity_item(item)
    return None


def _search_child_sku_info(ctx, product_id: str) -> list[dict[str, Any]]:
    if not str(product_id or "").strip():
        return []
    payload = _post_form(ctx, DXM_CHILD_SKU_INFO_API, {"id": product_id})
    _ensure_success(payload, "search child sku info")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if data.get("code") not in (0, "0", None):
        raise DianxiaomiPairingError(data.get("msg") or "search child sku info failed")
    rows = data.get("data") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sku = str(row.get("sku") or "").strip()
        if not sku:
            continue
        out.append({
            "product_id": str(row.get("productId") or "").strip(),
            "sku": sku,
            "name": str(row.get("name") or "").strip(),
            "quantity": int(row.get("num") or 0),
            "image_url": _normalize_image_url(row.get("imgUrl")),
        })
    return out


_COMMODITY_COPY_FIELDS = (
    "name",
    "nameEn",
    "spu",
    "price",
    "weight",
    "length",
    "width",
    "height",
    "volume",
    "imgUrl",
    "attr",
    "productType",
    "variantOrNot",
    "groupState",
    "productStatus",
    "isUsed",
    "isStock",
    "saleMode",
    "remark",
)

_DXM_IDENTITY_KEYS = {
    "id",
    "idstr",
    "puid",
    "parentid",
    "productid",
    "developmentid",
    "supplierid",
    "warehouseid",
    "warehoseid",
    "goodsshelfid",
    "creatorid",
    "updaterid",
    "createtime",
    "updatetime",
    "createdtime",
    "updatedtime",
}


def _view_commodity_detail(
    ctx,
    product_id: str,
    *,
    account_label: str = "DXM03",
) -> dict[str, Any]:
    payload = _post_form(
        ctx,
        DXM_VIEW_COMMODITY_API,
        {"id": str(product_id or "").strip()},
        account_label=account_label,
    )
    _ensure_success(payload, f"{account_label} view commodity")
    raw_data = payload.get("data")
    if isinstance(raw_data, str):
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError as exc:
            raise DianxiaomiPairingError(
                f"{account_label} view commodity returned invalid data JSON"
            ) from exc
    elif isinstance(raw_data, dict):
        data = raw_data
    else:
        data = {}
    if not isinstance(data, dict):
        raise DianxiaomiPairingError(f"{account_label} view commodity returned invalid data")
    return data


def _product_dto(detail: dict[str, Any]) -> dict[str, Any]:
    dto = detail.get("productDTO") if isinstance(detail, dict) else {}
    return dto if isinstance(dto, dict) else {}


def _strip_dxm_identity_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_dxm_identity_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    cleaned: dict[str, Any] = {}
    for key, raw in value.items():
        normalized_key = str(key).replace("_", "").lower()
        if normalized_key in _DXM_IDENTITY_KEYS:
            continue
        cleaned[key] = _strip_dxm_identity_fields(raw)
    return cleaned


def _source_commodity_from_detail(detail: dict[str, Any]) -> dict[str, Any]:
    dto = _product_dto(detail)
    product = dto.get("dxmCommodityProduct")
    return product if isinstance(product, dict) else {}


def _source_customs_from_detail(detail: dict[str, Any]) -> dict[str, Any]:
    customs = _product_dto(detail).get("dxmProductCustoms")
    return customs if isinstance(customs, dict) else {}


def _source_packs_from_detail(detail: dict[str, Any]) -> list[dict[str, Any]]:
    packs = _product_dto(detail).get("dxmProductPacks")
    return [item for item in packs if isinstance(item, dict)] if isinstance(packs, list) else []


def _target_sku_code(
    ctx,
    desired_sku_code: str,
    *,
    target_sku: str,
    max_attempts: int = 20,
) -> tuple[str, str]:
    desired = str(desired_sku_code or "").strip() or str(target_sku or "").strip()
    if not desired:
        return "", "empty"

    def available(value: str) -> bool:
        existing = _search_commodity_by_sku_code(ctx, value)
        if not existing:
            return True
        return str(existing.get("sku") or "").strip() == str(target_sku or "").strip()

    if available(desired):
        return desired, "preserved"
    for index in range(1, max_attempts + 1):
        suffix = "-MK" if index == 1 else f"-MK{index}"
        candidate = f"{desired}{suffix}"
        if available(candidate):
            return candidate, "renamed"
    raise DianxiaomiPairingError(f"DXM03 skuCode conflict cannot be resolved for {desired}")


def _replicated_commodity_form(
    source_detail: dict[str, Any],
    *,
    target_sku: str,
    target_sku_code: str,
    purchase_url: str = "",
    fallback_name: str = "",
    fallback_name_en: str = "",
) -> dict[str, str]:
    source_product = _source_commodity_from_detail(source_detail)
    group_state = int(source_product.get("groupState") or 0)
    if group_state == 1:
        raise DianxiaomiPairingError("combo sku replication requires component-first flow")
    commodity: dict[str, Any] = {}
    for key in _COMMODITY_COPY_FIELDS:
        if key in source_product:
            commodity[key] = source_product.get(key)
    commodity = _strip_dxm_identity_fields(commodity)
    commodity.update({
        "fullCid": dxm03_default_full_cid(),
        "sku": str(target_sku or "").strip(),
        "skuCode": str(target_sku_code or "").strip(),
        "name": str(source_product.get("name") or fallback_name or target_sku or "").strip(),
        "nameEn": str(source_product.get("nameEn") or fallback_name_en or "").strip(),
        "sourceUrl": str(purchase_url or source_product.get("sourceUrl") or "").strip(),
        "groupState": 0,
    })
    commodity.setdefault("productType", "100")

    form: dict[str, Any] = {
        "dxmCommodityProduct": json.dumps(commodity, ensure_ascii=False),
        "dxmWarehouseProductList": json.dumps([], ensure_ascii=False),
        "supplierProductRelationMapList": json.dumps([], ensure_ascii=False),
    }
    customs = _strip_dxm_identity_fields(_source_customs_from_detail(source_detail))
    if customs:
        form["dxmProductCustoms"] = json.dumps(customs, ensure_ascii=False)
    packs = _strip_dxm_identity_fields(_source_packs_from_detail(source_detail))
    if packs:
        form["dxmProductPacks"] = json.dumps(packs, ensure_ascii=False)
    return _commodity_save_payload(form)


def _commodity_save_payload(form: dict[str, Any]) -> dict[str, str]:
    return {
        "obj": json.dumps(form, ensure_ascii=False),
        "pid": "",
        "vid": "",
        "orderStatus": "",
        "shopId": "-1",
        "pt": "-1",
        "orderId": "",
        "orderWarehoseId": "-1",
        "orderCount": "0",
    }


def _replicated_combo_form(
    source_detail: dict[str, Any],
    *,
    target_sku: str,
    target_sku_code: str,
    target_components: list[dict[str, Any]],
    purchase_url: str = "",
    fallback_name: str = "",
    fallback_name_en: str = "",
) -> dict[str, str]:
    source_product = _source_commodity_from_detail(source_detail)
    child_ids = ",".join(
        str(component.get("target_product_id") or "").strip()
        for component in target_components
        if str(component.get("target_product_id") or "").strip()
    )
    child_nums = ",".join(
        str(int(component.get("quantity") or 1))
        for component in target_components
        if str(component.get("target_product_id") or "").strip()
    )
    if not child_ids or not child_nums:
        raise DianxiaomiPairingError("combo sku replication requires target components")

    def text_field(key: str, default: Any = "") -> str:
        value = source_product.get(key)
        if value is None:
            value = default
        return str(value)

    commodity = {
        "productId": "",
        "name": str(source_product.get("name") or fallback_name or target_sku or "").strip(),
        "nameEn": str(source_product.get("nameEn") or fallback_name_en or "").strip(),
        "skuCode": str(target_sku_code or "").strip(),
        "sku": str(target_sku or "").strip(),
        "productVariationStr": "",
        "sbmId": "",
        "agentId": "0",
        "developmentId": "0",
        "salesId": "0",
        "weight": source_product.get("weight") or "",
        "allowWeightError": source_product.get("allowWeightError") or "",
        "price": text_field("price"),
        "sourceUrl": str(purchase_url or source_product.get("sourceUrl") or "").strip(),
        "imgUrl": str(source_product.get("imgUrl") or "").strip(),
        "isUsed": 1,
        "fullCid": dxm03_default_full_cid(),
        "productType": str(source_product.get("productType") or "100"),
        "length": source_product.get("length") or 0,
        "width": source_product.get("width") or 0,
        "height": source_product.get("height") or 0,
        "qcType": source_product.get("qcType") or 0,
        "productStatus": text_field("productStatus"),
        "childIds": child_ids,
        "childNums": child_nums,
        "processFee": source_product.get("processFee") or 0,
        "qcContent": "",
        "qcImgStr": "",
        "qcImgNum": 0,
        "groupState": "1",
        "ncm": str(source_product.get("ncm") or ""),
        "cest": str(source_product.get("cest") or ""),
        "unit": str(source_product.get("unit") or ""),
        "origin": str(source_product.get("origin") or "0"),
    }
    form: dict[str, Any] = {
        "dxmCommodityProduct": json.dumps(commodity, ensure_ascii=False),
        "dxmProductCustoms": json.dumps(
            _strip_dxm_identity_fields(_source_customs_from_detail(source_detail)) or {},
            ensure_ascii=False,
        ),
        "warehouseIdList": "",
        "supplierProductRelationMapList": json.dumps([], ensure_ascii=False),
        "dxmProductPacks": json.dumps([], ensure_ascii=False),
        "imageUrls": "",
        "imgUrl": commodity["imgUrl"],
    }
    return _commodity_save_payload(form)


def _add_replicated_commodity(ctx, form_payload: dict[str, Any]) -> dict[str, Any]:
    last_error: Exception | None = None
    for api_path in (DXM_ADD_COMMODITY_API, DXM_ADD_COMMODITY_BZ_API):
        try:
            payload = _post_form(ctx, api_path, form_payload)
            _ensure_success(payload, "add replicated commodity")
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            data_code = data.get("code")
            if data_code not in (0, "0", 1, "1", None):
                raise DianxiaomiPairingError(data.get("msg") or "add replicated commodity failed")
            return payload
        except DianxiaomiPairingError as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise DianxiaomiPairingError("add replicated commodity failed")


def _add_replicated_combo_commodity(ctx, form_payload: dict[str, Any]) -> dict[str, Any]:
    payload = _post_form(ctx, DXM_ADD_COMMODITY_GROUP_API, form_payload)
    _ensure_success(payload, "add replicated combo commodity")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    data_code = data.get("code")
    if data_code not in (0, "0", 1, "1", None):
        raise DianxiaomiPairingError(data.get("msg") or "add replicated combo commodity failed")
    return payload


def _candidate_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("skuName", "name", "skuValue", "attributes", "attributeName", "specName", "title"):
        value = item.get(key)
        if value:
            parts.append(str(value))
    for key in ("skuValueList", "attributeList", "attr", "skuAttributes"):
        value = item.get(key)
        if isinstance(value, list):
            parts.extend(str(v) for v in value if v)
    return " ".join(parts).strip()


def _extract_candidate_skus(row: dict[str, Any]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_candidate(product_id: Any, sku_id: Any, title: Any) -> None:
        product_text = str(product_id or "").strip()
        sku_text = str(sku_id or "").strip()
        if not product_text or not sku_text:
            return
        key = (product_text, sku_text)
        if key in seen:
            return
        seen.add(key)
        candidates.append({
            "product_id_alibaba": product_text,
            "sku_id_alibaba": sku_text,
            "title": str(title or "").strip(),
        })

    selected_product_id = row.get("alibabaProductId") or row.get("productIdAlibaba")
    selected_sku_id = row.get("skuIdAlibaba")
    if selected_product_id and selected_sku_id:
        add_candidate(
            selected_product_id,
            selected_sku_id,
            row.get("skuNameAlibaba") or row.get("alibabaSkuName") or row.get("skuName"),
        )

    for product in row.get("alibabaProductList") or []:
        product_id = (
            product.get("alibabaProductId")
            or product.get("productIdAlibaba")
            or product.get("id")
            or product.get("offerId")
        )
        product_title = product.get("title") or product.get("name")
        sku_lists = []
        for key in (
            "alibabaProductSkuList",
            "skuList",
            "skuInfos",
            "alibabaSkuList",
            "productSkuList",
        ):
            value = product.get(key)
            if isinstance(value, list):
                sku_lists.extend(value)
        for sku_item in sku_lists:
            add_candidate(
                product_id,
                sku_item.get("skuIdAlibaba") or sku_item.get("skuId") or sku_item.get("id"),
                _candidate_text(sku_item) or product_title,
            )
    return candidates


def _normalize_pair_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    state_raw = row.get("state")
    try:
        state = int(state_raw)
    except (TypeError, ValueError):
        state = None
    alibaba_product_id = (
        str(row.get("alibabaProductId") or row.get("productIdAlibaba") or "").strip()
        or normalize_1688_offer_id(row.get("sourceUrl"))
    )
    return {
        "pair_row_id": str(row.get("id") or "").strip(),
        "product_id": str(row.get("productId") or "").strip(),
        "sku": str(row.get("sku") or "").strip(),
        "sku_code": str(row.get("skuCode") or "").strip(),
        "name": str(row.get("name") or "").strip(),
        "source_url": str(row.get("sourceUrl") or "").strip(),
        "state": state,
        "is_paired": state == 1,
        "alibaba_product_id": alibaba_product_id,
        "sku_id_alibaba": str(row.get("skuIdAlibaba") or "").strip(),
        "supplier_name": str(row.get("supplierName") or "").strip(),
        "alibaba_title": str(row.get("alibabaProductTitle") or row.get("titleAlibaba") or "").strip(),
        "candidates": _extract_candidate_skus(row),
    }


def _search_pair(ctx, sku: str) -> dict[str, Any] | None:
    payload = _post_form(ctx, PAIR_LIST_API, _pair_list_payload(sku))
    _ensure_success(payload, "search pairing")
    page = ((payload.get("data") or {}).get("page") or {})
    for row in page.get("list") or []:
        if str(row.get("sku") or "").strip() == sku:
            return _normalize_pair_row(row)
    return None


def _update_source_url(ctx, commodity_id: str, purchase_url: str) -> dict[str, Any]:
    payload = _post_form(
        ctx,
        DXM_UPDATE_SOURCE_URL_API,
        {"id": commodity_id, "sourceUrl": purchase_url},
    )
    _ensure_success(payload, "update source url")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    data_code = data.get("code")
    if data_code not in (0, "0", None):
        raise DianxiaomiPairingError(data.get("msg") or "update source url failed")
    return payload


def _trigger_source_sync(ctx, purchase_url: str) -> dict[str, Any]:
    payload = _post_form(
        ctx,
        PAIR_SOURCE_SYNC_API,
        {"urls": purchase_url, "nums": 1},
    )
    _ensure_success(payload, "sync 1688 source url")
    return payload


def _check_pair(ctx, product_id_alibaba: str, sku_id_alibaba: str) -> dict[str, Any]:
    payload = _post_form(
        ctx,
        PAIR_CHECK_API,
        {
            "checkType": 0,
            "productIdAlibaba": product_id_alibaba,
            "skuIdAlibaba": sku_id_alibaba,
        },
    )
    _ensure_success(payload, "check pairing")
    return payload


def _confirm_pair(
    ctx,
    *,
    pair_row_id: str,
    product_id_alibaba: str,
    sku_id_alibaba: str,
) -> dict[str, Any]:
    payload = _post_form(
        ctx,
        PAIR_CONFIRM_API,
        {
            "pairProductId": pair_row_id,
            "productIdAlibaba": product_id_alibaba,
            "skuIdAlibaba": sku_id_alibaba,
        },
    )
    _ensure_success(payload, "confirm pairing")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    data_code = data.get("code")
    if data_code not in (1, "1", 0, "0", None):
        raise DianxiaomiPairingError(data.get("msg") or "confirm pairing failed")
    return payload


def _open_dxm_context(cdp_url: str):
    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    try:
        browser, ctx = _connect_dxm_context(playwright, cdp_url)
        return playwright, browser, ctx
    except Exception:
        playwright.stop()
        raise


def _connect_dxm_context(playwright, cdp_url: str):
    browser = playwright.chromium.connect_over_cdp(cdp_url)
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    return browser, ctx


def _open_dxm03_context(cdp_url: str):
    return _open_dxm_context(cdp_url)


def _open_dxm02_context(cdp_url: str):
    return _open_dxm_context(cdp_url)


def _close_dxm03_context(playwright, browser) -> None:
    # Do not call browser.close() for the shared DXM03 Chrome; stopping
    # Playwright disconnects this client and leaves the logged-in profile alive.
    _ = browser
    try:
        playwright.stop()
    except Exception:
        pass


def fetch_dxm03_pairing_snapshot(
    skus: list[str],
    *,
    cdp_url: str | None = None,
    force_isolated_thread: bool | None = None,
) -> dict[str, dict[str, Any]]:
    return _run_playwright_operation(
        "dxm03_mingkong_pairing_snapshot",
        lambda: _fetch_dxm03_pairing_snapshot_impl(skus, cdp_url=cdp_url),
        force_isolated_thread=force_isolated_thread,
    )


def _fetch_dxm03_pairing_snapshot_impl(
    skus: list[str],
    *,
    cdp_url: str | None = None,
) -> dict[str, dict[str, Any]]:
    clean_skus = [str(sku or "").strip() for sku in skus if str(sku or "").strip()]
    if not clean_skus:
        return {}
    url = cdp_url or dxm03_cdp_url()
    with browser_automation_lock(
        task_code="dxm03_mingkong_pairing_snapshot",
        timeout_seconds=120,
        command=",".join(clean_skus),
    ):
        playwright, browser, ctx = _open_dxm03_context(url)
        try:
            out: dict[str, dict[str, Any]] = {}
            for sku in clean_skus:
                commodity = _search_commodity(ctx, sku)
                components: list[dict[str, Any]] = []
                if commodity and commodity.get("is_combo"):
                    components = _search_child_sku_info(ctx, commodity.get("id") or "")
                    for component in components:
                        component["pairing"] = _search_pair(ctx, component["sku"])
                out[sku] = {
                    "commodity": commodity,
                    "pairing": _search_pair(ctx, sku),
                    "combo_components": components,
                }
            return out
        finally:
            _close_dxm03_context(playwright, browser)


def _selection_map(selections: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in selections or []:
        if not isinstance(item, dict):
            continue
        for key in ("dianxiaomi_sku", "shopify_variant_id"):
            value = str(item.get(key) or "").strip()
            if value:
                out[value] = item
    return out


def _local_sku_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "shopify_product_id": row.get("shopify_product_id") or "",
        "shopify_variant_id": row.get("shopify_variant_id") or "",
        "shopify_sku": row.get("shopify_sku") or "",
        "shopify_price": row.get("shopify_price"),
        "shopify_compare_at_price": row.get("shopify_compare_at_price"),
        "shopify_currency": row.get("shopify_currency") or "USD",
        "shopify_inventory_quantity": row.get("shopify_inventory_quantity"),
        "shopify_weight_grams": row.get("shopify_weight_grams"),
        "variant_title": row.get("shopify_variant_title") or row.get("variant_title") or "",
        "dianxiaomi_sku": row.get("dianxiaomi_sku") or "",
        "dianxiaomi_product_sku": row.get("dianxiaomi_product_sku") or "",
        "dianxiaomi_sku_code": row.get("dianxiaomi_sku_code") or "",
        "dianxiaomi_name": row.get("dianxiaomi_name") or "",
        "source": row.get("source") or "",
        "image_url": row.get("image_url") or "",
        "purchase_1688_url": row.get("purchase_1688_url") or "",
        "mingkong_product_id": row.get("mingkong_product_id"),
        "mingkong_variant_id": row.get("mingkong_variant_id"),
        "mingkong_procurement": row.get("mingkong_procurement"),
        "is_combo": bool(row.get("is_combo")),
        "combo_components": row.get("combo_components") or [],
    }


_RESULT_STATUS_LABELS = {
    "already_exists": "DXM03 已存在",
    "already_paired": "DXM03 已配对",
    "already_paired_combo_components": "DXM03 组合组件已配对",
    "blocked": "阻断",
    "confirmed": "已同步",
    "created": "已复刻",
    "error": "失败",
    "pending": "待处理",
}


def _operation_item_label(item: dict[str, Any]) -> str:
    return (
        str(item.get("variant_title") or "").strip()
        or str(item.get("dianxiaomi_sku") or "").strip()
        or str(item.get("shopify_variant_id") or "").strip()
        or "SKU"
    )


def _operation_logs(action: str, items: list[dict[str, Any]]) -> list[dict[str, str]]:
    logs: list[dict[str, str]] = [{
        "level": "info",
        "message": f"{action}：后端已返回逐 SKU 处理结果",
    }]
    if not items:
        logs.append({"level": "warn", "message": "没有可处理的 SKU 行"})
        return logs
    for item in items:
        status = str(item.get("status") or item.get("error") or "").strip()
        label = _operation_item_label(item)
        readable = _RESULT_STATUS_LABELS.get(status, status or "未知状态")
        message = str(item.get("message") or "").strip()
        sku_code = str(item.get("dxm03_sku_code") or "").strip()
        original_code = str(item.get("original_mingkong_sku_code") or "").strip()
        suffix = ""
        if sku_code:
            suffix = f"；明空 SKUID {original_code or '-'} -> DXM03 SKUID {sku_code}"
        if message:
            suffix = f"{suffix}；{message}"
        level = "ok" if status in {
            "already_exists",
            "already_paired",
            "already_paired_combo_components",
            "confirmed",
            "created",
        } else ("error" if status == "error" else "warn")
        logs.append({
            "level": level,
            "message": f"{label}：{readable}{suffix}",
        })
    return logs


def load_mingkong_library_sku_rows(product: dict[str, Any]) -> dict[str, Any]:
    library_rows = mingkong_product_library.sku_rows_from_library(product)
    realtime_refresh_summary: dict[str, Any] | None = None
    if not library_rows:
        realtime_refresh_summary = mingkong_product_library.refresh_product_from_dxm02(product)
        library_rows = mingkong_product_library.sku_rows_from_library(product)
    base_rows = mingkong_product_library.public_shopify_sku_rows_from_product(product)
    rows = merge_full_sku_base_with_fill_rows(
        base_rows,
        [_local_sku_payload(row) for row in library_rows],
    )
    return {
        "rows": rows,
        "realtime_refresh": realtime_refresh_summary,
    }


def _row_sku_key(row: dict[str, Any]) -> str:
    return str(
        row.get("shopify_sku")
        or row.get("dianxiaomi_sku")
        or ""
    ).strip()


def _overlay_sku_fill(base: dict[str, Any], fill: dict[str, Any]) -> dict[str, Any]:
    if not fill:
        return dict(base)
    merged = dict(base)
    for key in (
        "dianxiaomi_sku",
        "dianxiaomi_product_sku",
        "dianxiaomi_sku_code",
        "dianxiaomi_name",
        "image_url",
        "purchase_1688_url",
        "mingkong_product_id",
        "mingkong_variant_id",
        "mingkong_procurement",
        "is_combo",
        "combo_components",
    ):
        value = fill.get(key)
        if value not in (None, "", []):
            merged[key] = value
    if not merged.get("dianxiaomi_sku") and _row_sku_key(base) == _row_sku_key(fill):
        merged["dianxiaomi_sku"] = fill.get("dianxiaomi_sku") or ""
    merged["source"] = "shopify_public_mingkong_fill" if fill else (base.get("source") or "shopify_public")
    return merged


def merge_full_sku_base_with_fill_rows(
    base_rows: list[dict[str, Any]],
    *fill_groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the full Shopify variant base and fill known Mingkong/local SKU fields."""

    if not base_rows:
        for rows in fill_groups:
            if rows:
                return [_local_sku_payload(row) for row in rows]
        return []

    fill_by_variant: dict[str, dict[str, Any]] = {}
    fill_by_sku: dict[str, dict[str, Any]] = {}
    for rows in fill_groups:
        for raw in rows or []:
            row = _local_sku_payload(raw)
            variant_id = str(row.get("shopify_variant_id") or "").strip()
            sku = _row_sku_key(row)
            if variant_id:
                fill_by_variant.setdefault(variant_id, row)
            if sku:
                fill_by_sku.setdefault(sku, row)

    out: list[dict[str, Any]] = []
    seen_variants: set[str] = set()
    for raw_base in base_rows:
        base = _local_sku_payload(raw_base)
        variant_id = str(base.get("shopify_variant_id") or "").strip()
        if not variant_id or variant_id in seen_variants:
            continue
        seen_variants.add(variant_id)
        fill = fill_by_variant.get(variant_id) or fill_by_sku.get(_row_sku_key(base)) or {}
        out.append(_overlay_sku_fill(base, fill))
    return out


def build_mingkong_library_sku_import_payload(product: dict[str, Any]) -> dict[str, Any]:
    loaded = load_mingkong_library_sku_rows(product)
    pairs: list[dict[str, Any]] = []
    for row in loaded["rows"]:
        variant_id = str(row.get("shopify_variant_id") or "").strip()
        dianxiaomi_sku = str(row.get("dianxiaomi_sku") or "").strip()
        if not variant_id:
            continue
        pairs.append({
            "shopify_product_id": row.get("shopify_product_id") or product.get("shopifyid") or "",
            "shopify_variant_id": variant_id,
            "shopify_sku": row.get("shopify_sku") or "",
            "shopify_currency": None,
            "shopify_variant_title": row.get("variant_title") or "",
            "dianxiaomi_sku": dianxiaomi_sku or None,
            "dianxiaomi_product_sku": row.get("dianxiaomi_product_sku") or None,
            "dianxiaomi_sku_code": row.get("dianxiaomi_sku_code") or None,
            "dianxiaomi_name": row.get("dianxiaomi_name") or None,
        })
    return {
        "ok": bool(pairs),
        "pairs": pairs,
        "items": loaded["rows"],
        "realtime_refresh": loaded["realtime_refresh"],
        "message": "" if pairs else "明空产品库未找到可同步的 SKU 行",
    }


def _target_value(
    target: dict[str, Any],
    source: dict[str, Any],
    *keys: str,
    default: Any = "",
) -> Any:
    for key in keys:
        value = target.get(key)
        if value is None or value == "":
            continue
        return value
    for key in keys:
        value = source.get(key)
        if value is None or value == "":
            continue
        return value
    return default


def _target_source_indexes(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_variant: dict[str, dict[str, Any]] = {}
    by_sku: dict[str, dict[str, Any]] = {}
    for raw in rows or []:
        row = _local_sku_payload(raw)
        variant_id = str(row.get("shopify_variant_id") or "").strip()
        sku = str(row.get("dianxiaomi_sku") or "").strip()
        if variant_id:
            by_variant.setdefault(variant_id, row)
        if sku:
            by_sku.setdefault(sku, row)
    return by_variant, by_sku


def build_target_sku_import_pairs(
    product: dict[str, Any],
    library_items: list[dict[str, Any]],
    targets: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Build local media SKU rows from the editable sync-review target column."""

    by_variant, by_sku = _target_source_indexes(library_items)
    pairs: list[dict[str, Any]] = []
    seen_variants: set[str] = set()
    for raw_target in targets or []:
        if not isinstance(raw_target, dict):
            continue
        target = raw_target
        source = (
            by_variant.get(str(target.get("shopify_variant_id") or "").strip())
            or by_sku.get(str(target.get("dianxiaomi_sku") or "").strip())
            or {}
        )
        variant_id = str(
            target.get("shopify_variant_id")
            or source.get("shopify_variant_id")
            or ""
        ).strip()
        if not variant_id or variant_id in seen_variants:
            continue
        target_sku = str(_target_value(target, source, "dianxiaomi_sku") or "").strip()
        seen_variants.add(variant_id)
        pairs.append({
            "shopify_product_id": _target_value(
                target,
                source,
                "shopify_product_id",
                default=product.get("shopifyid") or "",
            ),
            "shopify_variant_id": variant_id,
            "shopify_sku": _target_value(target, source, "shopify_sku"),
            "shopify_price": _target_value(target, source, "shopify_price", default=None),
            "shopify_compare_at_price": _target_value(
                target,
                source,
                "shopify_compare_at_price",
                default=None,
            ),
            "shopify_currency": _target_value(target, source, "shopify_currency", default="USD"),
            "shopify_inventory_quantity": _target_value(
                target,
                source,
                "shopify_inventory_quantity",
                default=None,
            ),
            "shopify_weight_grams": _target_value(
                target,
                source,
                "shopify_weight_grams",
                default=None,
            ),
            "shopify_variant_title": _target_value(
                target,
                source,
                "variant_title",
                "shopify_variant_title",
            ),
            "dianxiaomi_sku": target_sku or None,
            "dianxiaomi_product_sku": (
                _target_value(target, source, "dianxiaomi_product_sku") or None
            ),
            "dianxiaomi_sku_code": (
                _target_value(target, source, "dianxiaomi_sku_code") or None
            ),
            "dianxiaomi_name": _target_value(target, source, "dianxiaomi_name") or None,
        })
    return pairs


def first_purchase_url_from_targets(
    product: dict[str, Any],
    library_items: list[dict[str, Any]],
    targets: list[dict[str, Any]] | None,
) -> str:
    by_variant, by_sku = _target_source_indexes(library_items)
    for raw_target in targets or []:
        if not isinstance(raw_target, dict):
            continue
        target = raw_target
        source = (
            by_variant.get(str(target.get("shopify_variant_id") or "").strip())
            or by_sku.get(str(target.get("dianxiaomi_sku") or "").strip())
            or {}
        )
        selected_offer_id = (
            normalize_1688_offer_id(target.get("product_id_alibaba"))
            or normalize_1688_offer_id(target.get("purchase_1688_url"))
            or normalize_1688_offer_id(source.get("purchase_1688_url"))
        )
        purchase_url = _purchase_url_for_offer(
            selected_offer_id,
            target.get("purchase_1688_url")
            or source.get("purchase_1688_url")
            or product.get("purchase_1688_url")
            or "",
        )
        if purchase_url:
            return purchase_url
    return str(product.get("purchase_1688_url") or "").strip()


def _mingkong_reference_indexes(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_variant: dict[str, dict[str, Any]] = {}
    by_sku: dict[str, dict[str, Any]] = {}
    for item in rows:
        row = _local_sku_payload(item)
        variant_id = str(row.get("shopify_variant_id") or "").strip()
        sku = str(row.get("dianxiaomi_sku") or "").strip()
        if variant_id:
            by_variant.setdefault(variant_id, row)
        if sku:
            by_sku.setdefault(sku, row)
    return by_variant, by_sku


def _mingkong_reference_payload(row: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, Any]:
    source = str(fallback.get("source") or "").strip()
    if row is None and source.startswith("mingkong"):
        row = fallback
    if not row:
        return {}
    proc = row.get("mingkong_procurement") or {}
    purchase_url = str(row.get("purchase_1688_url") or proc.get("purchase_1688_url") or "").strip()
    return {
        "shopify_product_id": row.get("shopify_product_id") or "",
        "shopify_variant_id": row.get("shopify_variant_id") or "",
        "variant_title": row.get("variant_title") or "",
        "sku": row.get("dianxiaomi_sku") or "",
        "product_sku": row.get("dianxiaomi_product_sku") or "",
        "sku_code": row.get("dianxiaomi_sku_code") or "",
        "name": row.get("dianxiaomi_name") or "",
        "image_url": row.get("image_url") or "",
        "supplier_name": proc.get("supplier_name") or "",
        "purchase_1688_url": purchase_url,
        "alibaba_product_id": proc.get("alibaba_product_id") or normalize_1688_offer_id(purchase_url),
        "sku_id_alibaba": proc.get("sku_id_alibaba") or "",
        "pairing_state": proc.get("pairing_state"),
        "is_combo": bool(row.get("is_combo")),
        "combo_components": row.get("combo_components") or [],
        "source": row.get("source") or source,
    }


def build_workbench_payload(
    product: dict[str, Any],
    sku_rows: list[dict[str, Any]],
    *,
    include_live: bool = True,
    include_mingkong_reference: bool = False,
    fetch_live_fn=fetch_dxm03_pairing_snapshot,
) -> dict[str, Any]:
    purchase_url = str(product.get("purchase_1688_url") or "").strip()
    source_rows = list(sku_rows or [])
    library_rows: list[dict[str, Any]] = []
    base_rows: list[dict[str, Any]] = []
    realtime_refresh_error = ""
    mingkong_reference_error = ""
    full_sku_base_error = ""
    realtime_refresh_summary: dict[str, Any] | None = None
    try:
        base_rows = mingkong_product_library.public_shopify_sku_rows_from_product(product)
    except Exception as exc:
        full_sku_base_error = str(exc)
    if not source_rows:
        try:
            loaded_library = load_mingkong_library_sku_rows(product)
            library_rows = loaded_library["rows"]
            realtime_refresh_summary = loaded_library["realtime_refresh"]
            source_rows = library_rows
        except Exception as exc:
            realtime_refresh_error = str(exc)
            library_rows = []
    elif include_mingkong_reference:
        try:
            library_rows = [
                _local_sku_payload(row)
                for row in mingkong_product_library.sku_rows_from_library(product)
            ]
        except Exception as exc:
            mingkong_reference_error = str(exc)
            library_rows = []
    if base_rows and source_rows:
        local_rows = merge_full_sku_base_with_fill_rows(
            base_rows,
            [_local_sku_payload(row) for row in source_rows],
            library_rows,
        )
    else:
        local_rows = [_local_sku_payload(row) for row in source_rows]
    mingkong_by_variant, mingkong_by_sku = _mingkong_reference_indexes(library_rows)
    sku_values = [row["dianxiaomi_sku"] for row in local_rows if row.get("dianxiaomi_sku")]
    live_error = ""
    snapshot: dict[str, dict[str, Any]] = {}
    if include_live and sku_values:
        try:
            snapshot = fetch_live_fn(sku_values)
        except Exception as exc:
            live_error = str(exc)
    items: list[dict[str, Any]] = []
    ready_count = 0
    paired_count = 0
    for row in local_rows:
        sku = row.get("dianxiaomi_sku") or ""
        live = snapshot.get(sku) or {}
        commodity = live.get("commodity")
        pairing = live.get("pairing")
        components = live.get("combo_components") or []
        mingkong_reference = (
            mingkong_by_variant.get(str(row.get("shopify_variant_id") or "").strip())
            or mingkong_by_sku.get(sku)
        )
        row_purchase_url = str(row.get("purchase_1688_url") or "").strip()
        row_alibaba_product_id = normalize_1688_offer_id(row_purchase_url)
        status = "missing_local_sku"
        if sku and commodity and commodity.get("is_combo") and components:
            all_components_paired = all(
                (component.get("pairing") or {}).get("is_paired")
                for component in components
            )
            if all_components_paired:
                status = "combo_components_paired"
                paired_count += 1
            else:
                status = "combo_components_incomplete"
        elif sku and commodity and pairing and pairing.get("is_paired"):
            status = "paired"
            paired_count += 1
        elif sku and commodity and pairing:
            status = "ready_to_confirm"
            ready_count += 1
        elif sku and commodity:
            status = "missing_pair_row"
        elif sku:
            status = "missing_dxm03_commodity"
        items.append({
            **row,
            "purchase_1688_url": row_purchase_url,
            "alibaba_product_id": row_alibaba_product_id,
            "image_url": row.get("image_url") or (commodity or {}).get("image_url") or "",
            "is_combo": bool((commodity or {}).get("is_combo") or row.get("is_combo")),
            "combo_components": components,
            "mingkong_procurement": row.get("mingkong_procurement"),
            "mingkong": _mingkong_reference_payload(mingkong_reference, row),
            "dxm03": {
                "commodity": commodity,
                "pairing": pairing,
            },
            "status": status,
        })
    return {
        "product": {
            "id": product.get("id"),
            "product_code": product.get("product_code") or "",
            "name": product.get("name") or "",
            "shopifyid": product.get("shopifyid") or "",
            "shopify_title": product.get("shopify_title") or "",
            "product_link": product.get("product_link") or "",
            "purchase_1688_url": purchase_url,
            "alibaba_product_id": normalize_1688_offer_id(purchase_url),
        },
        "items": items,
        "summary": {
            "sku_count": len(items),
            "ready_count": ready_count,
            "paired_count": paired_count,
            "missing_count": len(items) - ready_count - paired_count,
            "has_purchase_url": bool(purchase_url),
            "live_error": live_error or realtime_refresh_error,
            "full_sku_base_error": full_sku_base_error,
            "mingkong_reference_error": mingkong_reference_error,
            "source": (
                "shopify_public_base"
                if base_rows
                else ("media_product_skus" if sku_rows else ("mingkong_library" if library_rows else "empty"))
            ),
            "realtime_refresh": realtime_refresh_summary,
        },
    }


def _sku_pair_for_replace(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "shopify_product_id": row.get("shopify_product_id"),
        "shopify_variant_id": row.get("shopify_variant_id"),
        "shopify_sku": row.get("shopify_sku"),
        "shopify_price": row.get("shopify_price"),
        "shopify_compare_at_price": row.get("shopify_compare_at_price"),
        "shopify_currency": row.get("shopify_currency") or "USD",
        "shopify_inventory_quantity": row.get("shopify_inventory_quantity"),
        "shopify_weight_grams": row.get("shopify_weight_grams"),
        "shopify_variant_title": row.get("shopify_variant_title") or row.get("variant_title"),
        "dianxiaomi_sku": row.get("dianxiaomi_sku"),
        "dianxiaomi_product_sku": row.get("dianxiaomi_product_sku"),
        "dianxiaomi_sku_code": row.get("dianxiaomi_sku_code"),
        "dianxiaomi_name": row.get("dianxiaomi_name"),
    }


def _purchase_url_for_selection(
    product: dict[str, Any],
    row: dict[str, Any],
    selection: dict[str, Any],
    source_commodity: dict[str, Any] | None,
) -> str:
    selected_product_id = (
        normalize_1688_offer_id(selection.get("product_id_alibaba"))
        or normalize_1688_offer_id(selection.get("purchase_1688_url"))
    )
    if selected_product_id:
        return _purchase_url_for_offer(selected_product_id, selection.get("purchase_1688_url") or "")
    return (
        str(selection.get("purchase_1688_url") or "").strip()
        or str(product.get("purchase_1688_url") or "").strip()
        or str(row.get("purchase_1688_url") or "").strip()
        or str((source_commodity or {}).get("source_url") or "").strip()
    )


def _wait_for_commodity(ctx, sku: str, *, attempts: int = 5) -> dict[str, Any] | None:
    for index in range(attempts):
        commodity = _search_commodity(ctx, sku)
        if commodity:
            return commodity
        if index < attempts - 1:
            time.sleep(1)
    return None


def replicate_mingkong_skus_to_dxm03(
    product: dict[str, Any],
    sku_rows: list[dict[str, Any]],
    *,
    selections: list[dict[str, Any]] | None = None,
    cdp_url: str | None = None,
    source_cdp_url: str | None = None,
    replace_product_skus_fn=None,
    update_product_fn=None,
    force_isolated_thread: bool | None = None,
) -> dict[str, Any]:
    if (
        force_isolated_thread is None
        and replace_product_skus_fn is None
        and update_product_fn is None
    ):
        return _run_pairing_subprocess(
            "replicate",
            {
                "product": product,
                "sku_rows": sku_rows,
                "selections": selections,
                "cdp_url": cdp_url,
                "source_cdp_url": source_cdp_url,
            },
        )
    return _run_playwright_operation(
        "dxm03_mingkong_sku_replicate",
        lambda: _replicate_mingkong_skus_to_dxm03_impl(
            product,
            sku_rows,
            selections=selections,
            cdp_url=cdp_url,
            source_cdp_url=source_cdp_url,
            replace_product_skus_fn=replace_product_skus_fn,
            update_product_fn=update_product_fn,
        ),
        force_isolated_thread=force_isolated_thread,
    )


def _replicate_mingkong_skus_to_dxm03_impl(
    product: dict[str, Any],
    sku_rows: list[dict[str, Any]],
    *,
    selections: list[dict[str, Any]] | None = None,
    cdp_url: str | None = None,
    source_cdp_url: str | None = None,
    replace_product_skus_fn=None,
    update_product_fn=None,
) -> dict[str, Any]:
    """Create missing DXM03 commodities by cloning safe SKU settings from DXM02."""

    local_rows = [_local_sku_payload(row) for row in sku_rows or []]
    rows_with_sku = [row for row in local_rows if row.get("dianxiaomi_sku")]
    if not rows_with_sku:
        message = "产品缺少可复刻的明空 SKU 行"
        return {
            "ok": False,
            "error": "missing_sku_rows",
            "message": message,
            "logs": [{"level": "error", "message": message}],
            "items": [],
        }
    selection_by_key = _selection_map(selections)
    target_url = cdp_url or dxm03_cdp_url()
    source_url = source_cdp_url or dxm02_cdp_url()
    results: list[dict[str, Any]] = []
    successful_by_sku: dict[str, dict[str, Any]] = {}
    first_purchase_url = ""
    with browser_automation_lock(
        task_code="dxm03_mingkong_sku_replicate",
        timeout_seconds=240,
        command=str(product.get("product_code") or product.get("id") or ""),
    ):
        source_playwright, source_browser, source_ctx = _open_dxm02_context(source_url)
        target_browser = None
        try:
            target_browser, target_ctx = _connect_dxm_context(source_playwright, target_url)
            for row in rows_with_sku:
                sku = str(row.get("dianxiaomi_sku") or "").strip()
                variant_id = str(row.get("shopify_variant_id") or "").strip()
                selection = (
                    selection_by_key.get(sku)
                    or selection_by_key.get(variant_id)
                    or {}
                )
                item_result: dict[str, Any] = {
                    **row,
                    "status": "pending",
                    "original_mingkong_sku_code": row.get("dianxiaomi_sku_code") or "",
                    "dxm03_sku_code": "",
                    "sku_code_strategy": "",
                }
                try:
                    existing_target = _search_commodity(target_ctx, sku)
                    if existing_target:
                        item_result.update({
                            "status": "already_exists",
                            "message": "DXM03 已存在同 SKU，直接复用",
                            "commodity": existing_target,
                            "dxm03_sku_code": existing_target.get("sku_code") or "",
                            "sku_code_strategy": "existing",
                        })
                        successful_by_sku[sku] = item_result
                        results.append(item_result)
                        continue

                    source_commodity = _search_commodity(source_ctx, sku)
                    item_result["source_commodity"] = source_commodity
                    if not source_commodity or not source_commodity.get("id"):
                        item_result.update({
                            "status": "blocked",
                            "error": "missing_dxm02_source",
                            "message": "DXM02 明空商品管理找不到该 SKU，无法复刻",
                        })
                        results.append(item_result)
                        continue
                    if source_commodity.get("is_combo"):
                        source_components = _search_child_sku_info(
                            source_ctx,
                            source_commodity["id"],
                        )
                        target_components: list[dict[str, Any]] = []
                        missing_components: list[str] = []
                        for component in source_components:
                            component_sku = str(component.get("sku") or "").strip()
                            target_component = _search_commodity(target_ctx, component_sku)
                            if not target_component or not target_component.get("id"):
                                missing_components.append(component_sku)
                                continue
                            target_components.append({
                                **component,
                                "target_product_id": target_component["id"],
                                "target_commodity": target_component,
                            })
                        item_result["combo_components"] = target_components
                        if not source_components:
                            item_result.update({
                                "status": "blocked",
                                "error": "missing_combo_components",
                                "message": "DXM02 组合 SKU 未返回组件，无法复刻",
                            })
                            results.append(item_result)
                            continue
                        if missing_components:
                            item_result.update({
                                "status": "blocked",
                                "error": "missing_target_combo_components",
                                "message": "DXM03 缺少组合组件 SKU："
                                + "，".join(missing_components),
                            })
                            results.append(item_result)
                            continue
                        source_detail = _view_commodity_detail(
                            source_ctx,
                            source_commodity["id"],
                            account_label="DXM02",
                        )
                        purchase_url = _purchase_url_for_selection(
                            product,
                            row,
                            selection,
                            source_commodity,
                        )
                        desired_sku_code = (
                            row.get("dianxiaomi_sku_code")
                            or source_commodity.get("sku_code")
                            or _source_commodity_from_detail(source_detail).get("skuCode")
                            or sku
                        )
                        final_sku_code, strategy = _target_sku_code(
                            target_ctx,
                            str(desired_sku_code or ""),
                            target_sku=sku,
                        )
                        form_payload = _replicated_combo_form(
                            source_detail,
                            target_sku=sku,
                            target_sku_code=final_sku_code,
                            target_components=target_components,
                            purchase_url=purchase_url,
                            fallback_name=(
                                row.get("dianxiaomi_name")
                                or source_commodity.get("name")
                                or ""
                            ),
                            fallback_name_en=product.get("shopify_title") or "",
                        )
                        _add_replicated_combo_commodity(target_ctx, form_payload)
                        created = _wait_for_commodity(target_ctx, sku)
                        if not created:
                            item_result.update({
                                "status": "error",
                                "error": "dxm03_combo_create_not_visible",
                                "message": "DXM03 组合创建接口返回成功，但商品管理暂未搜索到该 SKU",
                            })
                            results.append(item_result)
                            continue
                        item_result.update({
                            "status": "created",
                            "message": "已从明空 DXM02 复刻组合 SKU 到 DXM03",
                            "commodity": created,
                            "purchase_1688_url": purchase_url,
                            "dxm03_sku_code": created.get("sku_code") or final_sku_code,
                            "sku_code_strategy": strategy,
                        })
                        if purchase_url and not first_purchase_url:
                            first_purchase_url = purchase_url
                        successful_by_sku[sku] = item_result
                        results.append(item_result)
                        continue

                    source_detail = _view_commodity_detail(
                        source_ctx,
                        source_commodity["id"],
                        account_label="DXM02",
                    )
                    purchase_url = _purchase_url_for_selection(
                        product,
                        row,
                        selection,
                        source_commodity,
                    )
                    desired_sku_code = (
                        row.get("dianxiaomi_sku_code")
                        or source_commodity.get("sku_code")
                        or _source_commodity_from_detail(source_detail).get("skuCode")
                        or sku
                    )
                    final_sku_code, strategy = _target_sku_code(
                        target_ctx,
                        str(desired_sku_code or ""),
                        target_sku=sku,
                    )
                    form_payload = _replicated_commodity_form(
                        source_detail,
                        target_sku=sku,
                        target_sku_code=final_sku_code,
                        purchase_url=purchase_url,
                        fallback_name=row.get("dianxiaomi_name") or source_commodity.get("name") or "",
                        fallback_name_en=product.get("shopify_title") or "",
                    )
                    _add_replicated_commodity(target_ctx, form_payload)
                    created = _wait_for_commodity(target_ctx, sku)
                    if not created:
                        item_result.update({
                            "status": "error",
                            "error": "dxm03_create_not_visible",
                            "message": "DXM03 创建接口返回成功，但商品管理暂未搜索到该 SKU",
                        })
                        results.append(item_result)
                        continue
                    item_result.update({
                        "status": "created",
                        "message": "已从明空 DXM02 复刻到 DXM03",
                        "commodity": created,
                        "purchase_1688_url": purchase_url,
                        "dxm03_sku_code": created.get("sku_code") or final_sku_code,
                        "sku_code_strategy": strategy,
                    })
                    if purchase_url and not first_purchase_url:
                        first_purchase_url = purchase_url
                    successful_by_sku[sku] = item_result
                    results.append(item_result)
                except Exception as exc:
                    item_result.update({
                        "status": "error",
                        "error": "dxm03_replicate_failed",
                        "message": str(exc),
                    })
                    results.append(item_result)
        finally:
            _close_dxm03_context(source_playwright, source_browser)

    if successful_by_sku:
        medias_module = None
        if replace_product_skus_fn is None:
            from appcore import medias

            medias_module = medias
            replace_product_skus_fn = medias.replace_product_skus

        replacement_pairs: list[dict[str, Any]] = []
        for raw_row in sku_rows or []:
            pair = _sku_pair_for_replace(raw_row)
            sku = str(pair.get("dianxiaomi_sku") or "").strip()
            replicated = successful_by_sku.get(sku)
            commodity = (replicated or {}).get("commodity") or {}
            if replicated and commodity:
                pair["dianxiaomi_sku_code"] = (
                    replicated.get("dxm03_sku_code")
                    or commodity.get("sku_code")
                    or pair.get("dianxiaomi_sku_code")
                )
                pair["dianxiaomi_product_sku"] = (
                    commodity.get("product_sku")
                    or pair.get("dianxiaomi_product_sku")
                )
                pair["dianxiaomi_name"] = (
                    commodity.get("name")
                    or pair.get("dianxiaomi_name")
                )
            if pair.get("shopify_variant_id"):
                replacement_pairs.append(pair)
        if replacement_pairs:
            update_summary = replace_product_skus_fn(
                int(product["id"]),
                replacement_pairs,
                source="mingkong_replicated",
            )
        else:
            update_summary = {"inserted": 0, "updated": 0, "deleted": 0, "preserved": 0}
        if first_purchase_url and not str(product.get("purchase_1688_url") or "").strip():
            if update_product_fn is None:
                if medias_module is None:
                    from appcore import medias

                    medias_module = medias
                update_product_fn = medias_module.update_product
            update_product_fn(int(product["id"]), purchase_1688_url=first_purchase_url)
    else:
        update_summary = {"inserted": 0, "updated": 0, "deleted": 0, "preserved": 0}

    ok = bool(results) and all(
        item.get("status") in {"already_exists", "created"}
        for item in results
    )
    summary = {
        "created_count": sum(1 for item in results if item.get("status") == "created"),
        "existing_count": sum(1 for item in results if item.get("status") == "already_exists"),
        "blocked_count": sum(1 for item in results if item.get("status") == "blocked"),
        "error_count": sum(1 for item in results if item.get("status") == "error"),
        "local_update": update_summary,
    }
    message = (
        "复刻明空 SKU 完成："
        f"新建 {summary['created_count']}，"
        f"DXM03 已存在 {summary['existing_count']}，"
        f"阻断 {summary['blocked_count']}，"
        f"失败 {summary['error_count']}"
    )
    return {
        "ok": ok,
        "product_id": product.get("id"),
        "product_code": product.get("product_code") or "",
        "message": message,
        "logs": _operation_logs("复刻明空 SKU 到 DXM03", results),
        "items": results,
        "summary": summary,
    }


def confirm_dxm03_pairing(
    product: dict[str, Any],
    sku_rows: list[dict[str, Any]],
    *,
    selections: list[dict[str, Any]] | None = None,
    cdp_url: str | None = None,
    force_isolated_thread: bool | None = None,
) -> dict[str, Any]:
    return _run_playwright_operation(
        "dxm03_mingkong_pairing_confirm",
        lambda: _confirm_dxm03_pairing_impl(
            product,
            sku_rows,
            selections=selections,
            cdp_url=cdp_url,
        ),
        force_isolated_thread=force_isolated_thread,
    )


def _confirm_dxm03_pairing_impl(
    product: dict[str, Any],
    sku_rows: list[dict[str, Any]],
    *,
    selections: list[dict[str, Any]] | None = None,
    cdp_url: str | None = None,
) -> dict[str, Any]:
    purchase_url = str(product.get("purchase_1688_url") or "").strip()
    product_id_alibaba = normalize_1688_offer_id(purchase_url)
    source_rows = list(sku_rows or [])
    if not source_rows:
        source_rows = load_mingkong_library_sku_rows(product)["rows"]
    local_rows = [_local_sku_payload(row) for row in source_rows]
    rows_with_sku = [row for row in local_rows if row.get("dianxiaomi_sku")]
    if not rows_with_sku:
        message = "产品缺少可写入 DXM03 的 SKU 配对行"
        return {
            "ok": False,
            "error": "missing_sku_rows",
            "message": message,
            "logs": [{"level": "error", "message": message}],
            "items": [],
        }
    selection_by_key = _selection_map(selections)
    url = cdp_url or dxm03_cdp_url()
    results: list[dict[str, Any]] = []
    with browser_automation_lock(
        task_code="dxm03_mingkong_pairing_confirm",
        timeout_seconds=180,
        command=str(product.get("product_code") or product.get("id") or ""),
    ):
        playwright, browser, ctx = _open_dxm03_context(url)
        try:
            for row in rows_with_sku:
                sku = row["dianxiaomi_sku"]
                selection = (
                    selection_by_key.get(sku)
                    or selection_by_key.get(str(row.get("shopify_variant_id") or ""))
                    or {}
                )
                selected_product_id = (
                    normalize_1688_offer_id(selection.get("product_id_alibaba"))
                    or normalize_1688_offer_id(selection.get("purchase_1688_url"))
                    or normalize_1688_offer_id(row.get("purchase_1688_url"))
                    or product_id_alibaba
                )
                selected_purchase_url = _purchase_url_for_offer(
                    selected_product_id,
                    selection.get("purchase_1688_url") or row.get("purchase_1688_url") or purchase_url,
                )
                selected_sku_id = str(selection.get("sku_id_alibaba") or "").strip()
                item_result = {
                    **row,
                    "purchase_1688_url": selected_purchase_url,
                    "alibaba_product_id": selected_product_id,
                    "sku_id_alibaba": selected_sku_id,
                    "status": "pending",
                }
                try:
                    commodity = _search_commodity(ctx, sku)
                    item_result["commodity"] = commodity
                    if not commodity:
                        item_result.update({
                            "status": "blocked",
                            "error": "missing_dxm03_commodity",
                            "message": "DXM03 商品管理找不到该 SKU",
                        })
                        results.append(item_result)
                        continue
                    if commodity.get("is_combo"):
                        components = _search_child_sku_info(ctx, commodity.get("id") or "")
                        for component in components:
                            component["pairing"] = _search_pair(ctx, component["sku"])
                        item_result["combo_components"] = components
                        if components and all(
                            (component.get("pairing") or {}).get("is_paired")
                            for component in components
                        ):
                            item_result.update({
                                "status": "already_paired_combo_components",
                                "message": "组合 SKU 的组件采购配对已完整",
                            })
                        else:
                            item_result.update({
                                "status": "blocked",
                                "error": "combo_components_incomplete",
                                "message": "组合 SKU 需要先补齐组件 SKU 与组件采购配对",
                            })
                        results.append(item_result)
                        continue
                    if not selected_product_id:
                        item_result.update({
                            "status": "blocked",
                            "error": "missing_purchase_url",
                            "message": "产品缺少可识别的 1688 采购链接",
                        })
                        results.append(item_result)
                        continue
                    if (
                        selected_purchase_url
                        and commodity.get("id")
                        and commodity.get("source_url") != selected_purchase_url
                    ):
                        _update_source_url(ctx, commodity["id"], selected_purchase_url)
                        commodity = _search_commodity(ctx, sku) or commodity
                        item_result["commodity"] = commodity
                    pair = _search_pair(ctx, sku)
                    if not pair:
                        _trigger_source_sync(ctx, selected_purchase_url)
                        for _ in range(5):
                            time.sleep(1)
                            pair = _search_pair(ctx, sku)
                            if pair:
                                break
                    item_result["pairing_before"] = pair
                    if not pair or not pair.get("pair_row_id"):
                        item_result.update({
                            "status": "blocked",
                            "error": "missing_pair_row",
                            "message": "DXM03 1688 商品配对列表未生成待配对行",
                        })
                        results.append(item_result)
                        continue
                    existing_sku_id = str(pair.get("sku_id_alibaba") or "").strip()
                    if pair.get("is_paired") and (
                        not selected_sku_id or selected_sku_id == existing_sku_id
                    ):
                        item_result.update({
                            "status": "already_paired",
                            "sku_id_alibaba": existing_sku_id,
                            "pairing_after": pair,
                        })
                        results.append(item_result)
                        continue
                    if not selected_sku_id:
                        item_result.update({
                            "status": "blocked",
                            "error": "missing_sku_id_alibaba",
                            "message": "缺少人工确认的 1688 SKU ID",
                        })
                        results.append(item_result)
                        continue
                    _check_pair(ctx, selected_product_id, selected_sku_id)
                    _confirm_pair(
                        ctx,
                        pair_row_id=pair["pair_row_id"],
                        product_id_alibaba=selected_product_id,
                        sku_id_alibaba=selected_sku_id,
                    )
                    item_result["pairing_after"] = _search_pair(ctx, sku)
                    item_result["status"] = "confirmed"
                    results.append(item_result)
                except Exception as exc:
                    item_result.update({
                        "status": "error",
                        "error": "dxm03_write_failed",
                        "message": str(exc),
                    })
                    results.append(item_result)
        finally:
            _close_dxm03_context(playwright, browser)
    ok = bool(results) and all(
        item.get("status") in {
            "already_paired",
            "already_paired_combo_components",
            "confirmed",
        }
        for item in results
    )
    summary = {
        "confirmed_count": sum(1 for item in results if item.get("status") == "confirmed"),
        "already_paired_count": sum(
            1
            for item in results
            if item.get("status") in {
                "already_paired",
                "already_paired_combo_components",
            }
        ),
        "blocked_count": sum(1 for item in results if item.get("status") == "blocked"),
        "error_count": sum(1 for item in results if item.get("status") == "error"),
    }
    message = (
        "同步明空店小秘SKU完成："
        f"写入 {summary['confirmed_count']}，"
        f"已存在 {summary['already_paired_count']}，"
        f"阻断 {summary['blocked_count']}，"
        f"失败 {summary['error_count']}"
    )
    return {
        "ok": ok,
        "product_id": product.get("id"),
        "product_code": product.get("product_code") or "",
        "message": message,
        "logs": _operation_logs("同步明空店小秘SKU", results),
        "items": results,
        "summary": summary,
    }


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Mingkong pairing Playwright operations.")
    parser.add_argument("--operation", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = _pairing_subprocess_entrypoint(args.operation, payload)
        envelope = {"ok": True, "result": result}
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 - subprocess must preserve error text
        log.exception("mingkong pairing subprocess failed operation=%s", args.operation)
        envelope = {
            "ok": False,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }
        exit_code = 1

    Path(args.output).write_text(
        json.dumps(envelope, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(_main())
