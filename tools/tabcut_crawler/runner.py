from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from appcore.tabcut_selection import store
from appcore.tabcut_selection.models import normalize_goods_row, normalize_video_row
from appcore.tabcut_selection.scoring import score_candidate

from .client import (
    TabcutApiClient,
    analysis_video_search_payload,
    extract_items,
    extract_total,
    goods_ranking_url,
    video_ranking_url,
)


BEIJING = ZoneInfo("Asia/Shanghai")
DEFAULT_DAYS = 30
VIDEO_PAGE_SIZE = 100
VIDEO_PAGES_PER_SOURCE = 10
GOODS_PAGE_SIZE = 100
GOODS_PAGES_PER_DAY = 5
VIDEO_RANK_DAYS = (1, 7, 30)
VIDEO_SORTS = ((10, "play"), (60, "sales"))


@dataclass(frozen=True)
class CrawlSource:
    source: str
    pages: int
    url_for_page: Callable[[int], str]
    kind: str
    biz_date: str | None = None
    page_size: int = 100


def recent_biz_dates(days: int = DEFAULT_DAYS, *, today: date | None = None) -> list[str]:
    today = today or datetime.now(BEIJING).date()
    return [(today - timedelta(days=offset)).strftime("%Y%m%d") for offset in range(1, days + 1)]


def build_recent7_plan(biz_dates: list[str]) -> list[CrawlSource]:
    plan = []
    for rank_day in VIDEO_RANK_DAYS:
        rank_label = f"{rank_day}d"
        for sort, sort_label in VIDEO_SORTS:
            plan.append(
                CrawlSource(
                    f"video_{rank_label}_{sort_label}",
                    VIDEO_PAGES_PER_SOURCE,
                    lambda page, sort=sort, rank_day=rank_day: video_ranking_url(
                        sort=sort,
                        page_no=page,
                        rank_day=rank_day,
                        page_size=VIDEO_PAGE_SIZE,
                    ),
                    "video",
                    page_size=VIDEO_PAGE_SIZE,
                )
            )
    for biz_date in biz_dates:
        plan.append(
            CrawlSource(
                f"goods_daily_{biz_date}",
                GOODS_PAGES_PER_DAY,
                lambda page, biz_date=biz_date: goods_ranking_url(
                    biz_date=biz_date,
                    page_no=page,
                    page_size=GOODS_PAGE_SIZE,
                ),
                "goods",
                biz_date=biz_date,
                page_size=GOODS_PAGE_SIZE,
            )
        )
    return plan


def collect_recent7(
    *,
    cdp_url: str = "http://127.0.0.1:9227",
    output_dir: str | Path | None = None,
    days: int = DEFAULT_DAYS,
    persist: bool = True,
    min_interval_seconds: float = 3.3,
) -> dict[str, Any]:
    biz_dates = recent_biz_dates(days)
    latest_biz_date = _ymd_to_iso(biz_dates[0])
    output_path = Path(output_dir or Path("data") / "tabcut" / f"recent{days}-{datetime.now(BEIJING):%Y%m%d-%H%M%S}")
    output_path.mkdir(parents=True, exist_ok=True)
    api = TabcutApiClient(cdp_url=cdp_url, min_interval_seconds=min_interval_seconds)
    datasets: dict[str, dict[str, Any]] = {}
    request_count = 0

    for source in build_recent7_plan(biz_dates):
        items: list[dict[str, Any]] = []
        pages: list[dict[str, Any]] = []
        for page_no in range(1, source.pages + 1):
            page_items, total = api.fetch_items(source.url_for_page(page_no))
            request_count += 1
            pages.append({"pageNo": page_no, "count": len(page_items), "total": total})
            items.extend(page_items)
            _write_json(output_path / f"{source.source}.json", {"source": source.source, "pages": pages, "items": items})
        datasets[source.source] = {"source": source.source, "kind": source.kind, "biz_date": source.biz_date, "pages": pages, "items": items}

    normalized = _normalize_datasets(datasets, latest_biz_date=latest_biz_date)
    _write_json(output_path / "tabcut_us_recent7_snapshot.json", {"datasets": datasets, "normalized": normalized})
    _write_csv(output_path / "videos_top_recent7.csv", normalized["videos"])
    _write_csv(output_path / "products_daily_recent7.csv", normalized["goods"])
    _write_csv(output_path / "video_candidates_recent7.csv", normalized["candidates"])

    if persist:
        _persist_normalized(normalized)

    summary = {
        "ok": True,
        "output_dir": str(output_path),
        "biz_dates": biz_dates,
        "request_count": request_count,
        "video_count": len(normalized["videos"]),
        "goods_count": len(normalized["goods"]),
        "candidate_count": len(normalized["candidates"]),
    }
    _write_json(output_path / "manifest.json", summary)
    return summary


