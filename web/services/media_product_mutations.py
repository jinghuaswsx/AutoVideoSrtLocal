"""Service helpers for media product mutations."""

from __future__ import annotations

from dataclasses import dataclass
import re

from flask import jsonify
import pymysql.err

from appcore import link_availability, medias, product_link_domains


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,126}[a-z0-9]$")
_PRODUCT_CODE_SUFFIX = "-rjc"
_PRODUCT_CODE_SUFFIX_ERROR = "Product ID 必须以 -RJC 结尾"

_ROAS_PRODUCT_FIELDS = (
    "purchase_1688_url",
    "purchase_price",
    "packet_cost_estimated",
    "packet_cost_actual",
    "package_length_cm",
    "package_width_cm",
    "package_height_cm",
    "tk_sea_cost",
    "tk_air_cost",
    "tk_sale_price",
    "standalone_price",
    "standalone_shipping_fee",
)


@dataclass(frozen=True)
class ProductMutationResponse:
    payload: dict
    status_code: int


def validate_product_code(code: str) -> tuple[bool, str | None]:
    if not code:
        return False, "产品 ID 必填"
    if not code.endswith(_PRODUCT_CODE_SUFFIX):
        return False, _PRODUCT_CODE_SUFFIX_ERROR
    if not _SLUG_RE.match(code):
        return False, "产品 ID 只能使用小写字母、数字和连字符，长度 3-128，且首尾不能是连字符"
    return True, None


def build_product_create_response(
    body: dict | None,
    *,
    user_id: int,
    validate_product_code_fn=None,
    get_product_by_code_fn=None,
    create_product_fn=None,
) -> ProductMutationResponse:
    body = body or {}
    name = (body.get("name") or "").strip()
    if not name:
        return ProductMutationResponse({"error": "name required"}, 400)

    validate_product_code_fn = validate_product_code_fn or validate_product_code
    get_product_by_code_fn = get_product_by_code_fn or medias.get_product_by_code
    create_product_fn = create_product_fn or medias.create_product

    product_code = (body.get("product_code") or "").strip().lower() or None
    if product_code is not None:
        ok, err = validate_product_code_fn(product_code)
        if not ok:
            return ProductMutationResponse({"error": err}, 400)
        if get_product_by_code_fn(product_code):
            return ProductMutationResponse({"error": "product_code already exists"}, 409)

    product_id = create_product_fn(
        user_id,
        name,
        product_code=product_code,
    )
    return ProductMutationResponse({"id": product_id}, 201)


def build_product_update_response(
    product_id: int,
    product: dict,
    body: dict | None,
    *,
    validate_product_code_fn=None,
    get_product_by_code_fn=None,
    is_valid_language_fn=None,
    update_product_fn=None,
    replace_copywritings_fn=None,
    schedule_material_evaluation_fn=None,
    list_enabled_domain_rows_fn=None,
    list_link_availability_fn=None,
) -> ProductMutationResponse:
    body = body or {}
    validate_product_code_fn = validate_product_code_fn or validate_product_code
    get_product_by_code_fn = get_product_by_code_fn or medias.get_product_by_code
    is_valid_language_fn = is_valid_language_fn or medias.is_valid_language
    update_product_fn = update_product_fn or medias.update_product
    replace_copywritings_fn = replace_copywritings_fn or medias.replace_copywritings
    list_enabled_domain_rows_fn = (
        list_enabled_domain_rows_fn or product_link_domains.resolve_product_page_url_rows
    )
    list_link_availability_fn = (
        list_link_availability_fn or link_availability.list_results
    )

    update_fields: dict = {}

    if "name" in body:
        name = (body.get("name") or "").strip() or product["name"]
        update_fields["name"] = name

    if "product_code" in body:
        product_code = (body.get("product_code") or "").strip().lower()
        ok, err = validate_product_code_fn(product_code)
        if not ok:
            return ProductMutationResponse({"error": err}, 400)
        existing = get_product_by_code_fn(product_code)
        if existing and existing["id"] != product_id:
            return ProductMutationResponse({"error": "product_code already exists"}, 409)
        update_fields["product_code"] = product_code

    if "mk_id" in body:
        update_fields["mk_id"] = body.get("mk_id")

    if "shopifyid" in body:
        update_fields["shopifyid"] = body.get("shopifyid")

    for key in (
        "remark",
        "ai_score",
        "ai_evaluation_result",
        "ai_evaluation_detail",
        "listing_status",
    ):
        if key in body:
            update_fields[key] = body.get(key)

    for key in _ROAS_PRODUCT_FIELDS:
        if key in body:
            update_fields[key] = body.get(key)

    if isinstance(body.get("localized_links"), dict):
        cleaned = {}
        for lang, value in body["localized_links"].items():
            normalized_lang = str(lang or "").strip().lower()
            if not is_valid_language_fn(normalized_lang):
                continue
            if isinstance(value, dict):
                domain_links: dict[str, str] = {}
                for raw_domain, raw_url in value.items():
                    url = str(raw_url or "").strip()
                    if not url:
                        continue
                    try:
                        domain = product_link_domains.normalize_domain(str(raw_domain or ""))
                    except ValueError:
                        domain = product_link_domains.domain_from_url(url)
                    if domain:
                        domain_links[domain] = url
                if domain_links:
                    cleaned[normalized_lang] = domain_links
                continue
            url = str(value or "").strip()
            if url:
                cleaned[normalized_lang] = url
        update_fields["localized_links_json"] = cleaned

    if "ad_supported_langs" in body:
        update_fields["ad_supported_langs"] = _clean_ad_supported_langs(
            body.get("ad_supported_langs"),
            is_valid_language_fn=is_valid_language_fn,
        )

        # Precheck only the langs that are NEWLY added compared to the stored
        # value. Skips the check entirely on rename / unchanged / unchecked
        # so old products with later-broken links don't get locked out.
        # Spec: docs/superpowers/specs/2026-05-09-product-edit-ad-supported-langs-precheck-design.md
        old_set = _split_ad_supported_langs(product.get("ad_supported_langs"))
        new_set = _split_ad_supported_langs(update_fields["ad_supported_langs"])
        added = sorted(new_set - old_set)
        if added:
            issues = _check_ad_lang_precheck_issues(
                product=product,
                langs=added,
                list_enabled_domain_rows_fn=list_enabled_domain_rows_fn,
                list_link_availability_fn=list_link_availability_fn,
            )
            if issues:
                return ProductMutationResponse(
                    {
                        "error": "ad_supported_langs_precheck_failed",
                        "issues": issues,
                    },
                    422,
                )

    try:
        update_product_fn(product_id, **update_fields)
    except ValueError as exc:
        return ProductMutationResponse(
            {"error": "invalid_product_field", "message": str(exc)},
            400,
        )
    except pymysql.err.IntegrityError as exc:
        code = exc.args[0] if exc.args else None
        if code == 1062 and "uk_media_products_mk_id" in str(exc):
            return ProductMutationResponse(
                {
                    "error": "mk_id_conflict",
                    "message": "明空 ID 已被其他产品占用",
                },
                409,
            )
        raise

    if (
        schedule_material_evaluation_fn is not None
        and {"name", "product_code", "localized_links_json"} & set(update_fields)
    ):
        schedule_material_evaluation_fn(product_id, force=True)

    copywritings = body.get("copywritings")
    if isinstance(copywritings, dict):
        for lang_code, lang_items in copywritings.items():
            if not is_valid_language_fn(lang_code):
                continue
            if isinstance(lang_items, list):
                replace_copywritings_fn(product_id, lang_items, lang=lang_code)

    return ProductMutationResponse({"ok": True}, 200)


