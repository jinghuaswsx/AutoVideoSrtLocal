# Dianxiaomi Order Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Dianxiaomi order detail import path that fetches NewJoy and omurio order lines from 2026-01-01 through 2026-04-28, excludes SmartGearX, and stores detailed raw and normalized order data for later analytics.

**Architecture:** Add two durable tables for import batches and Dianxiaomi order lines. Put normalization and DB writes in `appcore/order_analytics.py`, and put browser/API orchestration in a new `tools/dianxiaomi_order_import.py` script modeled after `tools/shopifyid_dianxiaomi_sync.py`. Add a minimal `/order-analytics` API surface to start imports and read batch status, without changing the existing Shopify CSV/Excel import flow.

**Tech Stack:** Python, Flask, PyMySQL via `appcore.db`, Playwright sync API, MySQL migrations, pytest.

---

## File Structure

- Create `db/migrations/2026_04_28_dianxiaomi_order_import.sql`
  Defines `dianxiaomi_order_import_batches` and `dianxiaomi_order_lines`.
- Modify `appcore/order_analytics.py`
  Adds pure helpers for site filtering, order normalization, line normalization, import-batch writes, and line upsert.
- Create `tools/dianxiaomi_order_import.py`
  Connects to the server Dianxiaomi browser CDP session, calls `/api/package/list.json` and `/api/orderProfit/getOrderProfit.json`, filters lines, writes batches and rows, supports dry run and resume.
- Modify `web/routes/order_analytics.py`
  Adds batch status API and an import trigger API that calls the same service entrypoint in-process for small ranges.
- Modify `web/templates/order_analytics.html`
  Adds a small Dianxiaomi import panel under the existing order import area.
- Create `tests/test_dianxiaomi_order_import.py`
  Covers payload generation, date iteration, API response extraction, filtering, normalization, and dry-run summary.
- Modify or create `tests/test_order_analytics_dianxiaomi.py`
  Covers DB write helpers with monkeypatched `get_conn`/`execute` where direct DB access is not required.

## Implementation Notes

- The import source of truth is `POST https://www.dianxiaomi.com/api/package/list.json`.
- Profit/logistics enrichment is `POST https://www.dianxiaomi.com/api/orderProfit/getOrderProfit.json`.
- Listing sales APIs are kept optional for later reconciliation and are not needed for the first full import.
- Use the server Dianxiaomi browser CDP default `http://127.0.0.1:9223`, not the Shopify ID browser on `9222`.
- Do not use local Windows MySQL. For real imports, run on the server with local DB mode or through SSH mode consistent with the existing Shopify ID tool.
- Default date range for the user request is `2026-01-01` to `2026-04-28`.
- Default sites are `newjoy,omurio`; SmartGearX is excluded even if a product row matches another loose pattern.

---

### Task 1: Add Migration

**Files:**
- Create: `db/migrations/2026_04_28_dianxiaomi_order_import.sql`

- [ ] **Step 1: Write the migration**

Create `db/migrations/2026_04_28_dianxiaomi_order_import.sql`:

```sql
-- 店小秘订单明细导入：批次表 + 订单商品行表

CREATE TABLE IF NOT EXISTS dianxiaomi_order_import_batches (
  id                         BIGINT AUTO_INCREMENT PRIMARY KEY,
  source                     VARCHAR(64)  NOT NULL DEFAULT 'dianxiaomi',
  date_from                  DATE         NOT NULL,
  date_to                    DATE         NOT NULL,
  status                     ENUM('running','success','failed','dry_run') NOT NULL DEFAULT 'running',
  started_at                 DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at                DATETIME     DEFAULT NULL,
  duration_seconds           INT UNSIGNED DEFAULT NULL,
  requested_site_codes       VARCHAR(255) NOT NULL,
  included_shopify_ids_count INT          NOT NULL DEFAULT 0,
  total_pages                INT          NOT NULL DEFAULT 0,
  fetched_orders             INT          NOT NULL DEFAULT 0,
  fetched_lines              INT          NOT NULL DEFAULT 0,
  inserted_lines             INT          NOT NULL DEFAULT 0,
  updated_lines              INT          NOT NULL DEFAULT 0,
  skipped_lines              INT          NOT NULL DEFAULT 0,
  error_message              MEDIUMTEXT   DEFAULT NULL,
  summary_json               JSON         DEFAULT NULL,
  created_at                 DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at                 DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_dxm_batches_started (started_at),
  KEY idx_dxm_batches_status (status),
  KEY idx_dxm_batches_dates (date_from, date_to)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Dianxiaomi order import batch runs';

CREATE TABLE IF NOT EXISTS dianxiaomi_order_lines (
  id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
  batch_id            BIGINT       DEFAULT NULL,
  site_code           VARCHAR(64)  NOT NULL,
  product_id          INT          DEFAULT NULL COMMENT 'Matched local media_products.id',
  product_code        VARCHAR(128) DEFAULT NULL COMMENT 'Local media_products.product_code / Shopify handle',
  shopify_product_id  VARCHAR(64)  DEFAULT NULL,
  dxm_shop_id         VARCHAR(64)  DEFAULT NULL,
  dxm_shop_name       VARCHAR(255) DEFAULT NULL,
  dxm_package_id      VARCHAR(64)  NOT NULL,
  dxm_order_id        VARCHAR(128) DEFAULT NULL,
  extended_order_id   VARCHAR(128) DEFAULT NULL,
  package_number      VARCHAR(128) DEFAULT NULL,
  platform            VARCHAR(64)  DEFAULT NULL,
  order_state         VARCHAR(64)  DEFAULT NULL,
  buyer_name          VARCHAR(255) DEFAULT NULL,
  buyer_account       VARCHAR(255) DEFAULT NULL,
  product_name        VARCHAR(500) DEFAULT NULL,
  product_sku         VARCHAR(255) DEFAULT NULL,
  product_sub_sku     VARCHAR(255) DEFAULT NULL,
  product_display_sku VARCHAR(255) DEFAULT NULL,
  variant_text        VARCHAR(500) DEFAULT NULL,
  quantity            INT          NOT NULL DEFAULT 1,
  unit_price          DECIMAL(12,2) DEFAULT NULL,
  line_amount         DECIMAL(12,2) DEFAULT NULL,
  order_amount        DECIMAL(12,2) DEFAULT NULL,
  order_currency      VARCHAR(16)   DEFAULT NULL,
  ship_amount         DECIMAL(12,2) DEFAULT NULL COMMENT 'Buyer-paid shipping amount from order.shipAmount',
  amount_with_shipping DECIMAL(12,2) DEFAULT NULL,
  amount_cny          DECIMAL(12,2) DEFAULT NULL,
  logistic_fee        DECIMAL(12,2) DEFAULT NULL COMMENT 'Actual logistics fee from orderProfit.logisticFee',
  profit              DECIMAL(12,2) DEFAULT NULL,
  refund_amount_usd   DECIMAL(12,2) DEFAULT NULL,
  refund_amount       DECIMAL(12,2) DEFAULT NULL,
  buyer_country       VARCHAR(16)  DEFAULT NULL,
  buyer_country_name  VARCHAR(128) DEFAULT NULL,
  province            VARCHAR(128) DEFAULT NULL,
  city                VARCHAR(128) DEFAULT NULL,
  order_created_at    DATETIME     DEFAULT NULL,
  order_paid_at       DATETIME     DEFAULT NULL,
  paid_at             DATETIME     DEFAULT NULL,
  shipped_at          DATETIME     DEFAULT NULL,
  raw_order_json      JSON         NOT NULL,
  raw_line_json       JSON         NOT NULL,
  profit_json         JSON         DEFAULT NULL,
  imported_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_dxm_order_line (dxm_package_id, shopify_product_id, product_sku, product_sub_sku, product_display_sku),
  KEY idx_dxm_lines_batch (batch_id),
  KEY idx_dxm_lines_site_paid (site_code, order_paid_at),
  KEY idx_dxm_lines_shopify_product (shopify_product_id),
  KEY idx_dxm_lines_product_id (product_id),
  KEY idx_dxm_lines_country (buyer_country),
  CONSTRAINT fk_dxm_lines_batch FOREIGN KEY (batch_id) REFERENCES dianxiaomi_order_import_batches(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Dianxiaomi order line details imported from order APIs';
```

- [ ] **Step 2: Verify migration has no placeholders**

Run:

```powershell
Select-String -Encoding UTF8 -Path db/migrations/2026_04_28_dianxiaomi_order_import.sql -Pattern 'PLACEHOLDER_SHOULD_NOT_EXIST'
```

Expected: no output.

- [ ] **Step 3: Commit**

```powershell
git add db/migrations/2026_04_28_dianxiaomi_order_import.sql
git commit -m "feat: add dianxiaomi order import tables"
```

---

### Task 2: Add Pure Normalization Helpers

**Files:**
- Modify: `appcore/order_analytics.py`
- Test: `tests/test_order_analytics_dianxiaomi.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_order_analytics_dianxiaomi.py`:

```python
from __future__ import annotations

from datetime import datetime

from appcore import order_analytics as oa


def test_extract_shopify_product_id_from_line_prefers_product_id():
    line = {"productId": "8560559554733", "productUrl": "https://example.com/products/demo"}

    assert oa.extract_dianxiaomi_shopify_product_id(line) == "8560559554733"


def test_extract_shopify_product_id_from_url_fallback():
    line = {"productUrl": "https://admin.shopify.com/store/demo/products/8560559554733"}

    assert oa.extract_dianxiaomi_shopify_product_id(line) == "8560559554733"


def test_build_dianxiaomi_product_scope_excludes_smartgearx(monkeypatch):
    monkeypatch.setattr(
        oa,
        "query",
        lambda sql, args=(): [
            {"id": 1, "product_code": "newjoy-demo", "shopifyid": "111", "site_code": "newjoy"},
            {"id": 2, "product_code": "omurio-demo", "shopifyid": "222", "site_code": "omurio"},
            {"id": 3, "product_code": "smart-demo", "shopifyid": "333", "site_code": "smartgearx"},
        ],
    )

    scope = oa.build_dianxiaomi_product_scope(["newjoy", "omurio"])

    assert set(scope.by_shopify_id) == {"111", "222"}
    assert scope.by_shopify_id["111"]["site_code"] == "newjoy"
    assert scope.excluded_shopify_ids == {"333"}


def test_normalize_dianxiaomi_order_lines_keeps_requested_sites_and_amounts():
    scope = oa.DianxiaomiProductScope(
        by_shopify_id={
            "8560559554733": {
                "product_id": 7,
                "product_code": "demo-product",
                "site_code": "newjoy",
                "shopifyid": "8560559554733",
            }
        },
        excluded_shopify_ids=set(),
    )
    order = {
        "id": "9001",
        "shopId": "8477915",
        "shopName": "Joyeloo",
        "orderId": "DXM-1",
        "extendedOrderId": "#1001",
        "packageNumber": "PKG-1",
        "platform": "shopify",
        "state": "paid",
        "buyerName": "Ada",
        "buyerAccount": "ada@example.com",
        "buyerCountry": "US",
        "countryCN": "美国",
        "orderAmount": "17.94",
        "orderUnit": "USD",
        "shipAmount": "4.99",
        "refundAmountUsd": "0",
        "orderCreateTime": "2026-04-27 10:00:00",
        "orderPayTime": "2026-04-27 10:03:00",
        "productList": [
            {
                "productId": "8560559554733",
                "productName": "Demo Product",
                "productSku": "SKU-A",
                "productSubSku": "SUB-A",
                "productDisplaySku": "SKU-A / Red",
                "quantity": "2",
                "price": "12.95",
                "attrListStr": "Red",
            }
        ],
    }
    profits = {"9001": {"amountCNY": "128.00", "logisticFee": "6.50", "profit": "30.10"}}

    rows, skipped = oa.normalize_dianxiaomi_order(order, scope, profits)

    assert skipped == 0
    assert rows[0]["site_code"] == "newjoy"
    assert rows[0]["product_id"] == 7
    assert rows[0]["quantity"] == 2
    assert rows[0]["unit_price"] == 12.95
    assert rows[0]["line_amount"] == 25.9
    assert rows[0]["ship_amount"] == 4.99
    assert rows[0]["amount_with_shipping"] == 17.94
    assert rows[0]["logistic_fee"] == 6.5
    assert rows[0]["order_paid_at"] == datetime(2026, 4, 27, 10, 3)


def test_normalize_dianxiaomi_order_skips_smartgearx_scope():
    scope = oa.DianxiaomiProductScope(by_shopify_id={}, excluded_shopify_ids={"333"})
    order = {
        "id": "9002",
        "productList": [{"productId": "333", "productSku": "S", "quantity": "1", "price": "9.99"}],
    }

    rows, skipped = oa.normalize_dianxiaomi_order(order, scope, {})

    assert rows == []
    assert skipped == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_order_analytics_dianxiaomi.py -q
```

Expected: FAIL because the Dianxiaomi helper functions do not exist yet.

- [ ] **Step 3: Implement helpers**

Append these imports near the top of `appcore/order_analytics.py`:

```python
from dataclasses import dataclass
```

Add these helpers before the existing Shopify import section:

```python
@dataclass(frozen=True)
class DianxiaomiProductScope:
    by_shopify_id: dict[str, dict[str, Any]]
    excluded_shopify_ids: set[str]


def _safe_decimal_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return round(float(str(value).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None


def _parse_dianxiaomi_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19] if fmt.endswith("%S") else text[:10], fmt)
        except ValueError:
            continue
    return None


def extract_dianxiaomi_shopify_product_id(line: dict[str, Any]) -> str | None:
    for key in ("productId", "shopifyProductId", "pid"):
        value = str(line.get(key) or "").strip()
        if value.isdigit():
            return value
    for key in ("productUrl", "sourceUrl"):
        text = str(line.get(key) or "")
        match = re.search(r"/products/(\d+)", text)
        if match:
            return match.group(1)
    return None


def build_dianxiaomi_product_scope(site_codes: list[str]) -> DianxiaomiProductScope:
    requested = {str(code).strip().lower() for code in site_codes if str(code).strip()}
    rows = query(
        "SELECT id, product_code, shopifyid, LOWER(COALESCE(site_code, store_code, '')) AS site_code "
        "FROM media_products "
        "WHERE deleted_at IS NULL AND shopifyid IS NOT NULL AND shopifyid <> ''"
    )
    by_shopify_id: dict[str, dict[str, Any]] = {}
    excluded_shopify_ids: set[str] = set()
    for row in rows:
        shopifyid = str(row.get("shopifyid") or "").strip()
        if not shopifyid:
            continue
        site_code = str(row.get("site_code") or "").strip().lower()
        if site_code == "smartgearx":
            excluded_shopify_ids.add(shopifyid)
            continue
        if site_code in requested:
            by_shopify_id[shopifyid] = {
                "product_id": row.get("id"),
                "product_code": row.get("product_code"),
                "site_code": site_code,
                "shopifyid": shopifyid,
            }
    return DianxiaomiProductScope(by_shopify_id=by_shopify_id, excluded_shopify_ids=excluded_shopify_ids)


def _dianxiaomi_order_lines(order: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("productList", "cancelProductList"):
        value = order.get(key) or []
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def normalize_dianxiaomi_order(
    order: dict[str, Any],
    scope: DianxiaomiProductScope,
    profits_by_package_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    normalized: list[dict[str, Any]] = []
    skipped = 0
    package_id = str(order.get("id") or order.get("packageId") or "").strip()
    profit = profits_by_package_id.get(package_id) or {}
    for line in _dianxiaomi_order_lines(order):
        shopify_product_id = extract_dianxiaomi_shopify_product_id(line)
        if not shopify_product_id or shopify_product_id in scope.excluded_shopify_ids:
            skipped += 1
            continue
        product = scope.by_shopify_id.get(shopify_product_id)
        if not product:
            skipped += 1
            continue
        quantity = _safe_int(str(line.get("quantity") or line.get("productCount") or "1"), 1)
        unit_price = _safe_decimal_float(line.get("price"))
        line_amount = round((unit_price or 0) * quantity, 2) if unit_price is not None else None
        addr = order.get("dxmPackageAddr") if isinstance(order.get("dxmPackageAddr"), dict) else {}
        normalized.append({
            "site_code": product["site_code"],
            "product_id": product["product_id"],
            "product_code": product["product_code"],
            "shopify_product_id": shopify_product_id,
            "dxm_shop_id": str(order.get("shopId") or "").strip() or None,
            "dxm_shop_name": str(order.get("shopName") or "").strip() or None,
            "dxm_package_id": package_id,
            "dxm_order_id": str(order.get("orderId") or "").strip() or None,
            "extended_order_id": str(order.get("extendedOrderId") or "").strip() or None,
            "package_number": str(order.get("packageNumber") or "").strip() or None,
            "platform": str(order.get("platform") or order.get("shopPlatform") or "").strip() or None,
            "order_state": str(order.get("state") or "").strip() or None,
            "buyer_name": str(order.get("buyerName") or "").strip() or None,
            "buyer_account": str(order.get("buyerAccount") or "").strip() or None,
            "product_name": str(line.get("productName") or "").strip()[:500] or None,
            "product_sku": str(line.get("productSku") or "").strip() or None,
            "product_sub_sku": str(line.get("productSubSku") or "").strip() or None,
            "product_display_sku": str(line.get("productDisplaySku") or line.get("displaySku") or "").strip() or None,
            "variant_text": str(line.get("attrListStr") or line.get("attrList") or "").strip()[:500] or None,
            "quantity": quantity,
            "unit_price": unit_price,
            "line_amount": line_amount,
            "order_amount": _safe_decimal_float(order.get("orderAmount")),
            "order_currency": str(order.get("orderUnit") or "").strip() or None,
            "ship_amount": _safe_decimal_float(order.get("shipAmount")),
            "amount_with_shipping": _safe_decimal_float(order.get("orderAmount")),
            "amount_cny": _safe_decimal_float(profit.get("amountCNY")),
            "logistic_fee": _safe_decimal_float(profit.get("logisticFee")),
            "profit": _safe_decimal_float(profit.get("profit")),
            "refund_amount_usd": _safe_decimal_float(order.get("refundAmountUsd")),
            "refund_amount": _safe_decimal_float(order.get("refundAmount")),
            "buyer_country": str(order.get("buyerCountry") or addr.get("country") or "").strip() or None,
            "buyer_country_name": str(order.get("countryCN") or "").strip() or None,
            "province": str(addr.get("province") or "").strip() or None,
            "city": str(addr.get("city") or "").strip() or None,
            "order_created_at": _parse_dianxiaomi_ts(order.get("orderCreateTime")),
            "order_paid_at": _parse_dianxiaomi_ts(order.get("orderPayTime")),
            "paid_at": _parse_dianxiaomi_ts(order.get("paidTime")),
            "shipped_at": _parse_dianxiaomi_ts(order.get("shippedTime")),
            "raw_order_json": order,
            "raw_line_json": line,
            "profit_json": profit or None,
        })
    return normalized, skipped
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m pytest tests/test_order_analytics_dianxiaomi.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add appcore/order_analytics.py tests/test_order_analytics_dianxiaomi.py
git commit -m "feat: normalize dianxiaomi order lines"
```

---

### Task 3: Add Batch and Upsert DB Helpers

**Files:**
- Modify: `appcore/order_analytics.py`
- Test: `tests/test_order_analytics_dianxiaomi.py`

- [ ] **Step 1: Add failing tests**

Append:

```python
def test_start_and_finish_dianxiaomi_batch_use_expected_sql(monkeypatch):
    calls = []

    class Cursor:
        lastrowid = 42
        def execute(self, sql, args):
            calls.append(("cursor.execute", sql, args))
    class CursorContext(Cursor):
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
    class Conn:
        def cursor(self):
            return CursorContext()
        def commit(self):
            calls.append(("commit", "", ()))
        def close(self):
            calls.append(("close", "", ()))

    monkeypatch.setattr(oa, "get_conn", lambda: Conn())
    monkeypatch.setattr(
        oa,
        "execute",
        lambda sql, args=(): calls.append(("execute", sql, args)) or 1,
    )

    batch_id = oa.start_dianxiaomi_order_import_batch("2026-01-01", "2026-01-02", ["newjoy", "omurio"], 2)
    oa.finish_dianxiaomi_order_import_batch(batch_id, "success", {"inserted_lines": 3})

    assert batch_id == 42
    assert "INSERT INTO dianxiaomi_order_import_batches" in calls[0][1]
    assert calls[0][2] == ("2026-01-01", "2026-01-02", "newjoy,omurio", 2)
    assert "UPDATE dianxiaomi_order_import_batches SET status=%s" in calls[3][1]


def test_upsert_dianxiaomi_order_lines_serializes_json(monkeypatch):
    captured = {}

    class Cursor:
        rowcount = 1
        def execute(self, sql, args):
            captured["sql"] = sql
            captured["args"] = args
    class Conn:
        def cursor(self):
            return Cursor()
        def commit(self):
            captured["committed"] = True
        def close(self):
            captured["closed"] = True
    class CursorContext(Cursor):
        def __enter__(self):
            return self
        def __exit__(self, exc_type, exc, tb):
            return False
    class ConnContext(Conn):
        def cursor(self):
            return CursorContext()

    monkeypatch.setattr(oa, "get_conn", lambda: ConnContext())

    result = oa.upsert_dianxiaomi_order_lines(
        42,
        [{
            "site_code": "newjoy",
            "product_id": 7,
            "product_code": "demo",
            "shopify_product_id": "111",
            "dxm_package_id": "9001",
            "raw_order_json": {"id": "9001"},
            "raw_line_json": {"productId": "111"},
            "profit_json": {"profit": "1.23"},
        }],
    )

    assert result == {"affected": 1, "rows": 1}
    assert "INSERT INTO dianxiaomi_order_lines" in captured["sql"]
    assert '"id": "9001"' in captured["args"]
    assert captured["committed"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/test_order_analytics_dianxiaomi.py -q
```

Expected: FAIL because batch/upsert helpers do not exist.

- [ ] **Step 3: Implement DB helpers**

Add:

```python
def start_dianxiaomi_order_import_batch(
    date_from: str,
    date_to: str,
    site_codes: list[str],
    included_shopify_ids_count: int,
) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO dianxiaomi_order_import_batches "
                "(date_from, date_to, requested_site_codes, included_shopify_ids_count) "
                "VALUES (%s,%s,%s,%s)",
                (date_from, date_to, ",".join(site_codes), included_shopify_ids_count),
            )
            batch_id = int(cur.lastrowid)
        conn.commit()
        return batch_id
    finally:
        conn.close()


def finish_dianxiaomi_order_import_batch(
    batch_id: int,
    status: str,
    summary: dict[str, Any],
    error_message: str | None = None,
) -> None:
    execute(
        "UPDATE dianxiaomi_order_import_batches SET status=%s, finished_at=NOW(), "
        "duration_seconds=TIMESTAMPDIFF(SECOND, started_at, NOW()), "
        "total_pages=%s, fetched_orders=%s, fetched_lines=%s, inserted_lines=%s, "
        "updated_lines=%s, skipped_lines=%s, error_message=%s, summary_json=%s "
        "WHERE id=%s",
        (
            status,
            int(summary.get("total_pages") or 0),
            int(summary.get("fetched_orders") or 0),
            int(summary.get("fetched_lines") or 0),
            int(summary.get("inserted_lines") or 0),
            int(summary.get("updated_lines") or 0),
            int(summary.get("skipped_lines") or 0),
            error_message,
            json.dumps(summary, ensure_ascii=False),
            batch_id,
        ),
    )
```

Add `upsert_dianxiaomi_order_lines(batch_id, rows)` with explicit column list matching the migration. JSON fields must use `json.dumps(..., ensure_ascii=False)`. Use:

```sql
ON DUPLICATE KEY UPDATE
  batch_id=VALUES(batch_id),
  quantity=VALUES(quantity),
  unit_price=VALUES(unit_price),
  line_amount=VALUES(line_amount),
  order_amount=VALUES(order_amount),
  ship_amount=VALUES(ship_amount),
  amount_with_shipping=VALUES(amount_with_shipping),
  amount_cny=VALUES(amount_cny),
  logistic_fee=VALUES(logistic_fee),
  profit=VALUES(profit),
  raw_order_json=VALUES(raw_order_json),
  raw_line_json=VALUES(raw_line_json),
  profit_json=VALUES(profit_json),
  imported_at=NOW()
```

- [ ] **Step 4: Run tests**

```powershell
python -m pytest tests/test_order_analytics_dianxiaomi.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add appcore/order_analytics.py tests/test_order_analytics_dianxiaomi.py
git commit -m "feat: persist dianxiaomi order import rows"
```

---

### Task 4: Add Dianxiaomi Import Script

**Files:**
- Create: `tools/dianxiaomi_order_import.py`
- Test: `tests/test_dianxiaomi_order_import.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_dianxiaomi_order_import.py`:

```python
from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "dianxiaomi_order_import.py"


def _load_module():
    assert MODULE_PATH.exists(), f"missing import module: {MODULE_PATH}"
    spec = importlib.util.spec_from_file_location("dianxiaomi_order_import", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_iter_dates_includes_end_date():
    mod = _load_module()

    assert list(mod.iter_dates(date(2026, 1, 1), date(2026, 1, 3))) == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 3),
    ]


def test_build_order_payload_uses_pay_time_range_and_state():
    mod = _load_module()

    payload = mod.build_order_payload(date(2026, 4, 27), page_no=2, state="paid")

    assert payload["pageNo"] == 2
    assert payload["pageSize"] == 100
    assert payload["state"] == "paid"
    assert payload["startTime"] == "2026-04-27 00:00:00"
    assert payload["endTime"] == "2026-04-27 23:59:59"
    assert payload["orderField"] == "order_pay_time"


def test_extract_order_page_reads_list_and_total_page():
    mod = _load_module()
    payload = {
        "data": {
            "page": {
                "totalPage": 3,
                "pageNo": 1,
                "list": [{"id": "9001"}],
            }
        }
    }

    page = mod.extract_order_page(payload)

    assert page.total_page == 3
    assert page.page_no == 1
    assert page.orders == [{"id": "9001"}]


def test_run_import_dry_run_uses_fetchers_and_does_not_write(monkeypatch):
    mod = _load_module()
    written = []
    scope = mod.oa.DianxiaomiProductScope(
        by_shopify_id={"111": {"product_id": 1, "product_code": "demo", "site_code": "newjoy", "shopifyid": "111"}},
        excluded_shopify_ids=set(),
    )
    monkeypatch.setattr(mod.oa, "build_dianxiaomi_product_scope", lambda sites: scope)
    monkeypatch.setattr(mod.oa, "normalize_dianxiaomi_order", lambda order, scope, profits: ([{
        "site_code": "newjoy",
        "dxm_package_id": "9001",
        "shopify_product_id": "111",
        "raw_order_json": order,
        "raw_line_json": {"productId": "111"},
    }], 0))
    monkeypatch.setattr(mod.oa, "upsert_dianxiaomi_order_lines", lambda batch_id, rows: written.append(rows) or {"affected": 1, "rows": len(rows)})
    monkeypatch.setattr(mod.oa, "start_dianxiaomi_order_import_batch", lambda *args: 42)
    monkeypatch.setattr(mod.oa, "finish_dianxiaomi_order_import_batch", lambda *args, **kwargs: None)

    report = mod.run_import(
        start_date=date(2026, 4, 27),
        end_date=date(2026, 4, 27),
        site_codes=["newjoy"],
        states=["paid"],
        fetch_orders=lambda day, page_no, state: {"data": {"page": {"totalPage": 1, "pageNo": 1, "list": [{"id": "9001"}]}}},
        fetch_profits=lambda package_ids: {"9001": {"profit": "1.00"}},
        dry_run=True,
    )

    assert report["summary"]["fetched_orders"] == 1
    assert report["summary"]["fetched_lines"] == 1
    assert report["summary"]["inserted_lines"] == 0
    assert written == []
```

- [ ] **Step 2: Run tests to verify they fail**

```powershell
python -m pytest tests/test_dianxiaomi_order_import.py -q
```

Expected: FAIL because the script does not exist.

- [ ] **Step 3: Implement script**

Create `tools/dianxiaomi_order_import.py` with:

```python
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

from appcore import order_analytics as oa

ORDER_URL = "https://www.dianxiaomi.com/api/package/list.json"
PROFIT_URL = "https://www.dianxiaomi.com/api/orderProfit/getOrderProfit.json"
ORDER_PAGE_URL = "https://www.dianxiaomi.com/web/order/paid"
SERVER_BROWSER_CDP_URL = "http://127.0.0.1:9223"
DEFAULT_STATES = ["paid", "approved", "processed", "allocated", "shipped"]
BROWSER_MODES = ("auto", "server-cdp")


@dataclass(frozen=True)
class OrderPage:
    total_page: int
    page_no: int
    orders: list[dict[str, Any]]


def iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def build_order_payload(day: date, page_no: int, state: str) -> dict[str, Any]:
    return {
        "pageNo": page_no,
        "pageSize": 100,
        "shopId": "-1",
        "state": state,
        "platform": "",
        "isSearch": 0,
        "searchType": "orderId",
        "authId": "-1",
        "startTime": f"{day:%Y-%m-%d} 00:00:00",
        "endTime": f"{day:%Y-%m-%d} 23:59:59",
        "country": "",
        "orderField": "order_pay_time",
        "isVoided": 0,
        "isRemoved": 0,
        "ruleId": "-1",
        "storageId": 0,
        "isOversea": "-1",
        "isFree": 0,
        "isBatch": 0,
        "history": "",
        "custom": "-1",
        "timeOut": 0,
        "refundStatus": 0,
        "buyerAccount": "",
        "forbiddenStatus": "-1",
        "forbiddenReason": 0,
        "behindTrack": "-1",
        "orderId": "",
        "axios_cancelToken": "true",
    }


def ensure_dianxiaomi_success(payload: dict[str, Any]) -> None:
    if int(payload.get("code", -1)) != 0:
        raise RuntimeError(f"店小秘接口返回异常：code={payload.get('code')} msg={payload.get('msg')}")


def extract_order_page(payload: dict[str, Any]) -> OrderPage:
    ensure_dianxiaomi_success(payload)
    page = ((payload.get("data") or {}).get("page") or {})
    return OrderPage(
        total_page=int(page.get("totalPage") or 0),
        page_no=int(page.get("pageNo") or 0),
        orders=[item for item in (page.get("list") or []) if isinstance(item, dict)],
    )
```