def collect_analysis_video_search(
    *,
    cdp_url: str = "http://127.0.0.1:9227",
    output_dir: str | Path | None = None,
    video_create_time_begin: str,
    video_create_time_end: str,
    pages: int = 20,
    page_size: int = 100,
    persist: bool = True,
    min_interval_seconds: float = 3.3,
    sort_field: str = "video_sold_count",
) -> dict[str, Any]:
    source = f"analysis_video_search_{sort_field}"
    biz_date = video_create_time_end[:10]
    output_path = Path(output_dir or Path("data") / "tabcut" / f"analysis-video-search-{datetime.now(BEIJING):%Y%m%d-%H%M%S}")
    output_path.mkdir(parents=True, exist_ok=True)
    api = TabcutApiClient(cdp_url=cdp_url, min_interval_seconds=min_interval_seconds)

    items: list[dict[str, Any]] = []
    page_summaries: list[dict[str, Any]] = []
    total = 0
    for page_no in range(1, max(1, pages) + 1):
        payload = analysis_video_search_payload(
            page_no=page_no,
            page_size=page_size,
            sort_field=sort_field,
            video_create_time_begin=video_create_time_begin,
            video_create_time_end=video_create_time_end,
        )
        response = api.request_json(
            "POST",
            "https://www.tabcut.com/api/analysis/video-search/videoListV2",
            json_body=payload,
        )
        page_items = extract_items(response)
        total = extract_total(response, total or len(page_items))
        page_summaries.append({"pageNo": page_no, "count": len(page_items), "total": total})
        items.extend(page_items)
        _write_json(output_path / f"{source}.json", {"source": source, "pages": page_summaries, "items": items})

    normalized = _normalize_analysis_video_search_items(items, biz_date=biz_date, source=source)
    snapshot = {
        "source": source,
        "video_create_time_begin": video_create_time_begin,
        "video_create_time_end": video_create_time_end,
        "pages": page_summaries,
        "total": total,
        "items": items,
        "normalized": normalized,
    }
    _write_json(output_path / "tabcut_analysis_video_search_snapshot.json", snapshot)
    _write_csv(output_path / "analysis_video_search_videos.csv", normalized["videos"])
    _write_csv(output_path / "analysis_video_search_goods.csv", normalized["goods"])
    _write_csv(output_path / "analysis_video_search_candidates.csv", normalized["candidates"])

    if persist:
        _persist_analysis_video_search(normalized)

    summary = {
        "ok": True,
        "output_dir": str(output_path),
        "source": source,
        "request_count": len(page_summaries),
        "total": total,
        "raw_item_count": len(items),
        "video_count": len(normalized["videos"]),
        "goods_count": len(normalized["goods"]),
        "candidate_count": len(normalized["candidates"]),
        "video_create_time_begin": video_create_time_begin,
        "video_create_time_end": video_create_time_end,
    }
    _write_json(output_path / "manifest.json", summary)
    return summary


def import_analysis_video_search_output(output_dir: str | Path) -> dict[str, Any]:
    output_path = Path(output_dir)
    snapshot = json.loads((output_path / "tabcut_analysis_video_search_snapshot.json").read_text(encoding="utf-8"))
    normalized = snapshot["normalized"]
    _persist_analysis_video_search(normalized)
    summary = {
        "ok": True,
        "output_dir": str(output_path),
        "video_count": len(normalized.get("videos") or []),
        "goods_count": len(normalized.get("goods") or []),
        "candidate_count": len(normalized.get("candidates") or []),
    }
    _write_json(output_path / "import_manifest.json", summary)
    return summary


def _normalize_datasets(datasets: dict[str, dict[str, Any]], *, latest_biz_date: str) -> dict[str, list[dict[str, Any]]]:
    videos: list[dict[str, Any]] = []
    goods: list[dict[str, Any]] = []
    goods_by_item: dict[str, dict[str, Any]] = {}
    for source, dataset in datasets.items():
        if dataset["kind"] == "video":
            for row in dataset["items"]:
                normalized = normalize_video_row(row, source_sort=source)
                normalized["biz_date"] = latest_biz_date
                videos.append(normalized)
        elif dataset["kind"] == "goods":
            biz_date = _ymd_to_iso(str(dataset["biz_date"]))
            for row in dataset["items"]:
                normalized = normalize_goods_row(row, source=source)
                normalized["biz_date"] = biz_date
                goods.append(normalized)
                item_id = normalized.get("item_id")
                if item_id:
                    aggregate = goods_by_item.setdefault(item_id, dict(normalized, appeared_days=0, sold_count_7d_sum=0, gmv_7d_sum=0))
                    aggregate["appeared_days"] += 1
                    aggregate["sold_count_7d_sum"] += int(normalized.get("sold_count_period") or 0)
                    aggregate["gmv_7d_sum"] += float(normalized.get("gmv_period") or 0)
                    if (normalized.get("rank_position") or 999999) < (aggregate.get("rank_position") or 999999):
                        aggregate.update(normalized)

    candidates = _build_candidates(videos, goods_by_item, latest_biz_date=latest_biz_date)
    return {"videos": videos, "goods": goods, "candidates": candidates}


