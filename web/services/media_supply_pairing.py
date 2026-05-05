"""Service helpers for media supply pairing search responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from appcore import supply_pairing


@dataclass(frozen=True)
class SupplyPairingSearchResponse:
    payload: dict
    status_code: int


def build_supply_pairing_search_response(
    args: Mapping[str, str],
    *,
    search_supply_pairing_fn: Callable[..., dict] = supply_pairing.search_supply_pairing,
    extract_1688_url_fn: Callable[[dict], str | None] = supply_pairing.extract_1688_url,
) -> SupplyPairingSearchResponse:
    query = (args.get("q") or "").strip()
    if not query:
        return SupplyPairingSearchResponse(
            {"error": "missing_query", "message": "请提供 SKU 或关键词"},
            400,
        )

    raw_status = args.get("status")
    status = "" if raw_status is None else str(raw_status)
    try:
        result = search_supply_pairing_fn(query, status=status)
    except Exception as exc:
        return SupplyPairingSearchResponse(
            {"error": "dxm_failed", "message": str(exc)},
            502,
        )

    items = result.get("items") or []
    enriched = []
    for item in items:
        url_1688 = extract_1688_url_fn(item)
        copy = dict(item)
        copy["extracted_1688_url"] = (
            url_1688 if url_1688 and "1688.com" in url_1688 else None
        )
        enriched.append(copy)
    result["items"] = enriched
    return SupplyPairingSearchResponse({"ok": True, **result}, 200)