Add a `_post_form_via_page(page, url, payload)` helper using `page.evaluate()` like `tools/shopifyid_dianxiaomi_sync.py`.

Add `run_import(...)` that:

```python
scope = oa.build_dianxiaomi_product_scope(site_codes)
batch_id = None if dry_run else oa.start_dianxiaomi_order_import_batch(...)
for day in iter_dates(start_date, end_date):
    for state in states:
        first = extract_order_page(fetch_orders(day, 1, state))
        for page_no in range(1, first.total_page + 1):
            payload = first_payload if page_no == 1 else fetch_orders(day, page_no, state)
            orders = extract_order_page(payload).orders
            profits = fetch_profits([str(order.get("id")) for order in orders if order.get("id")])
            for order in orders:
                rows, skipped = oa.normalize_dianxiaomi_order(order, scope, profits)
                summary["skipped_lines"] += skipped
                summary["fetched_lines"] += len(rows)
                if rows and not dry_run:
                    result = oa.upsert_dianxiaomi_order_lines(batch_id, rows)
                    summary["inserted_lines"] += result["affected"]
```

The returned report must include `summary`, `date_from`, `date_to`, `site_codes`, `states`, and `dry_run`.

Add CLI arguments:

```text
--start-date
--end-date
--sites default newjoy,omurio
--states default paid,approved,processed,allocated,shipped
--browser-mode default auto
--browser-cdp-url default http://127.0.0.1:9223
--dry-run
--resume
--skip-login-prompt
```

- [ ] **Step 4: Run script unit tests**

```powershell
python -m pytest tests/test_dianxiaomi_order_import.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add tools/dianxiaomi_order_import.py tests/test_dianxiaomi_order_import.py
git commit -m "feat: add dianxiaomi order import script"
```

---

### Task 5: Add Minimal Web Trigger and Status APIs

**Files:**
- Modify: `web/routes/order_analytics.py`
- Modify: `web/templates/order_analytics.html`
- Test: existing route tests or new focused Flask route tests if patterns exist

- [ ] **Step 1: Inspect existing template import area**

Run:

```powershell
Select-String -Encoding UTF8 -Path web/templates/order_analytics.html -Pattern '订单导入','upload','ad-upload' -Context 3,6
```

Expected: locate the existing order import card and JavaScript upload handlers.

- [ ] **Step 2: Add route handlers**

In `web/routes/order_analytics.py`, add:

```python
@bp.route("/order-analytics/dianxiaomi-import-batches")
@login_required
@admin_required
def dianxiaomi_import_batches():
    rows = oa.get_dianxiaomi_order_import_batches(limit=request.args.get("limit", 20, type=int))
    return jsonify(_json_safe({"rows": rows}))


@bp.route("/order-analytics/dianxiaomi-import", methods=["POST"])
@login_required
@admin_required
def dianxiaomi_import():
    payload = request.get_json(silent=True) or {}
    start_date = (payload.get("start_date") or "2026-01-01").strip()
    end_date = (payload.get("end_date") or "2026-04-28").strip()
    site_codes = payload.get("site_codes") or ["newjoy", "omurio"]
    dry_run = bool(payload.get("dry_run", True))
    try:
        from tools import dianxiaomi_order_import as dxm_import
        result = dxm_import.run_import_from_server_browser(
            start_date_text=start_date,
            end_date_text=end_date,
            site_codes=site_codes,
            dry_run=dry_run,
            skip_login_prompt=True,
        )
    except Exception as exc:
        log.warning("dianxiaomi import failed: %s", exc, exc_info=True)
        return jsonify(error=f"店小秘订单导入失败：{exc}"), 500
    return jsonify(_json_safe(result))
```

Add `get_dianxiaomi_order_import_batches(limit=20)` in `appcore/order_analytics.py`:

```python
def get_dianxiaomi_order_import_batches(limit: int = 20) -> list[dict]:
    limit = max(1, min(int(limit or 20), 100))
    return query(
        "SELECT * FROM dianxiaomi_order_import_batches ORDER BY started_at DESC LIMIT %s",
        (limit,),
    )
```

- [ ] **Step 3: Add template panel**

Add a compact panel in `web/templates/order_analytics.html` near the order import area:

```html
<section class="oa-import-panel" id="dxmImportPanel">
  <div class="oa-import-panel__main">
    <h3>店小秘订单明细</h3>
    <p>NewJoy、omurio，默认 2026-01-01 至 2026-04-28。</p>
  </div>
  <div class="oa-import-panel__actions">
    <button type="button" class="btn btn-secondary" id="dxmDryRunBtn">试跑</button>
    <button type="button" class="btn btn-primary" id="dxmImportBtn">抓取并落库</button>
  </div>
</section>
```

Add JavaScript:

```javascript
async function runDxmImport(dryRun) {
  const btn = dryRun ? document.getElementById('dxmDryRunBtn') : document.getElementById('dxmImportBtn');
  btn.disabled = true;
  try {
    const res = await fetch('/order-analytics/dianxiaomi-import', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        start_date: '2026-01-01',
        end_date: '2026-04-28',
        site_codes: ['newjoy', 'omurio'],
        dry_run: dryRun
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || '导入失败');
    showToast(dryRun ? '店小秘试跑完成' : '店小秘订单已写入', 'success');
  } catch (err) {
    showToast(err.message || String(err), 'error');
  } finally {
    btn.disabled = false;
  }
}
document.getElementById('dxmDryRunBtn')?.addEventListener('click', () => runDxmImport(true));
document.getElementById('dxmImportBtn')?.addEventListener('click', () => runDxmImport(false));
```

