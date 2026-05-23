"""Rework screenshot upload and display tests."""

import io
import os
import json
import pytest
from flask import Flask


def test_upload_rework_screenshot_success(authed_client_no_db, monkeypatch, tmp_path):
    # Mock config.UPLOAD_DIR to a safe temp path
    monkeypatch.setattr("web.routes.pushes.config.UPLOAD_DIR", str(tmp_path))
    
    # Mock medias.get_item
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {"id": item_id, "product_id": 456, "lang": "de"}
    )
    
    # Let's POST to upload endpoint
    data = {
        "file": (io.BytesIO(b"fake image data"), "screenshot.png")
    }
    resp = authed_client_no_db.post(
        "/pushes/api/items/123/upload-rework-screenshot",
        data=data,
        content_type="multipart/form-data"
    )
    
    assert resp.status_code == 200
    res = resp.get_json()
    assert "url" in res
    assert "/pushes/api/rework-screenshot/" in res["url"]
    
    filename = res["url"].split("/")[-1]
    assert filename.endswith(".png")
    
    # Let's verify file exists in temporary UPLOAD_DIR
    target_file = tmp_path / "rework_screenshots" / filename
    assert target_file.exists()
    assert target_file.read_bytes() == b"fake image data"
    
    # Now let's try to fetch this file via GET
    get_resp = authed_client_no_db.get(res["url"])
    assert get_resp.status_code == 200
    assert get_resp.data == b"fake image data"


def test_upload_rework_screenshot_invalid_type(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {"id": item_id, "product_id": 456}
    )
    # Post non-image type
    data = {
        "file": (io.BytesIO(b"fake video data"), "video.mp4")
    }
    resp = authed_client_no_db.post(
        "/pushes/api/items/123/upload-rework-screenshot",
        data=data,
        content_type="multipart/form-data"
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "invalid_file_type"


def test_reject_to_task_with_image_urls(authed_client_no_db, monkeypatch):
    captured = {}
    
    monkeypatch.setattr(
        "web.routes.pushes.medias.get_item",
        lambda item_id: {"id": item_id, "product_id": 456, "task_id": 789}
    )
    
    def fake_reject_child_from_push(*, task_id, actor_user_id, issue_keys, reason, image_urls=None):
        captured["task_id"] = task_id
        captured["actor_user_id"] = actor_user_id
        captured["issue_keys"] = issue_keys
        captured["reason"] = reason
        captured["image_urls"] = image_urls
        return {
            "task_id": task_id,
            "status": "assigned",
            "issue_keys": issue_keys,
            "reason": reason,
            "image_urls": image_urls or []
        }
        
    monkeypatch.setattr(
        "web.routes.pushes.tasks_svc.reject_child_from_push",
        fake_reject_child_from_push
    )
    
    monkeypatch.setattr(
        "web.routes.pushes.pushes.refresh_push_status_cache_for_item",
        lambda item_id: None
    )
    
    payload = {
        "issue_keys": ["subtitle_removal"],
        "reason": "Test rework reason with pasted screenshot",
        "image_urls": ["/pushes/api/rework-screenshot/abc.png"]
    }
    
    resp = authed_client_no_db.post(
        "/pushes/api/items/123/reject-to-task",
        data=json.dumps(payload),
        content_type="application/json"
    )
    
    assert resp.status_code == 200
    assert captured["task_id"] == 789
    assert captured["issue_keys"] == ["subtitle_removal"]
    assert captured["reason"] == "Test rework reason with pasted screenshot"
    assert captured["image_urls"] == ["/pushes/api/rework-screenshot/abc.png"]
