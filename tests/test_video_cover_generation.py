from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path

import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def _png_bytes(size=(640, 640), color=(20, 120, 180)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpg_file(path: Path, size=(720, 1280), color=(180, 80, 20)) -> Path:
    Image.new("RGB", size, color).save(path, format="JPEG")
    return path


class _FakeProduct:
    title = "Portable Blender Pro"
    main_image_url = "https://cdn.example/blender.png"
    price_min = 39.99
    price_max = 39.99
    currency = "USD"


def test_generate_video_covers_uses_product_and_video_references(tmp_path, monkeypatch):
    from appcore import local_media_storage
    from appcore.video_cover_generation import generate_video_covers

    video_path = tmp_path / "input.mp4"
    video_path.write_bytes(b"fake video")
    calls = []
    ad_copy_calls = []
    analysis_calls = []

    def fake_thumbnail(video_path_arg: str, output_dir: str, scale=None):
        assert Path(video_path_arg) == video_path
        return str(_jpg_file(Path(output_dir) / "thumbnail.jpg"))

    def fake_generate_image(prompt: str, *, source_image: bytes, source_mime: str, **kwargs):
        calls.append({"prompt": prompt, "source_image": source_image, "source_mime": source_mime, "kwargs": kwargs})
        with Image.open(BytesIO(source_image)) as img:
            assert img.size == (1080, 1920)
        return _png_bytes(size=(900, 900), color=(30, 160, 210)), "image/png"

    def fake_analysis(use_case_code: str, **kwargs):
        analysis_calls.append({"use_case_code": use_case_code, "kwargs": kwargs})
        if use_case_code == "video_cover.video_analysis":
            assert Path(kwargs["media"]).is_file()
            return {"text": "video_text: fresh smoothie\ncover_reference: hand using blender"}
        if use_case_code == "video_cover.product_analysis":
            media = kwargs["media"]
            assert Path(media).is_file()
            return {"text": "<产品分析报告>\n<使用方式解析>\n手持搅拌杯制作奶昔</产品分析报告>"}
        raise AssertionError(use_case_code)

    def fake_ad_copy(use_case_code: str, **kwargs):
        ad_copy_calls.append({"use_case_code": use_case_code, "kwargs": kwargs})
        return {
            "text": json.dumps(
                {
                    "ad_copy_sets": [
                        {
                            "id": idx,
                            "angle": "痛点解决型",
                            "english": {
                                "headline": "Blend Anywhere",
                                "body_text": "Make smoothies without dragging out the big blender.",
                                "cta": "Shop Now",
                            },
                            "chinese_translation": {
                                "headline": "随处搅拌",
                                "body_text": "不用搬出大型搅拌机也能做奶昔。",
                                "cta": "立即购买",
                            },
                            "usage_note": "适合手持使用场景。",
                        }
                        for idx in range(1, 6)
                    ]
                },
                ensure_ascii=False,
            )
        }

    result = generate_video_covers(
        product_url="https://shop.example/products/blender",
        video_path=str(video_path),
        video_filename="demo.mp4",
        user_id=7,
        task_id="cover-task-1",
        product_fetch_fn=lambda url: _FakeProduct(),
        image_fetch_fn=lambda url: _png_bytes(size=(900, 900), color=(15, 90, 140)),
        thumbnail_extractor=fake_thumbnail,
        image_generate_fn=fake_generate_image,
        invoke_generate_fn=fake_analysis,
        ad_copy_invoke_fn=fake_ad_copy,
    )

    assert result["product"]["title"] == "Portable Blender Pro"
    assert result["product"]["main_image_url"] == "https://cdn.example/blender.png"
    assert result["model"]["channel"] == "local"
    assert result["model"]["model_id"] == "gpt-image-2"
    assert [cover["platform"] for cover in result["covers"]] == ["social_reels"]
    assert all(cover["width"] == 1080 and cover["height"] == 1920 for cover in result["covers"])
    assert all(local_media_storage.exists(cover["object_key"]) for cover in result["covers"])
    assert local_media_storage.exists(result["reference"]["object_key"])
    assert len(calls) == 1
    assert "Facebook Reels / Instagram Reels / TikTok / Shorts" in calls[0]["prompt"]
    assert "优秀的创意总监" in calls[0]["prompt"]
    assert "必须且只能添加一句简短英文 hook" in calls[0]["prompt"]
    assert '"headline": "Blend Anywhere"' in calls[0]["prompt"]
    assert "{product_analysis}" not in calls[0]["prompt"]
    assert "{video_analysis}" not in calls[0]["prompt"]
    assert "{ad_copy_sets}" not in calls[0]["prompt"]
    assert "Portable Blender Pro" in calls[0]["prompt"]
    assert calls[0]["kwargs"]["channel"] == "local"
    assert calls[0]["kwargs"]["model"] == "gpt-image-2"
    assert calls[0]["kwargs"]["service"] == "video_cover.generate"
    assert [call["use_case_code"] for call in analysis_calls] == [
        "video_cover.product_analysis",
        "video_cover.video_analysis",
    ]
    assert analysis_calls[0]["kwargs"]["provider_override"] == "openrouter"
    assert analysis_calls[0]["kwargs"]["model_override"] == "google/gemini-3-flash-preview"
    assert analysis_calls[1]["kwargs"]["provider_override"] == "gemini_vertex_adc"
    assert analysis_calls[1]["kwargs"]["model_override"] == "gemini-3.1-pro-preview"
    assert ad_copy_calls[0]["use_case_code"] == "video_cover.ad_copy"
    assert ad_copy_calls[0]["kwargs"]["response_format"] == {"type": "json_object"}
    assert ad_copy_calls[0]["kwargs"]["provider_override"] == "openrouter"
    assert ad_copy_calls[0]["kwargs"]["model_override"] == "google/gemini-3-flash-preview"
    assert "当前日期：" in ad_copy_calls[0]["kwargs"]["messages"][1]["content"]


def test_resolve_video_cover_model_options_matches_requested_mappings():
    from appcore.video_cover_generation import (
        resolve_cover_model_selection,
        resolve_text_model_selection,
        video_cover_model_options,
    )

    assert resolve_text_model_selection("video_analysis", "gemini_vertex_adc", "").model == "gemini-3.1-pro-preview"
    assert resolve_text_model_selection("video_analysis", "openrouter", "").model == "google/gemini-3.1-pro-preview"
    assert resolve_text_model_selection("product_analysis", "gemini_vertex_adc", "").model == "gemini-3-flash-preview"
    assert resolve_text_model_selection("ad_copy", "openrouter", "").model == "google/gemini-3-flash-preview"

    local = resolve_cover_model_selection("local", "gpt_image_2")
    assert local.provider == "local"
    assert local.model == "gpt-image-2"
    openrouter = resolve_cover_model_selection("openrouter", "nano_banana_pro")
    assert openrouter.provider == "openrouter"
    assert openrouter.model == "gemini-3-pro-image-preview"

    options = video_cover_model_options()
    assert options["steps"]["video_analysis"]["default_provider"] == "gemini_vertex_adc"
    assert "local" in options["steps"]["cover_generation"]["providers"]
    assert options["steps"]["cover_generation"]["models"]["local"]["gpt_image_2"] == "gpt-image-2"


def test_generate_local_cover_image_posts_docs_image_edit_payload():
    from appcore.video_cover_generation import generate_local_cover_image

    posted = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"b64_json": _png_bytes().hex()}]}

    def fake_post(url, *, headers, data, files, timeout):
        posted["url"] = url
        posted["headers"] = headers
        posted["data"] = data
        posted["files"] = files
        posted["timeout"] = timeout

        class B64Response:
            status_code = 200
            text = ""

            def json(self):
                import base64

                return {"data": [{"b64_json": base64.b64encode(_png_bytes()).decode("ascii")}]}

        return B64Response()

    out, mime = generate_local_cover_image(
        "make a cover",
        source_image=_png_bytes(),
        source_mime="image/png",
        model="gpt-image-2",
        api_key="sk-test",
        base_url="http://172.30.254.14:82/v1",
        post_fn=fake_post,
    )

    assert out.startswith(b"\x89PNG")
    assert mime == "image/png"
    assert posted["url"] == "http://172.30.254.14:82/v1/images/edits"
    assert posted["headers"]["Authorization"] == "Bearer sk-test"
    assert posted["data"]["model"] == "gpt-image-2"
    assert posted["data"]["prompt"] == "make a cover"
    assert posted["data"]["n"] == "1"
    assert posted["data"]["size"] == "1024x1536"
    assert posted["files"]["image"][0] == "reference.png"
    assert posted["files"]["image"][2] == "image/png"
    assert posted["files"]["image"][1].startswith(b"\x89PNG")