Use existing button classes and toast utilities in the template if names differ.

- [ ] **Step 4: Run route/template focused tests**

```powershell
python -m pytest tests/test_order_analytics_dashboard.py tests/test_order_analytics_ads.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add appcore/order_analytics.py web/routes/order_analytics.py web/templates/order_analytics.html
git commit -m "feat: add dianxiaomi order import entrypoint"
```

---

### Task 6: Server Dry Run and Full Import

**Files:**
- No source edits unless a verified issue appears.

- [ ] **Step 1: Verify tests locally in the worktree**

Run:

```powershell
python -m pytest tests/test_shopifyid_dianxiaomi_sync.py tests/test_order_analytics_dianxiaomi.py tests/test_dianxiaomi_order_import.py tests/test_order_analytics_ads.py tests/test_order_analytics_dashboard.py -q
```

Expected: PASS.

- [ ] **Step 2: Deploy or copy branch to test server only**

Use existing project deployment procedure for the test environment at `/opt/autovideosrt-test`. Do not touch `/opt/autovideosrt` unless the user explicitly asks for production release.

- [ ] **Step 3: Apply migration to test DB**

On the server, apply:

```bash
mysql auto_video_test < db/migrations/2026_04_28_dianxiaomi_order_import.sql
```

Expected: no SQL error.

- [ ] **Step 4: Confirm Dianxiaomi browser is available**

On the server:

```bash
curl -s http://127.0.0.1:9223/json/version
systemctl is-active autovideosrt-mk-browser.service
```

Expected: JSON version output and `active`.

- [ ] **Step 5: Run a two-day dry run**

On the server test environment:

```bash
cd /opt/autovideosrt-test
source venv/bin/activate
python tools/dianxiaomi_order_import.py --start-date 2026-04-27 --end-date 2026-04-28 --sites newjoy,omurio --browser-mode server-cdp --browser-cdp-url http://127.0.0.1:9223 --dry-run --skip-login-prompt
```

Expected: report shows fetched orders, normalized NewJoy/omurio lines, and zero inserted lines.

- [ ] **Step 6: Run a two-day real import**

```bash
python tools/dianxiaomi_order_import.py --start-date 2026-04-27 --end-date 2026-04-28 --sites newjoy,omurio --browser-mode server-cdp --browser-cdp-url http://127.0.0.1:9223 --skip-login-prompt
```

Expected: batch status `success`, `dianxiaomi_order_lines` contains rows for `newjoy` or `omurio`, SmartGearX is absent.

- [ ] **Step 7: Run full requested import**

```bash
python tools/dianxiaomi_order_import.py --start-date 2026-01-01 --end-date 2026-04-28 --sites newjoy,omurio --browser-mode server-cdp --browser-cdp-url http://127.0.0.1:9223 --skip-login-prompt --resume
```

Expected: success summary for the full date range.

- [ ] **Step 8: Verify stored fields**

Run SQL on `auto_video_test`:

```sql
SELECT site_code, COUNT(*) AS rows_count, MIN(order_paid_at), MAX(order_paid_at)
FROM dianxiaomi_order_lines
GROUP BY site_code;

SELECT COUNT(*) AS smartgearx_rows
FROM dianxiaomi_order_lines
WHERE site_code='smartgearx';

SELECT site_code, buyer_country, SUM(line_amount) AS product_sales, SUM(ship_amount) AS buyer_shipping, SUM(amount_with_shipping) AS order_amount_with_shipping
FROM dianxiaomi_order_lines
GROUP BY site_code, buyer_country
ORDER BY site_code, buyer_country;
```

Expected: NewJoy and omurio rows exist, SmartGearX count is 0, country revenue/shipping fields are populated where Dianxiaomi provided them.

- [ ] **Step 9: Commit any verified fixups**

If server testing required source fixes:

```powershell
git status --short
git add <changed files>
git commit -m "fix: stabilize dianxiaomi order import"
```

---

## Self-Review

- Spec coverage:
  - Date range `2026-01-01` to `2026-04-28`: covered in script defaults, web trigger, and server full import.
  - NewJoy and omurio only: covered by `build_dianxiaomi_product_scope`.
  - SmartGearX excluded: covered by scope filtering and verification SQL.
  - Detailed order data: covered by `dianxiaomi_order_lines` normalized columns and raw JSON fields.
  - Region sales, shipping, product sales, amount with shipping: covered by `buyer_country`, `line_amount`, `ship_amount`, `amount_with_shipping`.
  - Export Excel research: documented as optional Listing reconciliation, not first import source.
  - Storage first, analysis later: covered by raw JSON and normalized order-line table.
- Placeholder scan:
  - No implementation placeholders remain in the plan.
- Type consistency:
  - `DianxiaomiProductScope`, `normalize_dianxiaomi_order`, `upsert_dianxiaomi_order_lines`, and `run_import` signatures are consistently referenced across tasks.