def build_product_delete_response(
    product_id: int,
    *,
    soft_delete_product_fn=None,
) -> ProductMutationResponse:
    soft_delete_product_fn = soft_delete_product_fn or medias.soft_delete_product
    soft_delete_product_fn(product_id)
    return ProductMutationResponse({"ok": True}, 200)


def product_mutation_flask_response(result: ProductMutationResponse):
    return jsonify(result.payload), result.status_code


def _split_ad_supported_langs(raw) -> set[str]:
    if not raw:
        return set()
    if isinstance(raw, (list, tuple, set)):
        parts = [str(value).strip().lower() for value in raw if str(value).strip()]
    else:
        parts = [
            part.strip().lower()
            for part in str(raw).split(",")
            if part.strip()
        ]
    return {p for p in parts if p}


def _check_ad_lang_precheck_issues(
    *,
    product: dict,
    langs: list[str],
    list_enabled_domain_rows_fn,
    list_link_availability_fn,
) -> list[dict]:
    """Return one issue per failing lang.

    A lang fails when:
      - no enabled domain → {"lang": ..., "reason": "no_enabled_domains"}
      - any enabled domain is missing from the availability cache, has empty
        checked_at, or has ok=0 → {"lang": ..., "domains": [{"domain": ..., "reason": ...}]}
    """
    pid = int(product.get("id") or 0)
    issues: list[dict] = []
    for lang in langs:
        domain_rows = list_enabled_domain_rows_fn(product, lang) or []
        if not domain_rows:
            issues.append({"lang": lang, "reason": "no_enabled_domains"})
            continue
        avail_rows = list_link_availability_fn(pid, lang) or []
        avail_by_domain = {item.get("domain"): item for item in avail_rows}
        domain_issues: list[dict] = []
        for row in domain_rows:
            domain = row.get("domain")
            if not domain:
                continue
            entry = avail_by_domain.get(domain)
            if entry is None or not entry.get("checked_at"):
                domain_issues.append({"domain": domain, "reason": "not_checked"})
                continue
            if not entry.get("ok"):
                err = entry.get("error")
                if not err:
                    status = entry.get("http_status")
                    err = f"http {status}" if status else "unavailable"
                domain_issues.append({"domain": domain, "reason": err})
        if domain_issues:
            issues.append({"lang": lang, "domains": domain_issues})
    return issues


def _clean_ad_supported_langs(raw, *, is_valid_language_fn) -> str | None:
    if isinstance(raw, list):
        parts = [str(value).strip().lower() for value in raw if str(value).strip()]
    else:
        parts = [part.strip().lower() for part in str(raw or "").split(",") if part.strip()]

    seen: set[str] = set()
    kept: list[str] = []
    for code in parts:
        if code == "en" or code in seen:
            continue
        if not is_valid_language_fn(code):
            continue
        seen.add(code)
        kept.append(code)
    return ",".join(kept) if kept else None