def test_generate_ad_copy_sets_uses_user_prompt_and_validates_json():
    from appcore.video_cover_generation import generate_ad_copy_sets

    captured = {}

    def fake_invoke(use_case_code: str, **kwargs):
        captured["use_case_code"] = use_case_code
        captured.update(kwargs)
        return {
            "text": json.dumps(
                {
                    "ad_copy_sets": [
                        {
                            "id": idx,
                            "angle": "痛点解决型",
                            "english": {
                                "headline": "Easy Daily Fix",
                                "body_text": "A simple upgrade for busy mornings.",
                                "cta": "Learn More",
                            },
                            "chinese_translation": {
                                "headline": "轻松日常改进",
                                "body_text": "适合忙碌早晨的小升级。",
                                "cta": "了解更多",
                            },
                            "usage_note": "适合生活方式场景。",
                        }
                        for idx in range(1, 6)
                    ]
                },
                ensure_ascii=False,
            )
        }

    result = generate_ad_copy_sets(
        product_analysis="<使用方式解析>\nHandheld blender",
        video_analysis="video_text: fresh smoothie",
        current_date="2026-05-14",
        user_id=7,
        task_id="cover-task-1",
        provider="gemini_vertex_adc",
        invoke_chat_fn=fake_invoke,
    )

    assert result["ad_copy_sets"][0]["english"]["headline"] == "Easy Daily Fix"
    assert captured["use_case_code"] == "video_cover.ad_copy"
    assert captured["provider_override"] == "gemini_vertex_adc"
    assert captured["model_override"] == "gemini-3-flash-preview"
    assert captured["response_format"] == {"type": "json_object"}
    prompt = captured["messages"][1]["content"]
    assert "资深 Facebook / Instagram Reels 视频广告文案专家" in prompt
    assert "产品分析：<使用方式解析>" in prompt
    assert "视频素材分析：video_text: fresh smoothie" in prompt
    assert "当前日期：2026-05-14" in prompt
    assert "ad_copy_sets" in prompt


