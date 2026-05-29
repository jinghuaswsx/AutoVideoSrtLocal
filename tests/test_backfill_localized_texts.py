# tests/test_backfill_localized_texts.py
import json
import pytest
from scripts.backfill_localized_texts_in_push_history import backfill

def test_backfill_with_available_copywriting(monkeypatch):
    # 模拟 media_push_logs 返回一行记录
    initial_payload = {
        "product_name": "Test product",
        "texts": [
            {
                "title": "English Title",
                "message": "English Message",
                "description": "English Desc",
                "lang": "英语 EN",
            }
        ]
    }
    
    mock_log = {
        "log_id": 42,
        "item_id": 100,
        "product_id": 10,
        "lang": "de",
        "display_name": "demo.mp4",
        "filename": "demo.mp4",
        "request_payload": json.dumps(initial_payload, ensure_ascii=False)
    }
    
    queries = []
    executes = []
    
    def mock_query(sql, args=()):
        queries.append((sql, args))
        return [mock_log]
        
    def mock_execute(sql, args=()):
        executes.append((sql, args))
        return 1
        
    monkeypatch.setattr("scripts.backfill_localized_texts_in_push_history.query", mock_query)
    monkeypatch.setattr("scripts.backfill_localized_texts_in_push_history.execute", mock_execute)
    
    # 模拟找到了匹配的德语文案
    de_copy = {
        "title": "DE Title",
        "message": "DE Message",
        "description": "DE Description",
        "lang": "德语 DE",
    }
    monkeypatch.setattr(
        "scripts.backfill_localized_texts_in_push_history.resolve_localized_text_payload",
        lambda item: de_copy if item.get("lang") == "de" else None
    )
    
    # 运行回填
    backfill()
    
    # 验证执行了 UPDATE
    assert len(executes) == 1
    update_sql, args = executes[0]
    assert "UPDATE media_push_logs SET request_payload =" in update_sql
    assert args[1] == 42 # log_id
    
    # 验证回填后的内容是德语文案
    updated_payload = json.loads(args[0])
    assert updated_payload["texts"] == [de_copy]

def test_backfill_skips_when_localized_copywriting_missing(monkeypatch):
    initial_payload = {
        "product_name": "Test product",
        "texts": [
            {
                "title": "English Title",
                "message": "English Message",
                "description": "English Desc",
                "lang": "英语 EN",
            }
        ]
    }
    
    mock_log = {
        "log_id": 43,
        "item_id": 101,
        "product_id": 10,
        "lang": "fr",
        "display_name": "demo.mp4",
        "filename": "demo.mp4",
        "request_payload": json.dumps(initial_payload, ensure_ascii=False)
    }
    
    queries = []
    executes = []
    
    def mock_query(sql, args=()):
        queries.append((sql, args))
        return [mock_log]
        
    def mock_execute(sql, args=()):
        executes.append((sql, args))
        return 1
        
    monkeypatch.setattr("scripts.backfill_localized_texts_in_push_history.query", mock_query)
    monkeypatch.setattr("scripts.backfill_localized_texts_in_push_history.execute", mock_execute)
    
    # 模拟未找到法语小语种文案（当时是兜底英文）
    monkeypatch.setattr(
        "scripts.backfill_localized_texts_in_push_history.resolve_localized_text_payload",
        lambda item: None
    )
    
    # 运行回填
    backfill()
    
    # 验证没有执行任何 UPDATE，优雅跳过以保持英文原样兜底不损毁
    assert len(executes) == 0
