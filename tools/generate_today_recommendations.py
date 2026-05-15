"""Generate Xuanpin today recommendations from Dianxiaomi Top500 and MK materials."""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from appcore import llm_client, media_video_materials, pushes, today_recommendations
from appcore.db import query, query_one
from web.services.media_mk_selection import normalize_mk_media_path


USE_CASE_CODE = "xuanpin.today_recommendations"
OUTPUT_DIR = REPO_ROOT / "output" / "today_recommendations"
DEFAULT_PROVIDER = "gemini_vertex"
DEFAULT_MODEL = "gemini-3.1-flash-lite"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    text = text.replace("CNY", "").replace("USD", "").replace("$", "").strip()
    multiplier = 1.0
    if "万" in text:
        multiplier = 10000.0
        text = text.replace("万", "")
    elif "千" in text:
        multiplier = 1000.0
        text = text.replace("千", "")
    elif text.lower().endswith("k"):
        multiplier = 1000.0
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return default


def _trim(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _strip_rjc(handle: str) -> str:
    text = (handle or "").strip().lower()
    return re.sub(r"-rjc$", "", text)


def _product_handle(url: str) -> str:
    parsed = urlparse(url or "")
    parts = [part for part in parsed.path.split("/") if part]
    if "products" not in parts:
        return ""
    index = parts.index("products")
    if index + 1 >= len(parts):
        return ""
    return _strip_rjc(parts[index + 1])


def _link_tail(link: str) -> str:
    parsed = urlparse(link or "")
    parts = [part for part in parsed.path.split("/") if part]
    if "products" not in parts:
        return ""
    index = parts.index("products")
    if index + 1 >= len(parts):
        return ""
    return _strip_rjc(parts[index + 1])


def _latest_snapshot_date() -> str:
    row = query_one("SELECT MAX(snapshot_date) AS snapshot_date FROM dianxiaomi_rankings")
    value = (row or {}).get("snapshot_date")
    if not value:
        raise RuntimeError("dianxiaomi_rankings has no snapshots")
    return str(value)[:10]


def _load_rankings(snapshot_date: str, limit: int) -> list[dict[str, Any]]:
    return query(
        "SELECT id, product_id, product_name, product_url, sales_count, order_count, "
        "       revenue_main, rank_position "
        "FROM dianxiaomi_rankings "
        "WHERE snapshot_date=%s "
        "ORDER BY rank_position ASC LIMIT %s",
        (snapshot_date, int(limit)),
    )


def _enabled_country_codes() -> list[str]:
    rows = query(
        "SELECT code FROM media_languages "
        "WHERE enabled=1 AND code<>'en' ORDER BY sort_order ASC, code ASC"
    )
    codes = [str(row["code"]).strip().lower() for row in rows if row.get("code")]
    return codes or ["de", "fr", "es", "it"]


def _resolve_billing_user_id(explicit_user_id: int | None = None) -> int:
    if explicit_user_id:
        return int(explicit_user_id)
    row = query_one(
        "SELECT id FROM users "
        "WHERE is_active=1 AND role IN ('superadmin','admin') "
        "ORDER BY CASE WHEN username='admin' THEN 0 WHEN role='superadmin' THEN 1 ELSE 2 END, id ASC "
        "LIMIT 1"
    )
    if not row:
        raise RuntimeError(
            "No active admin user found for AI billing; pass --user-id explicitly."
        )
    return int(row["id"])


def _build_headers() -> dict[str, str]:
    headers = pushes.build_localized_texts_headers()
    if "Authorization" not in headers and "Cookie" not in headers:
        raise RuntimeError("MK credentials missing")
    return headers


def _mk_base_url() -> str:
    return (pushes.get_localized_texts_base_url() or "https://os.wedev.vip").rstrip("/")


def _search_mk_items(
    session: requests.Session,
    *,
    base_url: str,
    headers: dict[str, str],
    handle: str,
    timeout: int,
) -> list[dict[str, Any]]:
    resp = session.get(
        f"{base_url}/api/marketing/medias",
        params={"page": 1, "q": handle, "source": "", "level": "", "show_attention": 0},
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    if data.get("is_guest") is True or str(data.get("message") or "").startswith("登录"):
        raise RuntimeError("MK credentials expired")
    return [item for item in ((data.get("data") or {}).get("items") or []) if isinstance(item, dict)]


def _visible_videos(item: dict[str, Any]) -> list[dict[str, Any]]:
    videos = []
    for raw in item.get("videos") or []:
        if not isinstance(raw, dict) or raw.get("hidden"):
            continue
        path = normalize_mk_media_path(str(raw.get("path") or ""))
        if not path:
            continue
        spends = _as_float(raw.get("spends"))
        videos.append({
            "index": 0,
            "name": _trim(raw.get("name"), 260),
            "path": path,
            "image_path": normalize_mk_media_path(str(raw.get("image_path") or "")),
            "spends": spends,
            "ads_count": _as_int(raw.get("ads_count")),
            "author": _trim(raw.get("author"), 80),
            "upload_time": _trim(raw.get("upload_time"), 64),
            "duration_seconds": _as_float(raw.get("duration_seconds") or raw.get("duration")),
        })
    videos.sort(key=lambda item: (float(item["spends"]), int(item["ads_count"])), reverse=True)
    for index, video in enumerate(videos, start=1):
        video["index"] = index
    return videos


def _item_score(item: dict[str, Any], handle: str, videos: list[dict[str, Any]]) -> float:
    links = item.get("product_links") or []
    exact = any(_link_tail(str(link)) == handle for link in links)
    total_spend = sum(float(video["spends"]) for video in videos)
    total_ads = sum(int(video["ads_count"]) for video in videos)
    return (10000 if exact else 0) + min(total_spend, 200000) / 10 + total_ads * 5 + len(videos) * 20 + _as_int(item.get("id"))


def _select_mk_item(items: list[dict[str, Any]], handle: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    best: tuple[float, dict[str, Any], list[dict[str, Any]]] | None = None
    for item in items:
        videos = _visible_videos(item)
        if not videos:
            continue
        score = _item_score(item, handle, videos)
        if best is None or score > best[0]:
            best = (score, item, videos)
    if not best:
        return None, []
    return best[1], best[2]


def _texts_summary(item: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for text in (item.get("texts") or [])[:3]:
        if not isinstance(text, dict):
            continue
        out.append({
            "lang": _trim(text.get("lang"), 24),
            "title": _trim(text.get("title"), 120),
            "message": _trim(text.get("message"), 220),
            "description": _trim(text.get("description"), 120),
        })
    return out


def _base_score(ranking: dict[str, Any], videos: list[dict[str, Any]]) -> float:
    rank = max(1, _as_int(ranking.get("rank_position"), 999))
    sales = _as_int(ranking.get("sales_count"))
    orders = _as_int(ranking.get("order_count"))
    revenue = _as_float(ranking.get("revenue_main"))
    spend = sum(float(video["spends"]) for video in videos[:5])
    ads = sum(int(video["ads_count"]) for video in videos[:5])
    rank_score = max(0, 520 - rank) * 1.8
    return rank_score + math.log1p(sales) * 120 + math.log1p(orders) * 70 + math.log1p(revenue) * 18 + math.log1p(spend) * 55 + ads * 3


def collect_candidates(args: argparse.Namespace) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
    snapshot_date = args.snapshot_date or _latest_snapshot_date()
    rankings = _load_rankings(snapshot_date, args.source_limit)
    headers = _build_headers()
    base_url = _mk_base_url()
    candidates: list[dict[str, Any]] = []
    existing_english_identity = media_video_materials.existing_english_material_identity()
    stats = {
        "rankings_loaded": len(rankings),
        "mk_searches": 0,
        "mk_no_match": 0,
        "mk_no_video": 0,
        "mk_existing_english_video_excluded": 0,
        "candidates": 0,
    }
    session = requests.Session()
    for index, row in enumerate(rankings, start=1):
        handle = _product_handle(str(row.get("product_url") or ""))
        if not handle:
            stats["mk_no_match"] += 1
            continue
        try:
            items = _search_mk_items(
                session,
                base_url=base_url,
                headers=headers,
                handle=handle,
                timeout=args.timeout_seconds,
            )
            stats["mk_searches"] += 1
        except Exception as exc:
            print(f"[warn] mk search failed rank={row.get('rank_position')} handle={handle}: {exc}", flush=True)
            stats["mk_no_match"] += 1
            continue
        item, videos = _select_mk_item(items, handle)
        if not item:
            stats["mk_no_video"] += 1
            continue
        before_filter = len(videos)
        videos = [
            video for video in videos
            if not media_video_materials.is_existing_english_material(
                video_path=video.get("path"),
                video_name=video.get("name"),
                identity=existing_english_identity,
            )
        ]
        stats["mk_existing_english_video_excluded"] += before_filter - len(videos)
        if not videos:
            stats["mk_no_video"] += 1
            continue
        videos = videos[: args.max_materials_per_product]
        candidate = {
            "product_key": handle,
            "product_handle": handle,
            "shopify_product_id": str(row.get("product_id") or ""),
            "product_name": _trim(row.get("product_name"), 360),
            "product_url": str(row.get("product_url") or ""),
            "rank_position": _as_int(row.get("rank_position")),
            "sales_count": _as_int(row.get("sales_count")),
            "order_count": _as_int(row.get("order_count")),
            "revenue_main": str(row.get("revenue_main") or ""),
            "mk_product_id": item.get("id"),
            "mk_product_name": _trim(item.get("product_name"), 260),
            "mk_total_spends": sum(float(video["spends"]) for video in videos),
            "mk_total_ads": sum(int(video["ads_count"]) for video in videos),
            "mk_video_count": len(_visible_videos(item)),
            "texts": _texts_summary(item),
            "videos": videos,
            "base_score": round(_base_score(row, videos), 2),
            "main_image": item.get("main_image") or item.get("image") or "",
        }
        candidates.append(candidate)
        if args.request_delay_seconds:
            time.sleep(float(args.request_delay_seconds))
        if index % 25 == 0:
            print(f"[collect] processed={index} candidates={len(candidates)}", flush=True)
    candidates.sort(key=lambda item: float(item["base_score"]), reverse=True)
    stats["candidates"] = len(candidates)
    return snapshot_date, candidates, stats


def _recommendation_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "product_key": {"type": "string"},
                        "overall_score": {"type": "number"},
                        "countries": {"type": "array", "items": {"type": "string"}},
                        "video_indexes": {"type": "array", "items": {"type": "integer"}},
                        "reason": {"type": "string"},
                        "risk_flags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["product_key", "overall_score", "countries", "video_indexes", "reason"],
                },
            }
        },
        "required": ["recommendations"],
    }