def test_generate_video_analysis_optimizes_video_before_llm(tmp_path, monkeypatch):
    from appcore.llm_media_optimizer import OptimizedMedia
    from appcore.video_cover_generation import generate_video_analysis

    source = tmp_path / "source.mp4"
    optimized = tmp_path / "source.review480p.mp4"
    source.write_bytes(b"source")
    optimized.write_bytes(b"optimized")
    captured = {}

    def fake_prepare(video_path, policy, output_dir=None):
        captured["prepare"] = {
            "video_path": str(video_path),
            "policy": policy.name,
            "output_dir": str(output_dir),
        }
        return OptimizedMedia(
            original_path=str(source),
            llm_path=str(optimized),
            optimized=True,
            cleanup_path=str(optimized),
            original_bytes=6,
            llm_bytes=9,
            command=["ffmpeg", "-i", str(source), str(optimized)],
            policy_name=policy.name,
        )

    def fake_cleanup(media):
        captured["cleanup_path"] = media.cleanup_path

    def fake_invoke(use_case_code: str, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["media"] = kwargs["media"]
        return {"text": "video_text: demo\nvoiceover: demo"}

    monkeypatch.setattr("appcore.video_cover_generation.prepare_video_for_llm", fake_prepare)
    monkeypatch.setattr("appcore.video_cover_generation.cleanup_optimized_media", fake_cleanup)

    result = generate_video_analysis(
        video_path=str(source),
        product_title="Portable Blender Pro",
        product_url="https://shop.example/products/blender",
        main_image_url="https://cdn.example/blender.png",
        invoke_generate_fn=fake_invoke,
    )

    assert result.startswith("video_text")
    assert captured["prepare"]["policy"] == "review_480p_audio"
    assert captured["prepare"]["output_dir"] == str(tmp_path)
    assert captured["use_case_code"] == "video_cover.video_analysis"
    assert captured["media"] == str(optimized)
    assert captured["cleanup_path"] == str(optimized)


def test_build_platform_prompt_uses_creative_director_inputs():
    from appcore.video_cover_generation import SOCIAL_REELS_SPEC, build_platform_prompt

    prompt = build_platform_prompt(
        SOCIAL_REELS_SPEC,
        product_title="Portable Blender Pro",
        product_url="https://shop.example/products/blender",
        product_analysis="<产品核心理解>\nPortable Blender Pro",
        video_analysis="精选视频帧：hand using the blender in a kitchen",
        ad_copy_sets="- Blend Anywhere\n- Daily Smoothies Made Easy",
    )

    assert "请基于上传的产品图片、精选视频帧、<产品核心理解>" in prompt
    assert "hand using the blender in a kitchen" in prompt
    assert "Blend Anywhere" in prompt
    assert "不要做成电商商品主图、海报、影棚产品照，也不要做成截图" in prompt
    assert "画面中必须且只能包含一句简短英文 hook" in prompt
    assert "{product_analysis}" not in prompt
    assert "{video_analysis}" not in prompt
    assert "{ad_copy_sets}" not in prompt


def test_generate_video_covers_requires_product_title_and_main_image(tmp_path):
    from appcore.video_cover_generation import VideoCoverGenerationError, generate_video_covers

    video_path = tmp_path / "input.mp4"
    video_path.write_bytes(b"fake video")

    class MissingImage:
        title = "Missing Image Product"
        main_image_url = ""

    with pytest.raises(VideoCoverGenerationError, match="商品主图"):
        generate_video_covers(
            product_url="https://shop.example/products/missing",
            video_path=str(video_path),
            video_filename="demo.mp4",
            product_fetch_fn=lambda url: MissingImage(),
        )


def test_video_cover_page_rejects_non_admin(authed_user_client_no_db):
    user_resp = authed_user_client_no_db.get("/video-cover")
    assert user_resp.status_code == 403


def test_video_cover_page_requires_login(authed_client_no_db):
    client = authed_client_no_db.application.test_client()

    resp = client.get("/video-cover")

    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_video_cover_page_renders_project_list_for_admin(authed_client_no_db, monkeypatch):
    from web.routes import video_cover

    calls = []

    def fake_list_projects(*, user_id, is_admin):
        calls.append({"user_id": user_id, "is_admin": is_admin})
        return [
            {
                "id": "task-1",
                "display_name": "Lamp Cover",
                "original_filename": "lamp.mp4",
                "status": "uploaded",
                "created_at": None,
                "creator_name": "alice",
            }
        ]

    monkeypatch.setattr(video_cover.video_cover_project_store, "list_projects", fake_list_projects)

    admin_resp = authed_client_no_db.get("/video-cover")
    assert admin_resp.status_code == 200
    html = admin_resp.get_data(as_text=True)
    assert "文案封面生成" in html
    assert "新建项目" in html
    assert "Lamp Cover" in html
    assert "alice" in html
    assert "商品链接" in html
    assert "videoCoverDropzone" in html
    assert 'id="videoCoverFile"' in html
    assert 'id="videoCoverPreview"' in html
    assert 'id="previewClear"' in html
    assert "拖入视频" in html
    assert calls == [{"user_id": 1, "is_admin": True}]


def test_video_cover_project_create_persists_initial_workflow(authed_client_no_db, monkeypatch, tmp_path):
    from web.routes import video_cover

    inserted = {}

    def fake_insert_project(**kwargs):
        inserted.update(kwargs)

    monkeypatch.setattr(video_cover, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(video_cover, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(video_cover, "get_retention_hours", lambda project_type: 168)
    monkeypatch.setattr(video_cover.video_cover_project_store, "insert_project", fake_insert_project)
    monkeypatch.setattr(
        video_cover,
        "extract_thumbnail",
        lambda video_path, output_dir, scale=None: str(Path(output_dir) / "thumb.jpg"),
    )

    resp = authed_client_no_db.post(
        "/video-cover/api/projects",
        data={
            "product_url": "https://shop.example/products/lamp",
            "video_file": (BytesIO(b"video"), "lamp.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 201
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["id"] == inserted["task_id"]
    assert inserted["user_id"] == 1
    assert inserted["original_filename"] == "lamp.mp4"
    state = inserted["state"]
    assert state["type"] == "video_cover"
    assert state["product_url"] == "https://shop.example/products/lamp"
    assert Path(state["video_path"]).is_file()
    assert state["steps"] == {
        "video_analysis": "pending",
        "product_analysis": "pending",
        "ad_copy": "pending",
        "cover_generation": "pending",
    }


def test_video_cover_detail_shows_final_result_download_at_top(authed_client_no_db, monkeypatch):
    from web.routes import video_cover

    state = {
        "product_url": "https://shop.example/products/lamp",
        "display_name": "Lamp",
        "steps": {
            "video_analysis": "done",
            "product_analysis": "done",
            "ad_copy": "done",
            "cover_generation": "done",
        },
        "result": {
            "covers": [
                {
                    "platform": "social_reels",
                    "label": "Facebook / Instagram / TikTok / Shorts",
                    "object_key": "artifacts/video_cover/1/task-1/social_reels.png",
                    "width": 1080,
                    "height": 1920,
                }
            ]
        },
    }
    row = {"id": "task-1", "state_json": json.dumps(state, ensure_ascii=False), "display_name": "Lamp"}
    monkeypatch.setattr(
        video_cover.video_cover_project_store,
        "get_project",
        lambda task_id, *, user_id, is_admin: row,
    )
    monkeypatch.setattr(video_cover, "recover_project_if_needed", lambda task_id, project_type: state)

    resp = authed_client_no_db.get("/video-cover/task-1")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "最终封面" in html
    assert "下载最终封面" in html
    assert "/video-cover/api/task-1/download/social_reels" in html


def test_video_cover_step_requires_previous_steps_done(authed_client_no_db, monkeypatch, tmp_path):
    from web.routes import video_cover

    video_path = tmp_path / "lamp.mp4"
    video_path.write_bytes(b"video")
    state = {
        "id": "task-1",
        "type": "video_cover",
        "product_url": "https://shop.example/products/lamp",
        "video_path": str(video_path),
        "video_filename": "lamp.mp4",
        "task_dir": str(tmp_path),
        "steps": {
            "video_analysis": "pending",
            "product_analysis": "pending",
            "ad_copy": "pending",
            "cover_generation": "pending",
        },
        "step_messages": {},
    }
    row = {"id": "task-1", "state_json": json.dumps(state, ensure_ascii=False), "display_name": "Lamp"}
    monkeypatch.setattr(
        video_cover.video_cover_project_store,
        "get_project",
        lambda task_id, *, user_id, is_admin: row,
    )

    resp = authed_client_no_db.post("/video-cover/api/task-1/run/ad_copy", data={})

    assert resp.status_code == 400
    assert "请先完成视频分析" in resp.get_json()["error"]


def test_video_cover_step_run_updates_project_state(authed_client_no_db, monkeypatch, tmp_path):
    from web.routes import video_cover

    video_path = tmp_path / "lamp.mp4"
    video_path.write_bytes(b"video")
    saved = {}
    captured = {}
    product = _FakeProduct()
    state = {
        "id": "task-1",
        "type": "video_cover",
        "product_url": "https://shop.example/products/lamp",
        "video_path": str(video_path),
        "video_filename": "lamp.mp4",
        "task_dir": str(tmp_path),
        "steps": {
            "video_analysis": "pending",
            "product_analysis": "pending",
            "ad_copy": "pending",
            "cover_generation": "pending",
        },
        "step_messages": {},
    }
    row = {
        "id": "task-1",
        "user_id": 8,
        "state_json": json.dumps(state, ensure_ascii=False),
        "display_name": "Lamp",
    }
    monkeypatch.setattr(
        video_cover.video_cover_project_store,
        "get_project",
        lambda task_id, *, user_id, is_admin: row,
    )
    monkeypatch.setattr(video_cover, "_extract_product", lambda product_url: (product, product.title, product.main_image_url))
    monkeypatch.setattr(video_cover, "probe_media_info", lambda video_path_arg: {"duration": 8.0, "resolution": "720x1280"})
    def fake_generate_video_analysis(**kwargs):
        captured.update(kwargs)
        return "video_text: demo"

    monkeypatch.setattr(video_cover, "generate_video_analysis", fake_generate_video_analysis)

    def fake_save(task_id, next_state, status=None, execute_func=None, **kwargs):
        saved["task_id"] = task_id
        saved["state"] = next_state
        saved["status"] = status

    monkeypatch.setattr(video_cover, "save_project_state", fake_save)

    resp = authed_client_no_db.post(
        "/video-cover/api/task-1/run/video_analysis",
        data={"provider": "gemini_vertex_adc", "model": "gemini_31_pro"},
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["data"]["video_analysis"] == "video_text: demo"
    assert saved["task_id"] == "task-1"
    assert saved["status"] == "running"
    assert saved["state"]["steps"]["video_analysis"] == "done"
    assert saved["state"]["video_analysis"] == "video_text: demo"
    assert saved["state"]["models"]["video_analysis"]["provider"] == "gemini_vertex_adc"
    assert captured["user_id"] == 8


def test_video_cover_download_serves_owned_cover(authed_client_no_db, monkeypatch, tmp_path):
    from web.routes import video_cover

    cover_path = tmp_path / "social_reels.png"
    cover_path.write_bytes(b"png-data")
    state = {
        "result": {
            "covers": [
                {
                    "platform": "social_reels",
                    "object_key": "artifacts/video_cover/1/task-1/social_reels.png",
                }
            ]
        }
    }
    row = {"id": "task-1", "state_json": json.dumps(state, ensure_ascii=False), "display_name": "Lamp"}
    monkeypatch.setattr(
        video_cover.video_cover_project_store,
        "get_project",
        lambda task_id, *, user_id, is_admin: row,
    )
    monkeypatch.setattr(video_cover.local_media_storage, "safe_local_path_for", lambda object_key: cover_path)

    resp = authed_client_no_db.get("/video-cover/api/task-1/download/social_reels")

    assert resp.status_code == 200
    assert resp.data == b"png-data"
    assert resp.headers["Content-Disposition"].startswith("attachment;")


def test_video_cover_generate_route_calls_service(authed_client_no_db, monkeypatch):
    from web.routes import video_cover

    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        captured["video_path_existed_during_call"] = Path(kwargs["video_path"]).is_file()
        return {
            "task_id": "task-123",
            "product": {"title": "Lamp", "main_image_url": "https://cdn.example/lamp.png"},
            "reference": {"url": "/video-cover/artifact/ref.png", "object_key": "ref.png"},
            "model": {"channel": "local", "model_id": "gpt-image-2"},
            "covers": [
                {
                    "platform": "meta",
                    "label": "Meta",
                    "url": "/video-cover/artifact/meta.png",
                    "object_key": "meta.png",
                    "width": 1080,
                    "height": 1920,
                }
            ],
        }

    monkeypatch.setattr(video_cover, "generate_video_covers", fake_generate)

    resp = authed_client_no_db.post(
        "/video-cover/api/generate",
        data={
            "product_url": "https://shop.example/products/lamp",
            "video_file": (BytesIO(b"video"), "lamp.mp4"),
            "cover_provider": "openrouter",
            "cover_model": "nano_banana_2",
            "product_provider": "gemini_vertex_adc",
            "video_provider": "gemini_vertex_adc",
            "ad_copy_provider": "openrouter",
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["data"]["product"]["title"] == "Lamp"
    assert captured["product_url"] == "https://shop.example/products/lamp"
    assert captured["video_filename"] == "lamp.mp4"
    assert captured["user_id"] == 1
    assert captured["video_path_existed_during_call"] is True
    assert captured["cover_provider"] == "openrouter"
    assert captured["cover_model"] == "nano_banana_2"
    assert captured["product_analysis_provider"] == "gemini_vertex_adc"
    assert captured["video_analysis_provider"] == "gemini_vertex_adc"
    assert captured["ad_copy_provider"] == "openrouter"


def test_video_cover_generate_route_maps_ad_copy_failure_to_502(authed_client_no_db, monkeypatch):
    from appcore.video_cover_generation import VideoCoverGenerationError
    from web.routes import video_cover

    def fake_generate(**kwargs):
        raise VideoCoverGenerationError("文案创作失败：模型未返回合法 JSON")

    monkeypatch.setattr(video_cover, "generate_video_covers", fake_generate)

    resp = authed_client_no_db.post(
        "/video-cover/api/generate",
        data={
            "product_url": "https://shop.example/products/lamp",
            "video_file": (BytesIO(b"video"), "lamp.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 502
    assert resp.get_json()["error"] == "文案创作失败：模型未返回合法 JSON"


def test_layout_contains_video_cover_menu_entry():
    html = (ROOT / "web" / "templates" / "layout.html").read_text(encoding="utf-8")

    assert "/video-cover" in html
    assert "文案封面生成" in html
