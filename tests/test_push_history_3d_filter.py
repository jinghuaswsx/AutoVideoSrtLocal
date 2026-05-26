# tests/test_push_history_3d_filter.py
import pytest
from datetime import date, timedelta

def test_api_history_none_3d_filtration(monkeypatch, authed_client_no_db):
    """测试 api_history 在传入 ad_plan=none_3d 参数时，能够正确以天维度过滤 3 天以上的无广告记录。"""
    
    # 获取今天及相关日期对象
    today = date.today()
    date_2d_ago = today - timedelta(days=2)
    date_3d_ago = today - timedelta(days=3)
    date_4d_ago = today - timedelta(days=4)
    
    # 构造模拟的 media_push_logs / media_items / media_products 联合查询返回值
    mock_db_rows = [
        {
            "log_id": 1,
            "item_id": 101,
            "product_id": 201,
            "product_name": "Product A",
            "product_code": "code-a",
            "lang": "es",
            "display_name": "video1.mp4",
            "filename": "video1.mp4",
            "file_size": 10485760,
            "pushed_at": date_3d_ago, # 3天前推送，无广告 (应被筛选出)
            "operator_username": "admin",
            "request_payload": '{"videos": [{"url": "http://v.com/1", "image_url": "http://v.com/c1"}], "texts": [], "product_links": []}',
            "response_body": "{}"
        },
        {
            "log_id": 2,
            "item_id": 102,
            "product_id": 202,
            "product_name": "Product B",
            "product_code": "code-b",
            "lang": "fr",
            "display_name": "video2.mp4",
            "filename": "video2.mp4",
            "file_size": 20485760,
            "pushed_at": date_2d_ago, # 2天前推送，无广告 (不满足满3天，应被过滤排除)
            "operator_username": "admin",
            "request_payload": '{"videos": [{"url": "http://v.com/2", "image_url": "http://v.com/c2"}], "texts": [], "product_links": []}',
            "response_body": "{}"
        },
        {
            "log_id": 3,
            "item_id": 103,
            "product_id": 203,
            "product_name": "Product C",
            "product_code": "code-c",
            "lang": "de",
            "display_name": "video3.mp4",
            "filename": "video3.mp4",
            "file_size": 30485760,
            "pushed_at": date_4d_ago, # 4天前推送，有广告计划 (有广告，应被过滤排除)
            "operator_username": "admin",
            "request_payload": '{"videos": [{"url": "http://v.com/3", "image_url": "http://v.com/c3"}], "texts": [], "product_links": []}',
            "response_body": "{}"
        }
    ]
    
    # 模拟 db_query
    def mock_query(sql, args=()):
        if "media_push_logs" in sql:
            if "INTERVAL 3 DAY" in sql:
                return [mock_db_rows[0]]
            return mock_db_rows
        # 对于广告统计数据查询，仅 203 存在一条广告记录
        if "meta_ad_daily_campaign_metrics" in sql or "meta_ad_daily_ad_metrics" in sql:
            if args and args[0] == 203:
                return [
                    {
                        "product_id": 203,
                        "market_country": "DE",
                        "total_spend": 150.0,
                        "campaign_count": 2
                    }
                ]
            return [
                {
                    "total_spend": 0.0,
                    "campaign_count": 0
                }
            ]
        return []
        
    monkeypatch.setattr("appcore.db.query", mock_query)
    
    # 使用无数据库认证测试客户端访问 API
    resp = authed_client_no_db.get('/pushes/api/history?ad_plan=none_3d')
    assert resp.status_code == 200
    
    data = resp.get_json()
    items = data["items"]
    
    # 验证仅有一项输出（log_id 为 1 的 Product A）
    assert len(items) == 1
    assert items[0]["log_id"] == 1
    assert items[0]["product_code"] == "code-a"
    assert items[0]["has_ad_plan"] is False
