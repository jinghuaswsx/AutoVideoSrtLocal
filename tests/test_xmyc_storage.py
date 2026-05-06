from contextlib import contextmanager
from decimal import Decimal

import pytest

from appcore import xmyc_storage as mod


SAMPLE_PAGE_LIST_HTML = """
<input type="hidden" id="pageNo" value="1">
<input type="hidden" id="pageSize" value="200">
<input type="hidden" id="totalSize" value="3">
<input type="hidden" id="totalPage" value="1">
<div class="selectAllBox">
<table class="table_list commodityList">
  <thead><tr><th>x</th><th>商品信息</th><th>仓库</th><th>安全</th><th>在途</th><th>待出库</th><th>可用</th><th>在库</th><th>冻结</th><th>单价</th><th>总价</th><th>滞销</th><th>货架</th><th>时间</th></tr></thead>
  <tbody>
    <tr>
      <td><input class="commoditySingle" type="checkbox" value="11917939"/></td>
      <td><div class="product_item">
        <div class="img_out imgOut"><img src="x"/></div>
        <div>
          <div class="copyThis skuCode">83527156514</div>
          <div class="f_gray_a sku">115-18103480</div>
          <div class="f_gray_b goodsName">求生多功能锤 - 单个装</div>
        </div>
      </div></td>
      <td>小秘云仓-东莞黄江仓</td>
      <td>0</td><td>0</td><td>27</td><td>23</td><td>50</td><td>--</td>
      <td>16.57</td><td>828.5</td><td>--</td><td>E特大盒</td>
      <td>更新：2026-05-04 14:02</td>
    </tr>
    <tr>
      <td><input class="commoditySingle" type="checkbox" value="11916913"/></td>
      <td><div class="product_item"><div class="img_out imgOut"></div>
        <div>
          <div class="copyThis skuCode">83527075155</div>
          <div class="f_gray_a sku">0331-16555368</div>
          <div class="f_gray_b goodsName">全自动水枪 vector 蓝色-1</div>
        </div></div></td>
      <td>小秘云仓-东莞黄江仓</td>
      <td>0</td><td>0</td><td>9</td><td>3</td><td>3</td><td>--</td>
      <td>54.52</td><td>163.56</td><td>--</td><td>F单层两个</td>
      <td>更新：2026-05-04</td>
    </tr>
    <tr>
      <td><input class="commoditySingle" type="checkbox" value="9999"/></td>
      <td><div class="product_item"><div class="img_out imgOut"></div>
        <div>
          <div class="copyThis skuCode">EMPTYPRICE</div>
          <div class="f_gray_a sku">empty-price-sku</div>
          <div class="f_gray_b goodsName">无价商品</div>
        </div></div></td>
      <td>小秘云仓-东莞黄江仓</td>
      <td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>--</td>
      <td>--</td><td>--</td><td>--</td><td>--</td>
      <td>--</td>
    </tr>
  </tbody>
</table>
</div>
"""


def test_parse_page_list_html_extracts_total_and_rows():
    total, rows = mod.parse_page_list_html(SAMPLE_PAGE_LIST_HTML)
    assert total == 3
    assert len(rows) == 3
    first = rows[0]
    assert first["xmyc_id"] == "11917939"
    assert first["sku_code"] == "83527156514"
    assert first["sku"] == "115-18103480"
    assert "求生多功能锤" in first["goods_name"]
    assert first["warehouse"] == "小秘云仓-东莞黄江仓"
    assert first["stock_available"] == 50
    assert first["unit_price"] == Decimal("16.57")
    assert first["shelf_code"] == "E特大盒"
    assert first["image_url"] == "https://www.xmyc.com/x"


def test_parse_page_list_html_handles_empty_price():
    _, rows = mod.parse_page_list_html(SAMPLE_PAGE_LIST_HTML)
    third = rows[2]
    assert third["sku"] == "empty-price-sku"
    assert third["unit_price"] is None
    assert third["stock_available"] == 0


def test_parse_page_list_html_returns_empty_for_blank():
    total, rows = mod.parse_page_list_html("")
    assert total is None
    assert rows == []
    total, rows = mod.parse_page_list_html("<html><body>nothing</body></html>")
    assert rows == []


