"""任务统计：聚合函数 + 路由测试。"""
from datetime import date, datetime

import pytest


# ============================================================
# aggregate_stats_by_owner — 纯函数单测
# ============================================================


def test_aggregate_stats_normalizes_dates_and_passes_half_open_window(monkeypatch):
    """指定区间 → SQL 参数应为 [from_dt 00:00:00, to_dt+1day 00:00:00)。"""
    from appcore import pushes
    captured = {}

    def fake_query(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr("appcore.pushes.query", fake_query)
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    result = pushes.aggregate_stats_by_owner("2026-04-01", "2026-04-26")

    assert captured["params"][0] == datetime(2026, 4, 1, 0, 0, 0)
    assert captured["params"][1] == datetime(2026, 4, 27, 0, 0, 0)  # 半开右开
    assert result["date_from"] == "2026-04-01"
    assert result["date_to"] == "2026-04-26"
    assert result["rows"] == []
    assert result["totals"] == {
        "submitted": 0, "pushed": 0, "unpushed": 0, "push_rate": None,
    }


def test_aggregate_stats_default_dates_are_current_month_to_today(monkeypatch):
    """未传 date_from / date_to → 默认 [本月 1 日, 今天]。"""
    from appcore import pushes
    monkeypatch.setattr("appcore.pushes.query", lambda *a, **k: [])
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    result = pushes.aggregate_stats_by_owner()
    today = date.today()
    assert result["date_from"] == today.replace(day=1).strftime("%Y-%m-%d")
    assert result["date_to"] == today.strftime("%Y-%m-%d")


def test_aggregate_stats_rejects_inverted_range(monkeypatch):
    """date_from > date_to → ValueError。"""
    from appcore import pushes
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )
    with pytest.raises(ValueError):
        pushes.aggregate_stats_by_owner("2026-04-26", "2026-04-01")


def test_aggregate_stats_computes_derived_fields_and_totals(monkeypatch):
    """SQL 返回原始 rows → 函数注入 unpushed / push_rate / 合计。"""
    from appcore import pushes
    fake_rows = [
        {"user_id": 7, "owner_name": "张三", "submitted": 12, "pushed": 8},
        {"user_id": 8, "owner_name": "李四", "submitted": 8, "pushed": 8},
        {"user_id": None, "owner_name": "未指派", "submitted": 3, "pushed": 0},
    ]
    monkeypatch.setattr("appcore.pushes.query", lambda *a, **k: fake_rows)
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )

    result = pushes.aggregate_stats_by_owner("2026-04-01", "2026-04-26")
    assert result["rows"][0] == {
        "user_id": 7, "name": "张三",
        "submitted": 12, "pushed": 8, "unpushed": 4,
        "push_rate": pytest.approx(8 / 12),
    }
    assert result["rows"][1]["push_rate"] == pytest.approx(1.0)
    assert result["rows"][2]["push_rate"] == 0.0
    assert result["totals"]["submitted"] == 23
    assert result["totals"]["pushed"] == 16
    assert result["totals"]["unpushed"] == 7
    assert result["totals"]["push_rate"] == pytest.approx(16 / 23)


def test_aggregate_stats_empty_db_returns_null_rate(monkeypatch):
    """没有任何数据 → 合计推送率 = None（前端显示 —）。"""
    from appcore import pushes
    monkeypatch.setattr("appcore.pushes.query", lambda *a, **k: [])
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "u.username",
    )
    result = pushes.aggregate_stats_by_owner("2026-04-01", "2026-04-26")
    assert result["rows"] == []
    assert result["totals"] == {
        "submitted": 0, "pushed": 0, "unpushed": 0, "push_rate": None,
    }


def test_aggregate_stats_sql_filters_and_uses_owner_expr(monkeypatch):
    """SQL 应包含 owner_name_expr、排除 lang='en'、排除 deleted_at。"""
    from appcore import pushes
    captured = {}

    def fake_query(sql, params):
        captured["sql"] = sql
        return []

    monkeypatch.setattr("appcore.pushes.query", fake_query)
    monkeypatch.setattr(
        "appcore.pushes.medias._media_product_owner_name_expr",
        lambda: "COALESCE(NULLIF(TRIM(u.xingming), ''), u.username)",
    )
    pushes.aggregate_stats_by_owner("2026-04-01", "2026-04-26")
    assert "COALESCE(NULLIF(TRIM(u.xingming), ''), u.username)" in captured["sql"]
    assert "i.lang <> 'en'" in captured["sql"]
    assert "i.deleted_at IS NULL" in captured["sql"]
    assert "p.deleted_at IS NULL" in captured["sql"]
    assert "i.created_at >= %s" in captured["sql"]
    assert "i.created_at <  %s" in captured["sql"]
