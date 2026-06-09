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


def test_pushes_api_history_video_size_filter(authed_client_no_db, monkeypatch):
    """验证 pushes api history 接口在传入不同 video_size 参数时能正确过滤和拼接 SQL。"""
    captured_queries = []
    
    def fake_db_query(sql, args=()):
        captured_queries.append((sql, args))
        if "media_push_logs" in sql:
            return [
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
                    "is_new_product_push": 1
                }
            ]
        elif "meta_ad_daily_campaign_metrics" in sql or "meta_ad_daily_ad_metrics" in sql:
            return []
        return []

    monkeypatch.setattr("appcore.db.query", fake_db_query)
    monkeypatch.setattr("appcore.pushes.query", fake_db_query)

    # 1. 验证 large 视频大小过滤 (file_size >= 100M)
    captured_queries.clear()
    resp = authed_client_no_db.get("/pushes/api/history?video_size=large")
    assert resp.status_code == 200
    history_sql = [q[0] for q in captured_queries if "FROM media_push_logs" in q[0]][0]
    assert "i.file_size >= 104857600" in history_sql
    assert "i.file_size < 104857600" not in history_sql

    # 2. 验证 small 视频大小过滤 (file_size < 100M)
    captured_queries.clear()
    resp = authed_client_no_db.get("/pushes/api/history?video_size=small")
    assert resp.status_code == 200
    history_sql = [q[0] for q in captured_queries if "FROM media_push_logs" in q[0]][0]
    assert "i.file_size < 104857600" in history_sql
    assert "i.file_size >= 104857600" not in history_sql

    # 3. 验证 all 视频大小过滤 (不包含 size 过滤)
    captured_queries.clear()
    resp = authed_client_no_db.get("/pushes/api/history?video_size=")
    assert resp.status_code == 200
    history_sql = [q[0] for q in captured_queries if "FROM media_push_logs" in q[0]][0]
    assert "i.file_size >=" not in history_sql
    assert "i.file_size <" not in history_sql


def test_pushes_api_items_video_size_filter(authed_client_no_db, monkeypatch):
    """验证 pushes api items 接口在传入不同 video_size 参数时能正确进行 SQL 拼装和过滤。"""
    captured_queries = []
    
    def fake_db_query(sql, args=()):
        captured_queries.append((sql, args))
        if "FROM media_items" in sql:
            return [
                {
                    "id": 1001,
                    "product_id": 317,
                    "product_name": "Insects Set",
                    "product_code": "glow-go-insect-set-rjc",
                    "lang": "ja",
                    "filename": "ja-demo.mp4",
                    "display_name": "ja-demo.mp4",
                    "duration_seconds": 10.0,
                    "file_size": 1000,
                    "created_at": datetime(2026, 5, 26, 12, 0, 0),
                    "pushed_at": None,
                    "owner_name": "admin"
                }
            ]
        return []
        
    def fake_db_query_one(sql, args=()):
        captured_queries.append((sql, args))
        if "COUNT(*)" in sql:
            return {"c": 1}
        return None

    monkeypatch.setattr("appcore.db.query", fake_db_query)
    monkeypatch.setattr("appcore.db.query_one", fake_db_query_one)
    monkeypatch.setattr("appcore.pushes.query", fake_db_query)
    monkeypatch.setattr("appcore.pushes.query_one", fake_db_query_one)
    monkeypatch.setattr("appcore.pushes.status_cache_for_rows", lambda rows: {})

    # 1. large 视频大小过滤 (file_size >= 100M)
    captured_queries.clear()
    resp = authed_client_no_db.get("/pushes/api/items?video_size=large")
    assert resp.status_code == 200
    items_sql = [q[0] for q in captured_queries if "FROM media_items" in q[0]][0]
    assert "i.file_size >= 104857600" in items_sql
    assert "i.file_size < 104857600" not in items_sql

    # 2. small 视频大小过滤 (file_size < 100M)
    captured_queries.clear()
    resp = authed_client_no_db.get("/pushes/api/items?video_size=small")
    assert resp.status_code == 200
    items_sql = [q[0] for q in captured_queries if "FROM media_items" in q[0]][0]
    assert "i.file_size < 104857600" in items_sql
    assert "i.file_size >= 104857600" not in items_sql

    # 3. all 视频大小过滤 (不包含 size 过滤)
    captured_queries.clear()
    resp = authed_client_no_db.get("/pushes/api/items?video_size=")
    assert resp.status_code == 200
    items_sql = [q[0] for q in captured_queries if "FROM media_items" in q[0]][0]
    assert "i.file_size >=" not in items_sql
    assert "i.file_size <" not in items_sql

