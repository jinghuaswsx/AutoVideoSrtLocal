# tests/test_backfill_push_history.py
import pytest
from scripts.backfill_push_history import backfill

def test_backfill_no_items(monkeypatch):
    """当没有需要补全历史记录的 items 时，什么都不做。"""
    queried_sql = []
    
    def mock_query(sql, args=()):
        queried_sql.append(sql)
        return []
        
    monkeypatch.setattr("scripts.backfill_push_history.query", mock_query)
    
    # 运行回填
    backfill()
    
    assert len(queried_sql) > 0
    assert "media_push_logs" in queried_sql[0]


def test_backfill_success_with_item(monkeypatch):
    """当有 item 且 product 存在时，应该成功创建快照日志并更新 item。"""
    mock_items = [{
        "id": 101,
        "product_id": 201,
        "pushed_at": "2026-05-26 12:00:00",
        "display_name": "Test Video",
        "filename": "test.mp4",
        "file_size": 1024,
        "object_key": "videos/test.mp4",
        "cover_object_key": "covers/test.jpg",
        "lang": "de"
    }]
    
    mock_product = {
        "id": 201,
        "name": "Deutscher Artikel",
        "importance": 3,
        "selling_points": "Points",
        "product_code": "DE-01"
    }
    
    queries = []
    executes = []
    
    def mock_query(sql, args=()):
        queries.append((sql, args))
        return mock_items
        
    def mock_query_one(sql, args=()):
        queries.append((sql, args))
        if "media_products" in sql:
            return mock_product
        return None
        
    def mock_execute(sql, args=()):
        executes.append((sql, args))
        if "INSERT INTO media_push_logs" in sql:
            return 999  # 返回 log_id
        return 1
        
    def mock_build_item_payload(item, product):
        return {"test_payload": "success_payload"}
        
    monkeypatch.setattr("scripts.backfill_push_history.query", mock_query)
    monkeypatch.setattr("scripts.backfill_push_history.query_one", mock_query_one)
    monkeypatch.setattr("scripts.backfill_push_history.execute", mock_execute)
    monkeypatch.setattr("scripts.backfill_push_history.build_item_payload", mock_build_item_payload)
    
    # 运行回填
    backfill()
    
    # 验证是否执行了插入和更新
    insert_call = [e for e in executes if "INSERT INTO media_push_logs" in e[0]]
    update_call = [e for e in executes if "UPDATE media_items" in e[0]]
    
    assert len(insert_call) == 1
    assert len(update_call) == 1
    
    # 检查插入的数据
    sql, args = insert_call[0]
    assert args[0] == 101  # item_id
    assert "success_payload" in args[2]  # json payload
    assert args[4] == "2026-05-26 12:00:00"  # pushed_at
    
    # 检查更新的数据
    sql, args = update_call[0]
    assert args[0] == 999  # latest_push_id
    assert args[1] == 101  # item_id


def test_backfill_fallback_when_payload_fails(monkeypatch):
    """当 build_item_payload 失败时，应该使用 fallback 并成功插入记录。"""
    mock_items = [{
        "id": 102,
        "product_id": 202,
        "pushed_at": "2026-05-26 13:00:00",
        "display_name": "Test Video Fallback",
        "filename": "fallback.mp4",
        "file_size": 2048,
        "object_key": "videos/fallback.mp4",
        "cover_object_key": "covers/fallback.jpg",
        "lang": "fr"
    }]
    
    mock_product = {
        "id": 202,
        "name": "Fallback Article",
        "importance": 4,
        "selling_points": "Fallback Points",
        "product_code": "FR-01"
    }
    
    executes = []
    
    def mock_query(sql, args=()):
        return mock_items
        
    def mock_query_one(sql, args=()):
        if "media_products" in sql:
            return mock_product
        return None
        
    def mock_execute(sql, args=()):
        executes.append((sql, args))
        if "INSERT INTO media_push_logs" in sql:
            return 888
        return 1
        
    def mock_build_item_payload_error(item, product):
        raise ValueError("Simulated build error")
        
    monkeypatch.setattr("scripts.backfill_push_history.query", mock_query)
    monkeypatch.setattr("scripts.backfill_push_history.query_one", mock_query_one)
    monkeypatch.setattr("scripts.backfill_push_history.execute", mock_execute)
    monkeypatch.setattr("scripts.backfill_push_history.build_item_payload", mock_build_item_payload_error)
    
    # 运行
    backfill()
    
    # 验证 fallback 逻辑插入是否正常
    insert_call = [e for e in executes if "INSERT INTO media_push_logs" in e[0]]
    assert len(insert_call) == 1
    sql, args = insert_call[0]
    assert args[0] == 102
    assert "fallback.mp4" in args[2]  # json payload contains video filename
    assert args[4] == "2026-05-26 13:00:00"


def test_backfill_updates_existing_hollow_log(monkeypatch):
    """当存在空壳的成功日志时，应该使用 UPDATE 原地修复，并更新 item。"""
    mock_items = [{
        "id": 103,
        "product_id": 203,
        "pushed_at": "2026-05-26 14:00:00",
        "display_name": "Test Hollow",
        "filename": "hollow.mp4",
        "file_size": 512,
        "object_key": "videos/hollow.mp4",
        "cover_object_key": "covers/hollow.jpg",
        "lang": "ja"
    }]
    
    mock_product = {
        "id": 203,
        "name": "Japanischer Artikel",
        "importance": 2,
        "selling_points": "Japan Points",
        "product_code": "JP-01"
    }
    
    queries = []
    executes = []
    
    def mock_query(sql, args=()):
        queries.append((sql, args))
        return mock_items
        
    def mock_query_one(sql, args=()):
        queries.append((sql, args))
        if "media_products" in sql:
            return mock_product
        if "media_push_logs" in sql:
            return {"id": 777}  # 模拟已存在的空壳日志 ID
        return None
        
    def mock_execute(sql, args=()):
        executes.append((sql, args))
        return 1
        
    def mock_build_item_payload(item, product):
        return {"test_payload": "hollow_restored_payload"}
        
    monkeypatch.setattr("scripts.backfill_push_history.query", mock_query)
    monkeypatch.setattr("scripts.backfill_push_history.query_one", mock_query_one)
    monkeypatch.setattr("scripts.backfill_push_history.execute", mock_execute)
    monkeypatch.setattr("scripts.backfill_push_history.build_item_payload", mock_build_item_payload)
    
    # 运行回填
    backfill()
    
    # 验证是否执行了 UPDATE 修复和更新
    update_log_call = [e for e in executes if "UPDATE media_push_logs" in e[0]]
    update_item_call = [e for e in executes if "UPDATE media_items" in e[0]]
    
    assert len(update_log_call) == 1
    assert len(update_item_call) == 1
    
    # 检查 UPDATE 日志的内容
    sql, args = update_log_call[0]
    assert "hollow_restored_payload" in args[0]  # json payload
    assert args[1] == "Restored and fixed hollow payload"  # response_body
    assert args[2] == "2026-05-26 14:00:00"  # pushed_at
    assert args[3] == 777  # log_id (existing log)
    
    # 检查 UPDATE item 的内容
    sql, args = update_item_call[0]
    assert args[0] == 777  # latest_push_id
    assert args[1] == 103  # item_id

