from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from flask import Response
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server


def _now() -> datetime:
    return datetime.now(UTC)


def _write_sample_video(path: Path) -> None:
    # Upload route only needs bytes plus a browser-supplied video/mp4 mimetype.
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42e2e-sample-video")


def _write_sample_cover(path: Path) -> None:
    Image.new("RGB", (96, 96), (37, 108, 196)).save(path, format="PNG")


@contextmanager
def _serve_app(app):
    server = make_server("127.0.0.1", 0, app)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_medias_raw_sources_flow(monkeypatch, tmp_path):
    import web.app as web_app

    monkeypatch.setattr(web_app, "_run_startup_recovery", lambda: None)
    monkeypatch.setattr(web_app, "recover_all_interrupted_tasks", lambda: None, raising=False)
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: {
            "id": 1,
            "username": "e2e-admin",
            "role": "admin",
            "is_active": 1,
        } if int(user_id) == 1 else None,
    )

    from web.app import create_app
    from web.routes import medias as medias_routes
    from appcore import medias as medias_dao
    from appcore import tos_clients
    import appcore.bulk_translate_runtime as btr

    state = {
        "next_raw_id": 1001,
        "next_item_id": 2001,
        "task_seq": 1,
        "products": {
            101: {
                "id": 101,
                "user_id": 1,
                "name": "E2E 原始素材产品",
                "product_code": "e2e-medias-raw",
                "localized_links_json": None,
                "link_check_tasks_json": None,
                "archived": 0,
                "created_at": _now(),
                "updated_at": _now(),
            },
        },
        "raw_sources": {},
        "media_items": [],
        "objects": {},
        "translate_calls": [],
    }

    def _product(pid: int):
        return state["products"][int(pid)]

    def _visible_raw_sources(pid: int):
        pid = int(pid)
        rows = [
            row for row in state["raw_sources"].values()
            if int(row["product_id"]) == pid and row.get("deleted_at") is None
        ]
        return sorted(rows, key=lambda row: row["id"])

    def _count_items(pid: int):
        return sum(1 for row in state["media_items"] if int(row["product_id"]) == int(pid))

    def _count_raw(pid: int):
        return len(_visible_raw_sources(pid))

    def _lang_coverage(pid: int):
        coverage = {}
        for row in state["media_items"]:
            if int(row["product_id"]) != int(pid):
                continue
            bucket = coverage.setdefault(row["lang"], {"items": 0, "copy": 0, "cover": False})
            bucket["items"] += 1
        return coverage

    def fake_list_products(_user_id, keyword="", archived=False, offset=0, limit=20):
        keyword = (keyword or "").strip().lower()
        rows = []
        for row in state["products"].values():
            if archived != bool(row.get("archived")):
                continue
            if keyword and keyword not in row["name"].lower() and keyword not in row["product_code"].lower():
                continue
            rows.append(row)
        rows.sort(key=lambda row: row["id"])
        return rows[offset:offset + limit], len(rows)

    monkeypatch.setattr(medias_dao, "list_products", fake_list_products)
    monkeypatch.setattr(medias_dao, "count_items_by_product", lambda pids: {int(pid): _count_items(pid) for pid in pids})
    monkeypatch.setattr(medias_dao, "count_raw_sources_by_product", lambda pids: {int(pid): _count_raw(pid) for pid in pids})
    monkeypatch.setattr(medias_dao, "first_thumb_item_by_product", lambda pids: {})
    monkeypatch.setattr(
        medias_dao,
        "list_item_filenames_by_product",
        lambda pids, limit_per=5: {
            int(pid): [row["filename"] for row in state["media_items"] if int(row["product_id"]) == int(pid)][:limit_per]
            for pid in pids
        },
    )
    monkeypatch.setattr(medias_dao, "lang_coverage_by_product", lambda pids: {int(pid): _lang_coverage(pid) for pid in pids})
    monkeypatch.setattr(medias_dao, "get_product_covers_batch", lambda pids: {int(pid): {} for pid in pids})
    monkeypatch.setattr(medias_dao, "parse_link_check_tasks_json", lambda raw: {})
    monkeypatch.setattr(medias_dao, "list_languages", lambda: [
        {"code": "en", "name_zh": "英语"},
        {"code": "de", "name_zh": "德语"},
        {"code": "fr", "name_zh": "法语"},
    ])
    monkeypatch.setattr(medias_dao, "is_valid_language", lambda code: code in {"en", "de", "fr"})
    monkeypatch.setattr(medias_dao, "get_product", lambda pid: _product(pid))
    monkeypatch.setattr(medias_dao, "list_raw_sources", lambda pid: list(_visible_raw_sources(pid)))

    def fake_create_raw_source(pid, user_id, **kwargs):
        rid = state["next_raw_id"]
        state["next_raw_id"] += 1
        row = {
            "id": rid,
            "product_id": int(pid),
            "user_id": int(user_id),
            "display_name": kwargs.get("display_name"),
            "video_object_key": kwargs["video_object_key"],
            "cover_object_key": kwargs["cover_object_key"],
            "duration_seconds": kwargs.get("duration_seconds"),
            "file_size": kwargs.get("file_size"),
            "width": kwargs.get("width"),
            "height": kwargs.get("height"),
            "sort_order": 0,
            "created_at": _now(),
            "deleted_at": None,
        }
        state["raw_sources"][rid] = row
        return rid

    monkeypatch.setattr(medias_dao, "create_raw_source", fake_create_raw_source)
    monkeypatch.setattr(medias_dao, "get_raw_source", lambda rid: state["raw_sources"].get(int(rid)))
    monkeypatch.setattr(medias_dao, "soft_delete_raw_source", lambda rid: state["raw_sources"][int(rid)].update({"deleted_at": _now()}))

    monkeypatch.setattr(medias_routes, "_can_access_product", lambda product: True)
    monkeypatch.setattr(medias_routes, "get_media_duration", lambda path: 6.0)
    monkeypatch.setattr(medias_routes, "probe_media_info_safe", lambda path: {"width": 540, "height": 960})

    monkeypatch.setattr(tos_clients, "is_media_bucket_configured", lambda: True)
    monkeypatch.setattr(
        tos_clients,
        "build_media_raw_source_key",
        lambda user_id, pid, kind, filename: f"e2e/{int(pid)}/{kind}/{state['next_raw_id']}-{Path(filename).name}",
    )

    def fake_upload_media_object(object_key, data, content_type=None, bucket=None):
        state["objects"][object_key] = {
            "data": bytes(data),
            "content_type": content_type or "application/octet-stream",
        }

    monkeypatch.setattr(tos_clients, "upload_media_object", fake_upload_media_object)
    monkeypatch.setattr(tos_clients, "delete_media_object", lambda object_key: state["objects"].pop(object_key, None))
    monkeypatch.setattr(
        tos_clients,
        "generate_signed_media_download_url",
        lambda object_key, expires=None: f"/__fake-media/{object_key}",
    )

    def fake_create_bulk_translate_task(**kwargs):
        task_id = f"task-e2e-{state['task_seq']}"
        state["task_seq"] += 1
        state["translate_calls"].append({"task_id": task_id, **kwargs})
        return task_id

    def fake_start_task(task_id, user_id):
        call = next(row for row in state["translate_calls"] if row["task_id"] == task_id)
        for lang in call["target_langs"]:
            for rid in call["raw_source_ids"]:
                item_id = state["next_item_id"]
                state["next_item_id"] += 1
                state["media_items"].append({
                    "id": item_id,
                    "product_id": int(call["product_id"]),
                    "lang": lang,
                    "filename": f"{lang}-{rid}.mp4",
                    "source_raw_id": int(rid),
                    "created_at": _now(),
                })

    monkeypatch.setattr(btr, "create_bulk_translate_task", fake_create_bulk_translate_task)
    monkeypatch.setattr(btr, "start_task", fake_start_task)

    app = create_app()

    @app.get("/__fake-media/<path:object_key>")
    def _fake_media(object_key: str):
        row = state["objects"].get(object_key)
        if not row:
            return Response(status=404)
        return Response(row["data"], mimetype=row["content_type"])

    serializer = app.session_interface.get_signing_serializer(app)
    assert serializer is not None

    video_path = tmp_path / "sample.mp4"
    cover_path = tmp_path / "sample.png"
    _write_sample_video(video_path)
    _write_sample_cover(cover_path)

    session_cookie = serializer.dumps({"_user_id": "1", "_fresh": True})

    with _serve_app(app) as base_url:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_cookies([{
                "name": app.config.get("SESSION_COOKIE_NAME", "session"),
                "value": session_cookie,
                "url": base_url,
            }])
            page = context.new_page()

            page.goto(f"{base_url}/medias/", wait_until="networkidle")

            raw_btn = page.get_by_role("button", name="原始视频 (0)")
            expect(raw_btn).to_be_visible()
            raw_btn.click()

            raw_modal = page.locator("#rsModal")
            expect(raw_modal).to_be_visible()
            expect(raw_modal.get_by_role("heading", name="原始去字幕素材")).to_be_visible()
            expect(raw_modal.get_by_role("button", name="上传素材")).to_be_visible()
            close_btn = page.locator("#rsModalClose")
            expect(close_btn).to_be_visible()
            expect(page.locator("#rsDrawer")).to_have_count(0)

            page.get_by_role("button", name="上传素材").click()
            expect(page.locator("#rsUploadMask")).to_be_visible()
            expect(page.locator("#rsUploadCoverBox")).to_be_visible()
            expect(page.locator("#rsUploadVideoBox")).to_be_visible()
            with page.expect_file_chooser() as video_chooser:
                page.locator("#rsUploadVideoBox").click()
            video_chooser.value.set_files(str(video_path))
            with page.expect_file_chooser() as cover_chooser:
                page.locator("#rsUploadCoverBox").click()
            cover_chooser.value.set_files(str(cover_path))
            expect(page.locator("#rsUploadCoverPreview")).to_be_visible()
            expect(page.locator("#rsUploadVideoName")).to_contain_text("sample.mp4")
            expect(page.locator("#rsDisplayName")).to_have_value("sample.mp4")
            page.get_by_role("button", name="提交").click()

            expect(page.get_by_role("button", name="原始视频 (1)")).to_be_visible()
            card = page.locator("#rsList [data-rs-id='1001']")
            expect(card).to_be_visible()
            expect(card.get_by_role("button", name="封面图")).to_be_visible()
            expect(card.get_by_role("button", name="视频")).to_be_visible()
            expect(card.locator(".oc-rs-meta-line")).to_contain_text("时长")

            card.get_by_role("button", name="视频").click()
            expect(card.locator("video")).to_be_visible()
            video_src = card.locator("video").first.get_attribute("src")
            assert video_src is not None
            assert video_src.endswith("/medias/raw-sources/1001/video")
            close_btn.click()

            state["objects"].pop(state["raw_sources"][1001]["video_object_key"], None)
            page.get_by_role("button", name="原始视频 (1)").click()
            failing_card = page.locator("#rsList [data-rs-id='1001']")
            expect(failing_card).to_be_visible()
            failing_card.get_by_role("button", name="视频").click()
            expect(failing_card.locator(".vvideo-ph.err")).to_contain_text("视频加载失败")
            page.locator("#rsModalClose").click()

            page.locator(".js-translate").first.click()
            expect(page.locator("#rsTranslateDialog")).to_be_visible()
            expect(page.locator("#rstRsList")).to_contain_text("sample.mp4")
            page.locator("#rstLangs label", has_text="德语").click()
            expect(page.locator("#rstPreview")).to_contain_text("1 × 1 = 1")
            page.get_by_role("button", name="提交翻译").click()

            page.wait_for_url(f"{base_url}/tasks/task-e2e-1")
            browser.close()

    assert len(state["translate_calls"]) == 1
    assert state["translate_calls"][0]["raw_source_ids"] == [1001]
    assert len(state["media_items"]) == 1
    assert state["media_items"][0]["lang"] == "de"
    assert state["media_items"][0]["source_raw_id"] == 1001