def test_to_decimal_handles_dashes():
    assert mod._to_decimal("16.57") == Decimal("16.57")
    assert mod._to_decimal("--") is None
    assert mod._to_decimal("") is None
    assert mod._to_decimal(None) is None
    assert mod._to_decimal("garbage") is None


def test_to_int_strips_commas():
    assert mod._to_int("1,234") == 1234
    assert mod._to_int("--") is None
    assert mod._to_int(None) is None


class _FakeCursor:
    def __init__(self, store):
        self._store = store
        self.rowcount = 0
        self.last_sql = None
        self.last_params = None

    def __enter__(self): return self
    def __exit__(self, *a): pass

    def execute(self, sql, params=None):
        self.last_sql = sql
        self.last_params = params
        self._store["calls"].append({"sql": sql, "params": params})
        # simulate rowcount based on simple matching
        if sql.strip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            self.rowcount = self._store.get("force_rowcount", 1)


class _FakeConn:
    def __init__(self, store):
        self._store = store

    def cursor(self): return _FakeCursor(self._store)

    def commit(self): self._store["committed"] = True

    def __enter__(self): return self
    def __exit__(self, *a): pass


def test_set_product_skus_clears_then_attaches(monkeypatch):
    store: dict = {"calls": [], "force_rowcount": 2}

    @contextmanager
    def fake_get_conn():
        yield _FakeConn(store)

    monkeypatch.setattr(mod, "get_conn", fake_get_conn)
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [])
    monkeypatch.setattr(mod, "execute", lambda *a, **kw: None)

    out = mod.set_product_skus(317, ["115-18103480", "0331-16555368"], matched_by=42)
    sqls = [c["sql"] for c in store["calls"]]
    assert any("UPDATE xmyc_storage_skus" in s and "product_id = NULL" in s for s in sqls)
    assert any("WHERE sku IN" in s for s in sqls)
    attach_call = next(c for c in store["calls"] if "WHERE sku IN" in c["sql"])
    assert attach_call["params"][0] == 317
    assert attach_call["params"][1] == 42
    assert "115-18103480" in attach_call["params"]
    assert out["product_id"] == 317
    assert out["cleared"] >= 1


def test_set_product_skus_with_empty_list_only_clears(monkeypatch):
    store: dict = {"calls": [], "force_rowcount": 3}

    @contextmanager
    def fake_get_conn():
        yield _FakeConn(store)

    monkeypatch.setattr(mod, "get_conn", fake_get_conn)
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [])
    monkeypatch.setattr(mod, "execute", lambda *a, **kw: None)

    out = mod.set_product_skus(99, [], matched_by=1)
    sqls = [c["sql"] for c in store["calls"]]
    assert any("UPDATE xmyc_storage_skus" in s and "product_id = NULL" in s for s in sqls)
    assert not any("WHERE sku IN" in s for s in sqls)
    assert out["attached"] == 0


def test_upsert_skus_writes_image_url(monkeypatch):
    store: dict = {"calls": [], "force_rowcount": 1}

    @contextmanager
    def fake_get_conn():
        yield _FakeConn(store)

    monkeypatch.setattr(mod, "get_conn", fake_get_conn)

    mod.upsert_skus([{
        "xmyc_id": "11917939",
        "sku_code": "83527156514",
        "sku": "115-18103480",
        "goods_name": "Hammer",
        "image_url": "https://img.example.com/hammer.jpg",
        "unit_price": Decimal("16.57"),
        "stock_available": 50,
        "warehouse": "WH",
        "shelf_code": "A1",
    }])

    insert_call = store["calls"][0]
    assert "image_url" in insert_call["sql"]
    assert "image_url=VALUES(image_url)" in insert_call["sql"]
    assert "https://img.example.com/hammer.jpg" in insert_call["params"]


def test_refresh_picks_primary_sku_by_order_count(monkeypatch):
    captured = {}

    def fake_query(sql, params=None):
        if "FROM xmyc_storage_skus" in sql:
            return [
                {"sku": "sku-1box", "unit_price": Decimal("8.16")},
                {"sku": "sku-2box", "unit_price": Decimal("16.0")},
            ]
        if "FROM dianxiaomi_order_lines" in sql:
            return [
                {"sku": "sku-1box", "cnt": 100},
                {"sku": "sku-2box", "cnt": 5},
            ]
        return []

    def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params

    monkeypatch.setattr(mod, "query", fake_query)
    monkeypatch.setattr(mod, "execute", fake_execute)

    price = mod._refresh_product_purchase_price(42)
    assert price == Decimal("8.16")  # 1box has more orders → primary
    assert captured["params"] == (Decimal("8.16"), 42)


