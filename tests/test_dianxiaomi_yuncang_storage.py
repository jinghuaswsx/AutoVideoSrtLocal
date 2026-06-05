from decimal import Decimal

from appcore import dianxiaomi_yuncang as mod


SAMPLE_YUNCANG_HTML = """
<table>
  <thead><tr><th>商品信息</th><th>其他</th></tr></thead>
  <tbody>
    <tr class="content">
      <td></td>
      <td>
        <div class="copyDataContentText" data-content="多功能路边安全灯"></div>
        <div class="copyDataContentText" data-content="83527232710"></div>
        <div class="limingcentUrlpic"><span>0513-18188604</span></div>
      </td>
      <td></td><td></td><td></td>
      <td>12</td>
      <td></td><td></td>
      <td>20.15</td>
    </tr>
    <tr class="content">
      <td></td>
      <td>
        <div class="copyDataContentText" data-content="柔软硅胶气球灯"></div>
        <div class="copyDataContentText" data-content="83527215101"></div>
        <div class="limingcentUrlpic"><span>0511-15101221</span></div>
      </td>
      <td></td><td></td><td></td>
      <td>0</td>
      <td></td><td></td>
      <td>39.00</td>
    </tr>
  </tbody>
</table>
"""


def test_parse_yuncang_page_html_extracts_rows():
    rows = mod.parse_yuncang_page_html(SAMPLE_YUNCANG_HTML)

    assert rows == [
        {
            "sku": "0513-18188604",
            "sku_code": "83527232710",
            "goods_name": "多功能路边安全灯",
            "stock_available": 12,
            "unit_price": Decimal("20.15"),
        },
        {
            "sku": "0511-15101221",
            "sku_code": "83527215101",
            "goods_name": "柔软硅胶气球灯",
            "stock_available": 0,
            "unit_price": Decimal("39.00"),
        },
    ]


def test_upsert_skus_preserves_existing_aggregate_columns(monkeypatch):
    calls: list[tuple[str, object]] = []

    class FakeCursor:
        rowcount = 1

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def execute(self, sql, params=None):
            calls.append((sql, params))

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            calls.append(("COMMIT", None))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(mod, "ensure_table", lambda: calls.append(("ensure_table", None)))
    monkeypatch.setattr(mod, "get_conn", lambda: FakeConn())

    summary = mod.upsert_skus([
        {
            "sku": "0513-18188604",
            "sku_code": "83527232710",
            "goods_name": "多功能路边安全灯",
            "stock_available": 12,
            "unit_price": Decimal("20.15"),
        }
    ])

    insert_sql = calls[1][0]
    assert summary == {"rows": 1, "affected": 1}
    assert "INSERT INTO dianxiaomi_yuncang_skus" in insert_sql
    assert "standalone_price_sku" not in insert_sql
    assert "TRUNCATE" not in "\n".join(str(call[0]) for call in calls)


def test_refresh_purchase_price_uses_yuncang_without_old_table(monkeypatch):
    queries: list[str] = []
    executed: list[tuple[str, object]] = []

    def fake_query(sql, params=None):
        queries.append(sql)
        if "FROM media_product_skus" in sql:
            return [{"sku": "0513-18188604"}]
        if "FROM dianxiaomi_order_lines" in sql and "GROUP BY" not in sql:
            return [{"sku": "0511-15101221"}]
        if "FROM dianxiaomi_yuncang_skus" in sql and "unit_price" in sql:
            return [
                {"sku": "0513-18188604", "unit_price": Decimal("20.15")},
                {"sku": "0511-15101221", "unit_price": Decimal("39.00")},
            ]
        if "GROUP BY product_display_sku" in sql:
            return [{"sku": "0513-18188604", "cnt": 3}]
        return []

    monkeypatch.setattr(mod, "query", fake_query)
    monkeypatch.setattr(mod, "execute", lambda sql, params=None: executed.append((sql, params)))

    assert mod._refresh_product_purchase_price(581) == Decimal("20.15")
    assert executed == [("UPDATE media_products SET purchase_price = %s WHERE id = %s", (Decimal("20.15"), 581))]
    assert not any("xmyc_storage_skus" in sql for sql in queries)


def test_refresh_purchase_prices_for_matched_queries_yuncang_products(monkeypatch):
    refreshed: list[int] = []

    def fake_query(sql, params=None):
        assert "dianxiaomi_yuncang_skus" in sql
        assert "xmyc_storage_skus" not in sql
        return [{"product_id": 580}, {"product_id": 581}]

    monkeypatch.setattr(mod, "query", fake_query)
    monkeypatch.setattr(mod, "_refresh_product_purchase_price", lambda product_id: refreshed.append(product_id))

    assert mod.refresh_purchase_prices_for_matched() == {"refreshed": 2}
    assert refreshed == [580, 581]
