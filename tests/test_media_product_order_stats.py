from __future__ import annotations

from datetime import date


def test_product_order_stats_empty_product_ids_skip_query(monkeypatch):
    from appcore import media_product_order_stats as stats

    calls = []
    monkeypatch.setattr(stats, "query", lambda *args, **kwargs: calls.append(args) or [])

    assert stats.get_product_order_stats([]) == {}
    assert calls == []


def test_product_order_stats_aggregates_windows_and_language_rows(monkeypatch):
    from appcore import media_product_order_stats as stats

    captured = {}

    def fake_query(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return [
            {
                "product_id": 10,
                "buyer_country": "DE",
                "business_date": date(2026, 6, 5),
                "order_count": 2,
            },
            {
                "product_id": 10,
                "buyer_country": "DE",
                "business_date": date(2026, 6, 4),
                "order_count": 1,
            },
            {
                "product_id": 10,
                "buyer_country": "FR",
                "business_date": date(2026, 5, 31),
                "order_count": 3,
            },
            {
                "product_id": 10,
                "buyer_country": "US",
                "business_date": date(2026, 5, 7),
                "order_count": 5,
            },
            {
                "product_id": 10,
                "buyer_country": "XX",
                "business_date": date(2026, 6, 5),
                "order_count": 7,
            },
            {
                "product_id": 20,
                "buyer_country": "DE",
                "business_date": date(2026, 6, 5),
                "order_count": 4,
            },
        ]

    monkeypatch.setattr(stats, "query", fake_query)

    result = stats.get_product_order_stats([20, 10, 10], today=date(2026, 6, 5))

    assert result[10]["total"] == {
        "today": 9,
        "yesterday": 1,
        "last_7d": 13,
        "last_30d": 18,
    }
    assert result[10]["by_lang"]["de"] == {
        "today": 2,
        "yesterday": 1,
        "last_7d": 3,
        "last_30d": 3,
    }
    assert result[10]["by_lang"]["fr"] == {
        "today": 0,
        "yesterday": 0,
        "last_7d": 3,
        "last_30d": 3,
    }
    assert result[10]["by_lang"]["en"]["last_30d"] == 5
    assert "xx" not in result[10]["by_lang"]
    assert result[20]["total"]["today"] == 4
    assert result[20]["by_lang"]["de"]["today"] == 4
    assert result[20]["computed_at"] == "2026-06-05"

    assert "FROM order_profit_lines opl" in captured["sql"]
    assert "JOIN dianxiaomi_order_lines dol ON dol.id = opl.dxm_order_line_id" in captured["sql"]
    assert "dol.meta_business_date BETWEEN %s AND %s" in captured["sql"]
    assert "COUNT(DISTINCT NULLIF(TRIM(dol.dxm_package_id), '')) AS order_count" in captured["sql"]
    assert captured["params"] == (10, 20, date(2026, 5, 7), date(2026, 6, 5))
