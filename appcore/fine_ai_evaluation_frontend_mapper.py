"""Map fine AI evaluation JSON to frontend-friendly cards/charts/tables."""

from __future__ import annotations

from typing import Any


def build_frontend(summary: dict[str, Any], countries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    decision_counts = summary.get("decision_counts") or {}
    cards = [
        _card("summary_metric", "平均分", summary.get("average_score") or 0, "%", _score_severity(summary.get("average_score") or 0)),
        _card("recommendation", "推荐结论", summary.get("overall_recommendation") or "INCOMPLETE_DATA", "", _decision_severity(summary.get("overall_recommendation"))),
        _card("country", "最佳国家", summary.get("best_country") or "-", "", "success"),
        _card("country", "最弱国家", summary.get("worst_country") or "-", "", "warning"),
        _card("summary_metric", "GO 数量", int(decision_counts.get("GO") or 0), "", "success"),
        _card("summary_metric", "TEST 数量", int(decision_counts.get("TEST") or 0), "", "warning"),
        _card("summary_metric", "HOLD 数量", int(decision_counts.get("HOLD") or 0), "", "danger"),
    ]
    completed = [
        item for item in countries.values()
        if isinstance(item, dict) and item.get("status") in {"completed", "failed"}
    ]
    score_bar = []
    radar = []
    overview = []
    competitors = []
    badges = []
    action_items = []

    for item in completed:
        code = item.get("country_code") or ""
        scores = item.get("scores") or {}
        decision = item.get("decision") or {}
        final_decision = decision.get("final_decision") or "HOLD"
        score_bar.append({
            "country_code": code,
            "country_name_zh": item.get("country_name_zh") or "",
            "overall_score": int(scores.get("overall_score") or 0),
            "decision": final_decision,
        })
        radar.append({
            "country_code": code,
            "product_market_fit_score": int(scores.get("product_market_fit_score") or 0),
            "creative_fit_score": int(scores.get("creative_fit_score") or 0),
            "pricing_score": int(scores.get("pricing_score") or 0),
            "landing_page_fit_score": int(scores.get("landing_page_fit_score") or 0),
            "operational_fit_score": int(scores.get("operational_fit_score") or 0),
        })
        overview.append({
            "country_code": code,
            "country_name_zh": item.get("country_name_zh") or "",
            "overall_score": int(scores.get("overall_score") or 0),
            "decision": final_decision,
            "confidence": decision.get("confidence") or "low",
            "recommended_positioning": (item.get("recommendations") or {}).get("recommended_positioning") or "",
            "top_risk": _first_risk(item),
            "top_action": _first_action(item),
        })
        badges.append({
            "country_code": code,
            "label": final_decision,
            "severity": _decision_severity(final_decision),
        })
        for competitor in ((item.get("competitor_analysis") or {}).get("competitors") or []):
            competitors.append({
                "country_code": code,
                "name": competitor.get("name") or "",
                "platform": competitor.get("platform") or "",
                "price": competitor.get("price"),
                "currency": competitor.get("currency") or "",
                "url": competitor.get("url") or "",
            })
        action_items.extend(_action_items_for_country(item))

    return {
        "cards": cards,
        "charts": {
            "country_score_bar": score_bar,
            "score_radar": radar,
        },
        "tables": {
            "country_overview": overview,
            "competitors": competitors,
        },
        "badges": badges,
        "action_items": action_items[:30],
    }


def _card(card_type: str, title: str, value: Any, unit: str, severity: str) -> dict[str, Any]:
    return {
        "card_type": card_type,
        "title": title,
        "value": value,
        "unit": unit,
        "severity": severity,
    }


def _score_severity(score: float | int) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "neutral"
    if value >= 75:
        return "success"
    if value >= 60:
        return "warning"
    return "danger"


def _decision_severity(decision: str | None) -> str:
    return {
        "GO": "success",
        "TEST": "warning",
        "HOLD": "danger",
        "INCOMPLETE_DATA": "neutral",
    }.get(str(decision or "").upper(), "neutral")


def _first_risk(item: dict[str, Any]) -> str:
    risks = item.get("risks") or {}
    for group in ("claim_risks", "compliance_risks", "operational_risks", "trust_risks", "localization_risks"):
        values = risks.get(group) or []
        if values:
            return str(values[0])
    error = item.get("error") or {}
    return str(error.get("message") or "")


def _first_action(item: dict[str, Any]) -> str:
    recs = item.get("recommendations") or {}
    for key in ("creative_actions", "landing_page_actions", "ad_test_angles", "audience_suggestions"):
        values = recs.get(key) or []
        if values:
            return str(values[0])
    missing = item.get("missing_data") or []
    return f"补充数据：{missing[0]}" if missing else ""


def _action_items_for_country(item: dict[str, Any]) -> list[dict[str, Any]]:
    code = item.get("country_code") or ""
    out: list[dict[str, Any]] = []
    recs = item.get("recommendations") or {}
    for action in recs.get("creative_actions") or []:
        out.append(_action("medium", code, "creative", "素材动作", action))
    for action in recs.get("landing_page_actions") or []:
        out.append(_action("medium", code, "landing_page", "落地页动作", action))
    for missing in item.get("missing_data") or []:
        out.append(_action("high", code, "data", "补充数据", missing))
    for risk in _risk_values(item)[:2]:
        out.append(_action("high", code, "operations", "处理风险", risk))
    return out


def _action(priority: str, country_code: str, type_: str, title: str, description: Any) -> dict[str, str]:
    return {
        "priority": priority,
        "country_code": country_code,
        "type": type_,
        "title": title,
        "description": str(description or ""),
    }


def _risk_values(item: dict[str, Any]) -> list[str]:
    values: list[str] = []
    risks = item.get("risks") or {}
    for group in ("claim_risks", "compliance_risks", "operational_risks", "trust_risks", "localization_risks"):
        values.extend(str(value) for value in risks.get(group) or [] if str(value or "").strip())
    return values
