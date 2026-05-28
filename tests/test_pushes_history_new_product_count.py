from datetime import datetime
import json

def test_pushes_api_history_new_product_count_and_serialization(authed_client_no_db, monkeypatch):
    """验证 pushes api history 返回的 json 数据中正确包含了新产品推送标识和计数。"""
    rows = [
        {
            "log_id": 1,
            "item_id": 1001,
            "operator_user_id": 1,
            "status": "success",
            "request_payload": '{"videos": [{"url": "a.mp4"}], "texts": [], "product_links": []}',
            "response_body": "",
            "pushed_at": datetime(2026, 5, 26, 12, 0, 0),
            "lang": "ja",
            "display_name": "ja-demo.mp4",
            "filename": "ja-demo.mp4",
            "duration_seconds": 10.0,
            "file_size": 1000,
            "product_id": 317,
            "product_name": "Insects Set",
            "product_code": "glow-go-insect-set-rjc",
            "operator_username": "admin",
            "is_new_product_push": 1  # 这一条是新品推送
        },
        {
            "log_id": 2,
            "item_id": 1002,
            "operator_user_id": 1,
            "status": "success",
            "request_payload": '{"videos": [{"url": "b.mp4"}], "texts": [], "product_links": []}',
            "response_body": "",
            "pushed_at": datetime(2026, 5, 27, 12, 0, 0),
            "lang": "ja",
            "display_name": "ja-demo2.mp4",
            "filename": "ja-demo2.mp4",
            "duration_seconds": 12.0,
            "file_size": 1200,
            "product_id": 318,
            "product_name": "Water Gun",
            "product_code": "glow-go-water-gun-rjc",
            "operator_username": "admin",
            "is_new_product_push": 0  # 这一条不是新品推送
        }
    ]
    
    db_calls = []
    def fake_db_query(sql, args=()):
        db_calls.append((sql, args))
        if "media_push_logs" in sql:
            return rows
        elif "meta_ad_daily_campaign_metrics" in sql or "meta_ad_daily_ad_metrics" in sql:
            return [
                {
                    "total_spend": 300.0,
                    "total_purchase_value": 600.0,
                    "campaign_count": 2
                }
            ]
        return []
        
    monkeypatch.setattr("appcore.db.query", fake_db_query)
    
    resp = authed_client_no_db.get("/pushes/api/history?page=1")
    assert resp.status_code == 200
    data = resp.get_json()
    
    # 验证返回列表的长度和属性
    assert len(data["items"]) == 2
    assert data["items"][0]["is_new_product_push"] is True
    assert data["items"][1]["is_new_product_push"] is False
    
    # 验证新品数量的正确计算 (1 + 0 = 1)
    assert data["new_product_count"] == 1
    assert data["total"] == 2