def test_refresh_falls_back_to_median_when_no_orders(monkeypatch):
    def fake_query(sql, params=None):
        if "FROM xmyc_storage_skus" in sql:
            return [
                {"sku": "a", "unit_price": Decimal("5")},
                {"sku": "b", "unit_price": Decimal("9")},
                {"sku": "c", "unit_price": Decimal("100")},
            ]
        if "FROM dianxiaomi_order_lines" in sql:
            return []
        return []

    captured = {}
    monkeypatch.setattr(mod, "query", fake_query)
    monkeypatch.setattr(mod, "execute", lambda sql, params: captured.update({"params": params}))

    price = mod._refresh_product_purchase_price(7)
    assert price == Decimal("9")  # median of 5/9/100


def test_refresh_clears_when_no_skus(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda *a, **kw: [])
    captured = {}
    monkeypatch.setattr(mod, "execute", lambda sql, params: captured.update({"sql": sql, "params": params}))
    price = mod._refresh_product_purchase_price(7)
    assert price is None
    assert "purchase_price = NULL" in captured["sql"]


def test_list_skus_builds_filter_sql(monkeypatch):
    captured = {}

    def fake_query(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return []

    monkeypatch.setattr(mod, "query", fake_query)
    mod.list_skus(keyword="昆虫", matched_filter="unmatched", limit=50, offset=10)
    assert "s.sku LIKE" in captured["sql"]
    assert "s.product_id IS NULL" in captured["sql"]
    params = list(captured["params"])
    assert params[-2:] == [50, 10]
    assert params[0] == "%昆虫%"
    assert "s.image_url" in captured["sql"]


def test_update_sku_writes_decimal_and_returns_row(monkeypatch):
    from appcore import xmyc_storage as mod

    fake_execute_calls = []

    def fake_execute(sql, params):
        fake_execute_calls.append((sql, params))

    def fake_query_one(sql, params):
        assert params == (42,)
        return {"id": 42, "sku": "115-18103480", "standalone_price_sku": "25.00",
                "standalone_shipping_fee_sku": None, "packet_cost_actual_sku": None}

    monkeypatch.setattr(mod, "execute", fake_execute)
    monkeypatch.setattr(mod, "query_one", fake_query_one)

    row = mod.update_sku(42, {"standalone_price_sku": "25.00"})
    assert row["standalone_price_sku"] == "25.00"
    assert len(fake_execute_calls) == 1
    assert "standalone_price_sku = %s" in fake_execute_calls[0][0]
    assert fake_execute_calls[0][1][0] == mod.Decimal("25.00")


def test_update_sku_allows_null(monkeypatch):
    from appcore import xmyc_storage as mod

    fake_execute_calls = []

    def fake_execute(sql, params):
        fake_execute_calls.append((sql, params))

    monkeypatch.setattr(mod, "execute", fake_execute)
    monkeypatch.setattr(mod, "query_one", lambda sql, params: {"id": 1})

    mod.update_sku(1, {"packet_cost_actual_sku": None})
    assert len(fake_execute_calls) == 1
    assert fake_execute_calls[0][1][0] is None


def test_update_sku_rejects_invalid_decimal():
    from appcore import xmyc_storage as mod
    import pytest
    with pytest.raises(ValueError, match="invalid decimal"):
        mod.update_sku(1, {"standalone_price_sku": "twelve"})


def test_update_sku_raises_on_no_editable_fields():
    from appcore import xmyc_storage as mod
    import pytest
    with pytest.raises(ValueError, match="no editable fields"):
        mod.update_sku(1, {"sku": "should-not-be-editable"})


def test_update_sku_raises_lookup_error(monkeypatch):
    from appcore import xmyc_storage as mod
    import pytest

    monkeypatch.setattr(mod, "execute", lambda sql, params: None)
    monkeypatch.setattr(mod, "query_one", lambda sql, params: None)
    with pytest.raises(LookupError, match="not found"):
        mod.update_sku(99999, {"standalone_price_sku": "10.00"})