def _build_candidates(
    videos: list[dict[str, Any]],
    goods_by_item: dict[str, dict[str, Any]],
    *,
    latest_biz_date: str,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for video in videos:
        video_id = str(video.get("video_id") or "")
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        item_id = str(video.get("primary_item_id") or "")
        goods = goods_by_item.get(item_id, {})
        metrics = {
            **video,
            "goods_sold_count_7d": goods.get("sold_count_7d_sum"),
            "goods_gmv_7d": goods.get("gmv_7d_sum"),
            "goods_sold_count_total": goods.get("sold_count_total"),
            "goods_gmv_total": goods.get("gmv_total"),
            "goods_growth_rate_7d": goods.get("sold_growth_rate_period"),
        }
        scored = score_candidate(metrics)
        candidate = {
            "biz_date": latest_biz_date,
            "region": "US",
            "video_id": video_id,
            "primary_item_id": item_id or None,
            "score": scored["score"],
            "score_parts": scored["parts"],
            "play_count": video.get("play_count"),
            "item_sold_count": video.get("item_sold_count"),
            "video_split_sold_count": video.get("video_split_sold_count"),
            "video_split_gmv": video.get("video_split_gmv"),
            "goods_sold_count_7d": goods.get("sold_count_7d_sum"),
            "goods_gmv_7d": goods.get("gmv_7d_sum"),
            "goods_sold_count_total": goods.get("sold_count_total"),
            "goods_gmv_total": goods.get("gmv_total"),
            "goods_growth_rate_7d": goods.get("sold_growth_rate_period"),
            "category_l1_name": goods.get("category_l1_name"),
            "category_l2_name": goods.get("category_l2_name"),
            "category_l3_name": goods.get("category_l3_name"),
            "candidate_json": {"video": video, "goods": goods},
        }
        candidates.append(candidate)
    return sorted(candidates, key=lambda row: float(row.get("score") or 0), reverse=True)


def _normalize_analysis_video_search_items(
    items: list[dict[str, Any]],
    *,
    biz_date: str,
    source: str,
) -> dict[str, list[dict[str, Any]]]:
    videos: list[dict[str, Any]] = []
    goods: list[dict[str, Any]] = []
    goods_by_item: dict[str, dict[str, Any]] = {}
    seen_goods: set[str] = set()
    for index, row in enumerate(items, start=1):
        video = normalize_video_row(row, source_sort=source)
        video["biz_date"] = biz_date
        video["rank_position"] = video.get("rank_position") or index
        videos.append(video)

        item_id = str(row.get("itemId") or "").strip()
        if item_id and item_id not in seen_goods:
            seen_goods.add(item_id)
            normalized_goods = normalize_goods_row(row, source=source)
            normalized_goods["biz_date"] = biz_date
            goods.append(normalized_goods)
            goods_by_item[item_id] = {
                **normalized_goods,
                "sold_count_7d_sum": normalized_goods.get("sold_count_7d"),
                "gmv_7d_sum": normalized_goods.get("gmv_7d"),
            }

    candidates = _build_candidates(videos, goods_by_item, latest_biz_date=biz_date)
    return {"videos": videos, "goods": goods, "candidates": candidates}


def _persist_normalized(normalized: dict[str, list[dict[str, Any]]]) -> None:
    for video in normalized["videos"]:
        store.upsert_video(video)
        store.upsert_video_snapshot(video)
    for goods in normalized["goods"]:
        store.upsert_goods(goods)
        store.upsert_goods_snapshot(goods)
    for candidate in normalized["candidates"]:
        store.upsert_video_candidate(candidate)


def _persist_analysis_video_search(normalized: dict[str, list[dict[str, Any]]]) -> None:
    for video in normalized["videos"]:
        store.upsert_video(video)
        store.upsert_video_snapshot(video)
    for goods in normalized["goods"]:
        store.upsert_goods(goods)
    for candidate in normalized["candidates"]:
        store.upsert_video_candidate(candidate)


def _ymd_to_iso(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row.keys() if key != "raw"})
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

