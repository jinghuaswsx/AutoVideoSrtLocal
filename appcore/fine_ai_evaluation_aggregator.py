"""Code aggregation for five-country fine AI evaluation results."""

from __future__ import annotations

from collections import Counter
from typing import Any


def build_summary(countries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    completed = [
        item for item in countries.values()
        if isinstance(item, dict) and item.get("status") == "completed"
    ]
    ranking = sorted(
        completed,
        key=lambda item: int((item.get("scores") or {}).get("overall_score") or 0),
        reverse=True,
    )
    ranking_rows = []
    for index, item in enumerate(ranking, start=1):
        ranking_rows.append({
            "country_code": item.get("country_code") or "",
            "country_name_zh": item.get("country_name_zh") or "",
            "overall_score": int((item.get("scores") or {}).get("overall_score") or 0),
            "decision": (item.get("decision") or {}).get("final_decision") or "HOLD",
            "rank": index,
        })

    decision_counts = Counter(
        (item.get("decision") or {}).get("final_decision") or "HOLD"
        for item in completed
    )
    average_score = (
        round(sum(row["overall_score"] for row in ranking_rows) / len(ranking_rows), 1)
        if ranking_rows else 0
    )
    top_opportunities = _top_items(completed, ("recommendations", "ad_test_angles"), limit=8)
    top_risks = _risk_items(completed, limit=8)
    common_missing_data = _common_strings(
        item for country in countries.values()
        for item in (country.get("missing_data") or [])
    )
    common_localization_actions = _top_items(
        completed,
        ("recommendations", "landing_page_actions"),
        limit=6,
    )
    next_actions = _build_next_actions(completed, top_risks, common_missing_data)

    return {
        "overall_recommendation": _overall_recommendation(average_score, decision_counts, completed),
        "average_score": average_score,
        "best_country": ranking_rows[0]["country_code"] if ranking_rows else "",
        "worst_country": ranking_rows[-1]["country_code"] if ranking_rows else "",
        "country_ranking": ranking_rows,
        "decision_counts": {
            "GO": int(decision_counts.get("GO") or 0),
            "TEST": int(decision_counts.get("TEST") or 0),
            "HOLD": int(decision_counts.get("HOLD") or 0),
        },
        "top_opportunities": top_opportunities,
        "top_risks": top_risks,
        "common_localization_actions": common_localization_actions,
        "common_missing_data": common_missing_data,
        "next_actions": next_actions,
    }


def _overall_recommendation(
    average_score: float,
    decision_counts: Counter,
    completed: list[dict[str, Any]],
) -> str:
    if not completed:
        return "INCOMPLETE_DATA"
    if decision_counts.get("GO", 0) and average_score >= 75:
        return "GO"
    if decision_counts.get("HOLD", 0) >= max(1, len(completed) // 2 + 1):
        return "HOLD"
    return "TEST"


def _top_items(items: list[dict[str, Any]], path: tuple[str, str], *, limit: int) -> list[str]:
    values: list[str] = []
    for item in items:
        node: Any = item
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        for value in node or []:
            text = str(value or "").strip()
            if text and text not in values:
                values.append(text)
            if len(values) >= limit:
                return values
    return values


def _risk_items(items: list[dict[str, Any]], *, limit: int) -> list[str]:
    out: list[str] = []
    for item in items:
        risks = item.get("risks") or {}
        for group in (
            "claim_risks",
            "compliance_risks",
            "operational_risks",
            "trust_risks",
            "localization_risks",
        ):
            for value in risks.get(group) or []:
                text = str(value or "").strip()
                if text and text not in out:
                    out.append(text)
                if len(out) >= limit:
                    return out
    return out


def _common_strings(values) -> list[str]:
    counts = Counter(str(value or "").strip() for value in values if str(value or "").strip())
    return [text for text, _count in counts.most_common(8)]


def _build_next_actions(
    completed: list[dict[str, Any]],
    top_risks: list[str],
    common_missing_data: list[str],
) -> list[str]:
    actions: list[str] = []
    if common_missing_data:
        actions.append("补齐影响评分准确性的产品/素材/履约数据")
    if top_risks:
        actions.append("优先处理跨国家共性的 claim、合规、信任或履约风险")
    for item in completed:
        decision = (item.get("decision") or {}).get("final_decision")
        if decision in {"GO", "TEST"}:
            country = item.get("country_name_zh") or item.get("country_code")
            actions.append(f"按 {country} 的 30 天测试计划准备首轮素材和落地页")
            break
    return actions[:6]
