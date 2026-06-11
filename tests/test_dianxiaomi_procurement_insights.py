from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

from appcore import dianxiaomi_procurement_insights as mod
from server_config import DEFAULT_SERVER_HOST


ROOT = Path(__file__).resolve().parents[1]


def test_build_insights_response_matches_media_product_sku(monkeypatch):
    def fake_query(sql, params=()):
        if "FROM media_product_skus" in sql:
            return [
                {
                    "id": 7,
                    "name": "Demo Product",
                    "product_code": "demo-product-rjc",
                    "shopifyid": "123456789",
                    "shopify_title": "Demo Product EN",
                    "dianxiaomi_sku": "SKU-A",
                    "dianxiaomi_product_sku": "",
                    "dianxiaomi_sku_code": "",
                    "shopify_sku": "",
                }
            ]
        return []

    monkeypatch.setattr(mod, "query", fake_query)
    monkeypatch.setattr(mod, "query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "current_meta_business_date", lambda: date(2026, 6, 9))
    monkeypatch.setattr(
        mod.media_product_ad_status_cache,
        "get_product_ad_summary_cache",
        lambda pids: {
            7: {
                "delivery_status": "active",
                "overall_roas": 2.345,
                "ad_spend_usd": 120.5,
                "active_7d_ad_spend_usd": 15.0,
                "order_revenue_usd": 210.0,
                "shipping_revenue_usd": 30.0,
                "total_revenue_usd": 240.0,
                "computed_at": "2026-06-09T12:00:00",
            }
        },
    )
    monkeypatch.setattr(
        mod.media_product_order_stats,
        "get_product_order_stats",
        lambda pids, today=None: {
            7: {"total": {"today": 3, "yesterday": 2, "last_7d": 11, "last_30d": 24}}
        },
    )
    monkeypatch.setattr(
        mod.media_product_ad_orders_report,
        "get_product_ad_orders_report",
        lambda product_id, today=None: {
            "total": {
                "today_orders": 3,
                "today_spend": 8.0,
                "today_roas": 1.5,
                "yesterday_orders": 2,
                "yesterday_spend": 12.5,
                "yesterday_roas": 2.2,
                "last_7d_orders": 11,
                "last_7d_spend": 30.0,
                "last_7d_roas": 2.1,
                "last_30d_orders": 24,
                "last_30d_spend": 90.0,
                "last_30d_roas": 2.4,
                "total_orders": 42,
                "total_spend": 120.5,
                "total_roas": 2.345,
            },
            "by_lang": {
                "de": {
                    "today_orders": 1,
                    "yesterday_orders": 0,
                    "last_7d_orders": 4,
                    "last_30d_orders": 9,
                    "today_spend": 8.0,
                    "last_7d_spend": 30.0,
                    "total_spend": 50.0,
                    "last_7d_roas": 2.1,
                    "total_roas": 2.4,
                }
            }
        },
    )
    monkeypatch.setattr(mod.media_product_ad_status_cache, "get_product_lang_ad_summary_cache", lambda pids: {})

    payload = mod.build_insights_response({"sku": "SKU-A", "page_url": "https://www.dianxiaomi.com/"})

    assert payload["matched"] is True
    assert payload["product"]["id"] == 7
    assert payload["product"]["match_method"] == "media_product_skus"
    assert payload["summary"]["delivery_status"] == "active"
    assert payload["summary"]["orders"]["today"] == 3
    assert payload["summary"]["orders"]["last_7d"] == 11
    assert payload["summary"]["total_orders"] == 42
    assert payload["summary"]["true_roas"] == 2.345
    assert payload["summary"]["periods"]["today"] == {
        "label": "今天",
        "orders": 3,
        "ad_spend_usd": 8.0,
        "roas": 1.5,
    }
    assert payload["summary"]["periods"]["yesterday"]["orders"] == 2
    assert payload["summary"]["periods"]["last_7d"]["ad_spend_usd"] == 30.0
    assert payload["summary"]["periods"]["last_30d"]["roas"] == 2.4
    de = next(item for item in payload["markets"] if item["lang"] == "de")
    assert de["delivery_status"] == "active"
    assert de["orders"]["last_7d"] == 4
    assert payload["data_quality"]["status"] == "ok"


def test_market_delivery_status_prefers_lang_ad_summary_cache(monkeypatch):
    def fake_query_one(sql, params=()):
        if "LOWER(product_code)=LOWER" in sql:
            return {
                "id": 600,
                "name": "煮蛋器",
                "product_code": "rapid-7-egg-electric-boiler-rjc",
                "shopifyid": "8591139963053",
                "shopify_title": "Rapid 7-Egg Electric Boiler",
            }
        return None

    monkeypatch.setattr(mod, "query", lambda *args, **kwargs: [])
    monkeypatch.setattr(mod, "query_one", fake_query_one)
    monkeypatch.setattr(mod, "current_meta_business_date", lambda: date(2026, 6, 11))
    monkeypatch.setattr(
        mod.media_product_ad_status_cache,
        "get_product_ad_summary_cache",
        lambda pids: {
            600: {
                "delivery_status": "active",
                "overall_roas": 2.01,
                "ad_spend_usd": 1533.85,
                "active_7d_ad_spend_usd": 242.12,
                "computed_at": "2026-06-11T10:22:35",
            }
        },
    )
    monkeypatch.setattr(
        mod.media_product_ad_status_cache,
        "get_product_lang_ad_summary_cache",
        lambda pids: {
            600: {
                "de": {
                    "delivery_status": "active",
                    "active_7d_ad_spend_usd": 14.31,
                    "ad_spend_usd": 93.89,
                    "ad_roas": 1.0976,
                }
            }
        },
    )
    monkeypatch.setattr(
        mod.media_product_order_stats,
        "get_product_order_stats",
        lambda pids, today=None: {600: {"total": {"today": 0, "yesterday": 12, "last_7d": 67, "last_30d": 82}}},
    )
    monkeypatch.setattr(
        mod.media_product_ad_orders_report,
        "get_product_ad_orders_report",
        lambda product_id, today=None: {
            "total": {"total_orders": 82, "total_spend": 1296.69, "total_roas": 1.93},
            "by_lang": {
                "de": {
                    "today_orders": 0,
                    "yesterday_orders": 0,
                    "last_7d_orders": 2,
                    "last_30d_orders": 2,
                    "today_spend": 0,
                    "last_7d_spend": 79.83,
                    "total_spend": 79.83,
                    "last_7d_roas": 1.29,
                    "total_roas": 1.29,
                }
            },
        },
    )

    payload = mod.build_insights_response({"product_code": "rapid-7-egg-electric-boiler-rjc"})

    de = next(item for item in payload["markets"] if item["lang"] == "de")
    assert de["delivery_status"] == "active"
    assert de["delivery_label"] == "投放中"
    assert de["orders"]["last_7d"] == 2
    assert de["last_7d_spend_usd"] == 79.83


def test_build_insights_response_uses_product_code_fallback(monkeypatch):
    def fake_query_one(sql, params=()):
        if "LOWER(product_code)=LOWER" in sql:
            return {
                "id": 8,
                "name": "Code Product",
                "product_code": "code-product-rjc",
                "shopifyid": "",
                "shopify_title": "",
            }
        return None

    monkeypatch.setattr(mod, "query", lambda *args, **kwargs: [])
    monkeypatch.setattr(mod, "query_one", fake_query_one)
    monkeypatch.setattr(mod, "current_meta_business_date", lambda: date(2026, 6, 9))
    monkeypatch.setattr(mod.media_product_ad_status_cache, "get_product_ad_summary_cache", lambda pids: {})
    monkeypatch.setattr(mod.media_product_ad_status_cache, "get_product_lang_ad_summary_cache", lambda pids: {})
    monkeypatch.setattr(
        mod.media_product_order_stats,
        "get_product_order_stats",
        lambda pids, today=None: {8: {"total": {"today": 0, "yesterday": 1, "last_7d": 2, "last_30d": 3}}},
    )
    monkeypatch.setattr(
        mod.media_product_ad_orders_report,
        "get_product_ad_orders_report",
        lambda product_id, today=None: {"by_lang": {}},
    )

    payload = mod.build_insights_response({"product_code": "code-product-rjc"})

    assert payload["matched"] is True
    assert payload["product"]["match_method"] == "media_products.product_code"
    assert payload["summary"]["orders"]["yesterday"] == 1
    assert payload["data_quality"]["status"] == "warning"


def test_build_insights_response_no_match(monkeypatch):
    monkeypatch.setattr(mod, "query", lambda *args, **kwargs: [])
    monkeypatch.setattr(mod, "query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr(mod, "current_meta_business_date", lambda: date(2026, 6, 9))

    payload = mod.build_insights_response({"sku": "UNKNOWN-SKU"})

    assert payload["matched"] is False
    assert payload["product"] is None
    assert payload["summary"]["orders"]["last_7d"] == 0
    assert payload["data_quality"]["status"] == "warning"


def _make_route_client(monkeypatch):
    from flask import Flask
    from web.auth import login_manager
    from web.routes.dianxiaomi_procurement_insights import bp

    fake_user = {
        "id": 1,
        "username": "admin",
        "role": "admin",
        "permissions": None,
        "is_active": 1,
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 1 else None)

    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.config["WTF_CSRF_ENABLED"] = False
    login_manager.init_app(app)
    app.register_blueprint(bp)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    return client


def test_procurement_insights_route_returns_json(monkeypatch):
    captured = {}

    def fake_build(args):
        captured.update(args)
        return {
            "ok": True,
            "matched": False,
            "summary": {"orders": {"today": 0}},
            "data_quality": {"status": "warning"},
        }

    monkeypatch.setattr(
        "web.routes.dianxiaomi_procurement_insights.service.build_insights_response",
        fake_build,
    )

    client = _make_route_client(monkeypatch)
    response = client.get(
        "/dianxiaomi-procurement-insights/api/insights?sku=SKU-A&product_code=demo-rjc"
    )

    assert response.status_code == 200
    assert captured["sku"] == "SKU-A"
    assert captured["product_code"] == "demo-rjc"
    assert response.get_json()["ok"] is True


def test_procurement_insights_health_route(monkeypatch):
    client = _make_route_client(monkeypatch)
    response = client.get("/dianxiaomi-procurement-insights/api/health")

    assert response.status_code == 200
    assert response.get_json()["service"] == "dianxiaomi_procurement_insights"


def test_chrome_extension_manifest_and_assets():
    ext_dir = ROOT / "tools" / "dianxiaomi_procurement_insights" / "chrome_ext"
    manifest = json.loads((ext_dir / "manifest.json").read_text(encoding="utf-8"))
    background = (ext_dir / "background.js").read_text(encoding="utf-8")
    content = (ext_dir / "content.js").read_text(encoding="utf-8")
    styles = (ext_dir / "styles.css").read_text(encoding="utf-8")

    assert manifest["manifest_version"] == 3
    assert "https://*.dianxiaomi.com/*" in manifest["host_permissions"]
    assert f"http://{DEFAULT_SERVER_HOST}/*" in manifest["host_permissions"]
    assert manifest["background"]["service_worker"] == "background.js"
    assert manifest["content_scripts"][0]["js"] == ["content.js"]
    assert manifest["content_scripts"][0]["all_frames"] is True
    assert "const DEFAULT_BACKEND_BASE = `http://${DEFAULT_BACKEND_HOST_PARTS.join(\".\")}`;" in background
    assert ":8080" not in background
    assert "/dianxiaomi-procurement-insights/api/insights" in background
    assert 'credentials: "include"' in background
    assert "collectClues" in content
    assert "getDianxiaomiSkuCandidateTokens" in content
    assert "looksLikeNumericDelimitedSku" in content
    assert "lineStartSkuCandidates" in content
    assert "findPurchaseModal" in content
    assert "syncPanelPlacement" in content
    assert "setInterval(schedulePanelPlacement" in content
    assert "renderProductLinks" in content
    assert "产品中心" in content
    assert "订单中心" in content
    assert "/medias/?q=" in content
    assert "/order-analytics/dxm-orders-view/order-trend/" in content
    assert "renderPeriodRows" in content
    assert "dpi-period-table" in content
    assert "last_30d" in content
    assert ".dpi-modal-anchored" in styles
    assert ".dpi-product-actions" in styles
    assert ".dpi-total-value" in styles
    assert ".dpi-period-table" in styles


def test_chrome_extension_sku_candidates_cover_dianxiaomi_shapes():
    content_path = ROOT / "tools" / "dianxiaomi_procurement_insights" / "chrome_ext" / "content.js"
    script = f"""
const fs = require("fs");
const vm = require("vm");
const contentPath = {json.dumps(str(content_path))};
const code = fs.readFileSync(contentPath, "utf8");
class FakeElement {{}}
const context = {{
  console,
  Element: FakeElement,
  MutationObserver: function MutationObserver() {{ this.observe = () => undefined; }},
  chrome: {{ runtime: {{ lastError: null, sendMessage: (_message, callback) => callback({{ ok: true }}) }} }},
  window: {{
    innerWidth: 1600,
    innerHeight: 900,
    getComputedStyle: () => ({{ display: "block", visibility: "visible", opacity: "1" }}),
    addEventListener: () => undefined,
    setInterval: () => undefined,
    setTimeout: () => undefined,
    requestAnimationFrame: () => 1,
  }},
  document: {{
    readyState: "loading",
    addEventListener: () => undefined,
    body: {{ innerText: "" }},
    documentElement: {{ appendChild: () => undefined }},
    activeElement: null,
    getElementById: () => null,
    querySelectorAll: () => [],
    createElement: () => ({{ addEventListener: () => undefined, classList: {{ add: () => undefined, remove: () => undefined }}, style: {{}} }}),
  }},
}};
context.globalThis = context;
vm.runInNewContext(code, context, {{ filename: contentPath }});
const sampleText = [
  "0427-16411412",
  "0514-16428715-2",
  "45807908847785-1",
  "159464308921378826-99",
  "2305171755-04251136",
  "PM1999041-RED-L",
  "DG250315033-heiSkoda-1",
  "YI21513591334",
  "GQ2223686-",
  "3XL",
  "100cm",
  "2026-06-11",
  "$10.50",
].join("\\n");
console.log(JSON.stringify(context.getDianxiaomiSkuCandidateTokens(sampleText)));
"""
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr
    candidates = json.loads(result.stdout)
    assert "0427-16411412" in candidates
    assert "0514-16428715-2" in candidates
    assert "45807908847785-1" in candidates
    assert "159464308921378826-99" in candidates
    assert "2305171755-04251136" in candidates
    assert "PM1999041-RED-L" in candidates
    assert "DG250315033-heiSkoda-1" in candidates
    assert "YI21513591334" in candidates
    assert "GQ2223686-" in candidates
    assert "3XL" not in candidates
    assert "100cm" not in candidates
    assert "2026-06-11" not in candidates