def _json_from_llm_result(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("json")
    if isinstance(raw, dict):
        return raw
    text = raw if isinstance(raw, str) else result.get("text")
    if isinstance(text, str):
        try:
            return json.loads(text)
        except ValueError:
            match = re.search(r"\{.*\}", text, re.S)
            if match:
                return json.loads(match.group(0))
    raise ValueError("LLM result is not valid JSON")


def _llm_prompt(
    *,
    candidates: list[dict[str, Any]],
    countries: list[str],
    limit: int,
    final_pass: bool,
) -> str:
    compact = []
    for item in candidates:
        compact.append({
            "product_key": item["product_key"],
            "rank": item["rank_position"],
            "sales": item["sales_count"],
            "orders": item["order_count"],
            "revenue": item["revenue_main"],
            "product_name": item["product_name"],
            "mk_name": item["mk_product_name"],
            "base_score": item.get("base_score"),
            "texts": item.get("texts", []),
            "videos": [
                {
                    "index": video["index"],
                    "name": video["name"],
                    "spend": video["spends"],
                    "ads": video["ads_count"],
                    "author": video["author"],
                }
                for video in item.get("videos", [])
            ],
        })
    mode = "final selection" if final_pass else "batch screening"
    return (
        f"You are selecting ecommerce short-video materials for today's Xuanpin recommendation library. "
        f"This is {mode}. Evaluate products and their listed videos using sales momentum, material clarity, "
        f"creative freshness, country fit, and obvious compliance/category risk. "
        f"Allowed country codes: {', '.join(countries)}. "
        f"Return up to {limit} product recommendations. Use product_key exactly as provided. "
        "video_indexes must refer to the candidate's video index values, picking the best one to five videos. "
        "Prefer products with strong sales plus reusable material. Avoid products that look saturated, overly sensitive, "
        "medical-risky, or impossible to localize. Output JSON only.\n\n"
        f"Candidates:\n{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}"
    )


def _call_llm(
    *,
    candidates: list[dict[str, Any]],
    countries: list[str],
    limit: int,
    final_pass: bool,
    provider: str,
    model: str,
    user_id: int | None,
    project_id: str,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    result = llm_client.invoke_generate(
        USE_CASE_CODE,
        prompt=_llm_prompt(candidates=candidates, countries=countries, limit=limit, final_pass=final_pass),
        system="Return strict JSON for product recommendation selection.",
        response_schema=_recommendation_schema(),
        temperature=0.18,
        max_output_tokens=8192,
        provider_override=provider,
        model_override=model,
        user_id=user_id,
        project_id=project_id,
    )
    payload = _json_from_llm_result(result)
    rows = payload.get("recommendations") or []
    return [row for row in rows if isinstance(row, dict)]


def _fallback_countries(product_name: str, countries: list[str]) -> list[str]:
    text = product_name.lower()
    preferred = ["de", "fr", "es", "it"]
    if any(word in text for word in ("car", "tool", "anchor", "screw", "window", "garden", "hose")):
        preferred = ["de", "fr", "nl", "sv"]
    elif any(word in text for word in ("hair", "beauty", "hat", "clip", "chain", "flower")):
        preferred = ["de", "fr", "it", "es"]
    elif any(word in text for word in ("toy", "water", "pet", "organizer")):
        preferred = ["de", "fr", "es", "pt"]
    chosen = [code for code in preferred if code in countries]
    return chosen[:4] or countries[:4]


def _fallback_recommendations(candidates: list[dict[str, Any]], countries: list[str], limit: int) -> list[dict[str, Any]]:
    rows = []
    for item in sorted(candidates, key=lambda row: float(row.get("base_score") or 0), reverse=True)[:limit]:
        rows.append({
            "product_key": item["product_key"],
            "overall_score": min(99.0, max(50.0, float(item.get("base_score") or 0) / 18)),
            "countries": _fallback_countries(item["product_name"], countries),
            "video_indexes": [video["index"] for video in item.get("videos", [])[:5]],
            "reason": "销量排名、素材消耗和广告数综合靠前，适合先进入人工复核。",
            "risk_flags": ["llm_fallback"],
        })
    return rows


def _ensure_full_material_capacity(
    recommendations: list[dict[str, Any]],
    *,
    candidates: list[dict[str, Any]],
    countries: list[str],
    target_products: int,
    max_materials_per_product: int,
) -> list[dict[str, Any]]:
    candidate_by_key = {item["product_key"]: item for item in candidates}
    enough_video_keys = {
        item["product_key"]
        for item in candidates
        if len(item.get("videos") or []) >= max_materials_per_product
    }
    kept: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec in recommendations:
        key = str(rec.get("product_key") or "")
        if not key or key in seen or key not in candidate_by_key:
            continue
        seen.add(key)
        if key in enough_video_keys:
            kept.append(rec)
        else:
            deferred.append(rec)
    for item in sorted(candidates, key=lambda row: float(row.get("base_score") or 0), reverse=True):
        if len(kept) >= target_products:
            break
        key = item["product_key"]
        if key in seen or key not in enough_video_keys:
            continue
        kept.extend(_fallback_recommendations([item], countries, 1))
        seen.add(key)
    for rec in deferred:
        if len(kept) >= target_products:
            break
        kept.append(rec)
    for item in sorted(candidates, key=lambda row: float(row.get("base_score") or 0), reverse=True):
        if len(kept) >= target_products:
            break
        key = item["product_key"]
        if key in seen:
            continue
        kept.extend(_fallback_recommendations([item], countries, 1))
        seen.add(key)
    return kept[:target_products]


def select_recommendations(
    args: argparse.Namespace,
    *,
    candidates: list[dict[str, Any]],
    countries: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if args.no_llm:
        return _fallback_recommendations(candidates, countries, args.target_products), {"mode": "fallback_no_llm"}

    batch_winners: list[dict[str, Any]] = []
    errors: list[str] = []
    for offset in range(0, len(candidates), args.llm_batch_size):
        chunk = candidates[offset: offset + args.llm_batch_size]
        try:
            recs = _call_llm(
                candidates=chunk,
                countries=countries,
                limit=args.batch_pick_limit,
                final_pass=False,
                provider=args.provider,
                model=args.model,
                user_id=args.user_id,
                project_id=f"today-recommendations-batch-{offset // args.llm_batch_size + 1}",
            )
            batch_winners.extend(recs)
            print(f"[llm] batch={offset // args.llm_batch_size + 1} winners={len(recs)}", flush=True)
        except Exception as exc:
            message = str(exc)
            errors.append(message)
            print(f"[warn] llm batch failed offset={offset}: {message}", flush=True)

    candidate_by_key = {item["product_key"]: item for item in candidates}
    finalist_keys = []
    for rec in batch_winners:
        key = str(rec.get("product_key") or "")
        if key in candidate_by_key and key not in finalist_keys:
            finalist_keys.append(key)
    finalist_candidates = [candidate_by_key[key] for key in finalist_keys]
    if len(finalist_candidates) < args.target_products:
        for item in candidates:
            if item["product_key"] not in finalist_keys:
                finalist_candidates.append(item)
                finalist_keys.append(item["product_key"])
            if len(finalist_candidates) >= max(args.target_products, args.llm_batch_size):
                break

    try:
        final_recs = _call_llm(
            candidates=finalist_candidates[: max(args.target_products * 3, args.llm_batch_size)],
            countries=countries,
            limit=args.target_products,
            final_pass=True,
            provider=args.provider,
            model=args.model,
            user_id=args.user_id,
            project_id="today-recommendations-final",
        )
        mode = "llm"
    except Exception as exc:
        errors.append(str(exc))
        final_recs = _fallback_recommendations(candidates, countries, args.target_products)
        mode = "fallback_after_llm_error"
    if len(final_recs) < args.target_products:
        selected_keys = {str(item.get("product_key") or "") for item in final_recs}
        filler_source = [
            item for item in candidates
            if item["product_key"] not in selected_keys
        ]
        fillers = _fallback_recommendations(
            filler_source,
            countries,
            args.target_products - len(final_recs),
        )
        final_recs.extend(fillers)
    final_recs = _ensure_full_material_capacity(
        final_recs,
        candidates=candidates,
        countries=countries,
        target_products=args.target_products,
        max_materials_per_product=getattr(args, "max_materials_per_product", 5),
    )
    return final_recs, {"mode": mode, "batch_winners": len(batch_winners), "errors": errors[:10]}


def build_rows(
    *,
    selected: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    ranking_snapshot_date: str,
    max_materials_per_product: int,
) -> list[dict[str, Any]]:
    by_key = {item["product_key"]: item for item in candidates}
    rows: list[dict[str, Any]] = []
    seen_products: set[str] = set()
    product_rank = 0
    for rec in selected:
        key = str(rec.get("product_key") or "")
        if key in seen_products or key not in by_key:
            continue
        candidate = by_key[key]
        seen_products.add(key)
        product_rank += 1
        index_set = []
        for raw_index in rec.get("video_indexes") or []:
            index = _as_int(raw_index)
            if index and index not in index_set:
                index_set.append(index)
        videos_by_index = {int(video["index"]): video for video in candidate.get("videos", [])}
        chosen_videos = [videos_by_index[index] for index in index_set if index in videos_by_index]
        if not chosen_videos:
            chosen_videos = list(candidate.get("videos", []))
        chosen_paths = {str(video.get("path") or "") for video in chosen_videos}
        for video in candidate.get("videos", []):
            if len(chosen_videos) >= max_materials_per_product:
                break
            path = str(video.get("path") or "")
            if path in chosen_paths:
                continue
            chosen_videos.append(video)
            chosen_paths.add(path)
        for material_rank, video in enumerate(chosen_videos[:max_materials_per_product], start=1):
            product_key = candidate["product_key"]
            row = {
                "candidate_key": today_recommendations.candidate_key_for(product_key, video.get("path"), video.get("name")),
                "product_recommendation_rank": product_rank,
                "material_rank": material_rank,
                "overall_score": round(float(rec.get("overall_score") or candidate.get("base_score") or 0), 2),
                "product_key": product_key,
                "product_handle": candidate.get("product_handle"),
                "shopify_product_id": candidate.get("shopify_product_id"),
                "product_name": candidate.get("product_name"),
                "product_url": candidate.get("product_url"),
                "sales_count": candidate.get("sales_count"),
                "order_count": candidate.get("order_count"),
                "revenue_main": candidate.get("revenue_main"),
                "rank_position": candidate.get("rank_position"),
                "mk_product_id": candidate.get("mk_product_id"),
                "mk_product_name": candidate.get("mk_product_name"),
                "mk_total_spends": candidate.get("mk_total_spends"),
                "mk_total_ads": candidate.get("mk_total_ads"),
                "mk_video_count": candidate.get("mk_video_count"),
                "video_name": video.get("name"),
                "video_path": video.get("path"),
                "video_image_path": video.get("image_path"),
                "video_spends": video.get("spends"),
                "video_ads_count": video.get("ads_count"),
                "video_author": video.get("author"),
                "video_upload_time": video.get("upload_time"),
                "video_duration_seconds": video.get("duration_seconds"),
                "recommended_countries": [str(code).strip().lower() for code in (rec.get("countries") or []) if str(code).strip()],
                "ai_reason": _trim(rec.get("reason"), 1000),
                "ai_detail": {
                    "ranking_snapshot_date": ranking_snapshot_date,
                    "risk_flags": rec.get("risk_flags") or [],
                    "base_score": candidate.get("base_score"),
                    "texts": candidate.get("texts") or [],
                },
                "mk_video_metadata": {
                    "mp4_url": "",
                    "filename": video.get("name"),
                    "duration_seconds": video.get("duration_seconds"),
                    "cover_url": "",
                    "video_path": video.get("path"),
                    "cover_path": video.get("image_path"),
                    "product_name": candidate.get("mk_product_name") or candidate.get("product_name"),
                    "product_link": candidate.get("product_url"),
                    "main_image": candidate.get("main_image"),
                    "product_code": candidate.get("product_handle") or candidate.get("product_key"),
                    "mk_id": candidate.get("mk_product_id"),
                },
            }
            rows.append(row)
    return rows


def _write_report(payload: dict[str, Any]) -> str:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = OUTPUT_DIR / f"today-recommendations-{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return str(path)


def run(args: argparse.Namespace) -> dict[str, Any]:
    today_recommendations.guard_against_windows_local_mysql()
    if args.apply_migrations:
        from appcore import db_migrations

        db_migrations.ensure_up_to_date()

    recommendation_date = args.recommendation_date or date.today().isoformat()
    args.user_id = _resolve_billing_user_id(args.user_id)
    snapshot_date, candidates, collect_stats = collect_candidates(args)
    countries = _enabled_country_codes()
    run_id = today_recommendations.create_run(
        recommendation_date=recommendation_date,
        ranking_snapshot_date=snapshot_date,
        source_limit=args.source_limit,
        target_products=args.target_products,
        target_materials=args.target_products * args.max_materials_per_product,
        ai_provider=args.provider,
        ai_model=args.model,
    )
    try:
        selected, ai_stats = select_recommendations(args, candidates=candidates, countries=countries)
        rows = build_rows(
            selected=selected,
            candidates=candidates,
            ranking_snapshot_date=snapshot_date,
            max_materials_per_product=args.max_materials_per_product,
        )
        stored = today_recommendations.replace_recommendations(
            run_id=run_id,
            recommendation_date=recommendation_date,
            ranking_snapshot_date=snapshot_date,
            rows=rows,
        )
        summary = {
            "recommendation_date": recommendation_date,
            "ranking_snapshot_date": snapshot_date,
            "countries": countries,
            "collect": collect_stats,
            "ai": ai_stats,
            "selected_products": len({row["product_key"] for row in rows}),
            "selected_materials": len(rows),
            "stored": stored,
        }
        output_file = _write_report({"summary": summary, "rows": rows})
        today_recommendations.finish_run(run_id, status="success", summary=summary, output_file=output_file)
        return {"run_id": run_id, "output_file": output_file, **summary}
    except Exception as exc:
        summary = {"collect": collect_stats, "error": str(exc)}
        output_file = _write_report(summary)
        today_recommendations.finish_run(
            run_id,
            status="failed",
            summary=summary,
            output_file=output_file,
            error_message=str(exc),
        )
        raise


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Xuanpin today recommendations.")
    parser.add_argument("--snapshot-date", default="")
    parser.add_argument("--recommendation-date", default="")
    parser.add_argument("--source-limit", type=int, default=500)
    parser.add_argument("--target-products", type=int, default=20)
    parser.add_argument("--max-materials-per-product", type=int, default=5)
    parser.add_argument("--llm-batch-size", type=int, default=40)
    parser.add_argument("--batch-pick-limit", type=int, default=8)
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="User id used for AI usage billing. Defaults to the active admin/superadmin account.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--request-delay-seconds", type=float, default=0.12)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--apply-migrations", action=argparse.BooleanOptionalAction, default=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    summary = run(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
