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


def test_normalize_product_image_jpg_outputs_400_square_jpeg():
    from appcore.video_cover_generation import normalize_product_image_jpg

    payload = normalize_product_image_jpg(_png_bytes(size=(900, 240), color=(20, 120, 180)))

    assert payload.startswith(b"\xff\xd8")
    with Image.open(BytesIO(payload)) as img:
        assert img.format == "JPEG"
        assert img.size == (400, 400)


def _make_superadmin_client_no_db(monkeypatch):
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.db.query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.db.query_one", lambda *args, **kwargs: None)
    monkeypatch.setattr("appcore.scheduled_tasks.query", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "appcore.medias.list_enabled_language_codes",
        lambda: ["de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en"],
    )
    from web.app import create_app

    fake_user = {
        "id": 1,
        "username": "admin",
        "role": "superadmin",
        "is_active": 1,
    }
    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 1 else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    return client


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
            media = kwargs["media"]
            if isinstance(media, list):
                assert all(Path(item).is_file() for item in media)
            else:
                assert Path(media).is_file()
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
                                "title": "Blend Anywhere",
                                "message": "Make smoothies without dragging out the big blender.",
                                "description": "Fresh Drinks Made Simple",
                            },
                            "chinese_translation": {
                                "title": "随处搅拌",
                                "message": "不用搬出大型搅拌机也能做奶昔。",
                                "description": "轻松制作新鲜饮品",
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
    assert "不要在图片中生成任何文字" in calls[0]["prompt"]
    assert "必须且只能添加一句简短英文 hook" not in calls[0]["prompt"]
    assert '"title": "Blend Anywhere"' in calls[0]["prompt"]
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


def test_generate_video_covers_respects_image_count_and_copy_metadata(tmp_path):
    from appcore import local_media_storage
    from appcore.video_cover_generation import generate_video_covers

    video_path = tmp_path / "input.mp4"
    video_path.write_bytes(b"fake video")
    calls = []
    progress = []
    copy_payload = {
        "ad_copy_sets": [
            {
                "id": idx,
                "angle": f"角度 {idx}",
                "english": {
                    "title": f"Hook {idx}",
                    "message": f"Body copy {idx}",
                    "description": f"Description {idx}",
                },
                "chinese_translation": {
                    "title": f"钩子 {idx}",
                    "message": f"正文 {idx}",
                    "description": f"描述 {idx}",
                },
                "usage_note": f"画面建议 {idx}",
            }
            for idx in range(1, 6)
        ]
    }

    def fake_thumbnail(video_path_arg: str, output_dir: str, scale=None):
        return str(_jpg_file(Path(output_dir) / "thumbnail.jpg"))

    def fake_generate_image(prompt: str, *, source_image: bytes, source_mime: str, **kwargs):
        calls.append(prompt)
        return _png_bytes(size=(900, 900), color=(30, 160, 210)), "image/png"

    result = generate_video_covers(
        product_url="https://shop.example/products/blender",
        video_path=str(video_path),
        video_filename="demo.mp4",
        user_id=7,
        task_id="cover-task-count",
        product_fetch_fn=lambda url: _FakeProduct(),
        image_fetch_fn=lambda url: _png_bytes(size=(900, 900), color=(15, 90, 140)),
        thumbnail_extractor=fake_thumbnail,
        image_generate_fn=fake_generate_image,
        product_analysis_text="<产品分析报告>demo</产品分析报告>",
        video_analysis_text="<视频素材分析>demo</视频素材分析>",
        ad_copy_payload=copy_payload,
        image_count=3,
        on_cover_done=lambda partial: progress.append([cover["index"] for cover in partial["covers"]]),
    )

    assert len(calls) == 3
    assert progress == [[1], [1, 2], [1, 2, 3]]
    assert [cover["index"] for cover in result["covers"]] == [1, 2, 3]
    assert [cover["source_ad_copy_id"] for cover in result["covers"]] == [1, 2, 3]
    assert [cover["hook"] for cover in result["covers"]] == ["Hook 1", "Hook 2", "Hook 3"]
    assert [cover["copy"]["english"]["message"] for cover in result["covers"]] == [
        "Body copy 1",
        "Body copy 2",
        "Body copy 3",
    ]
    assert [cover["copy"]["english"]["description"] for cover in result["covers"]] == [
        "Description 1",
        "Description 2",
        "Description 3",
    ]
    assert all(cover["overlay_text"].startswith("Hook ") for cover in result["covers"])
    assert all(cover["overlay_box"]["width"] <= 1080 for cover in result["covers"])
    assert all(cover["formatted_copy"].startswith("标题: Hook ") for cover in result["covers"])
    assert "不要在图片中生成任何文字" in calls[0]
    assert all(local_media_storage.exists(cover["object_key"]) for cover in result["covers"])


def test_generate_video_covers_normalizes_legacy_copy_metadata(tmp_path):
    from appcore.video_cover_generation import generate_video_covers

    video_path = tmp_path / "input.mp4"
    video_path.write_bytes(b"fake video")
    copy_payload = {
        "ad_copy_sets": [
            {
                "id": 1,
                "angle": "旧结构",
                "english": {
                    "headline": "Legacy Hook",
                    "body_text": "Legacy body copy.",
                    "cta": "Legacy Description",
                },
                "chinese_translation": {
                    "headline": "旧钩子",
                    "body_text": "旧正文",
                    "cta": "旧描述",
                },
                "usage_note": "兼容旧任务。",
            }
        ]
    }

    def fake_thumbnail(video_path_arg: str, output_dir: str, scale=None):
        return str(_jpg_file(Path(output_dir) / "thumbnail.jpg"))

    def fake_generate_image(prompt: str, *, source_image: bytes, source_mime: str, **kwargs):
        return _png_bytes(size=(900, 900), color=(30, 160, 210)), "image/png"

    result = generate_video_covers(
        product_url="https://shop.example/products/blender",
        video_path=str(video_path),
        video_filename="demo.mp4",
        user_id=7,
        task_id="cover-task-legacy",
        product_fetch_fn=lambda url: _FakeProduct(),
        image_fetch_fn=lambda url: _png_bytes(size=(900, 900), color=(15, 90, 140)),
        thumbnail_extractor=fake_thumbnail,
        image_generate_fn=fake_generate_image,
        product_analysis_text="<产品分析报告>demo</产品分析报告>",
        video_analysis_text="<视频素材分析>demo</视频素材分析>",
        ad_copy_payload=copy_payload,
        image_count=1,
    )

    cover = result["covers"][0]
    assert cover["copy"]["english"] == {
        "title": "Legacy Hook",
        "message": "Legacy body copy.",
        "description": "Legacy Description",
    }
    assert cover["formatted_copy"] == (
        "标题: Legacy Hook\n"
        "文案: Legacy body copy.\n"
        "描述: Legacy Description"
    )
    assert cover["overlay_text"] == "Legacy Hook"


def test_resolve_video_cover_model_options_matches_requested_mappings():
    from appcore.video_cover_generation import (
        resolve_cover_model_selection,
        resolve_text_model_selection,
        video_cover_model_options,
    )

    assert resolve_text_model_selection("video_analysis", "gemini_vertex_adc", "").model == "gemini-3.1-pro-preview"
    assert resolve_text_model_selection("video_analysis", "openrouter", "").model == "google/gemini-3.1-pro-preview"
    assert resolve_text_model_selection("video_analysis", "gemini_vertex_adc", "gemini_3_flash").model == "gemini-3-flash-preview"
    assert resolve_text_model_selection("product_analysis", "gemini_vertex_adc", "").model == "gemini-3-flash-preview"
    assert resolve_text_model_selection("ad_copy", "openrouter", "").model == "google/gemini-3-flash-preview"
    assert resolve_text_model_selection("ad_copy", "openrouter", "claude_sonnet").model == "anthropic/claude-sonnet-4.6"
    assert resolve_text_model_selection("ad_copy", "openrouter", "openai/gpt-5.5").alias == "gpt_5_5"

    local = resolve_cover_model_selection("local", "gpt_image_2")
    assert local.provider == "local"
    assert local.model == "gpt-image-2"
    openrouter = resolve_cover_model_selection("openrouter", "nano_banana_pro")
    assert openrouter.provider == "openrouter"
    assert openrouter.model == "gemini-3-pro-image-preview"
    openrouter_image2 = resolve_cover_model_selection("openrouter", "openai_image_2_high")
    assert openrouter_image2.provider == "openrouter"
    assert openrouter_image2.model == "openai/gpt-5.4-image-2:high"

    options = video_cover_model_options()
    assert options["steps"]["video_analysis"]["default_provider"] == "gemini_vertex_adc"
    assert "gemini_3_flash" in options["steps"]["video_analysis"]["providers"]["gemini_vertex_adc"]["models"]
    assert "claude_sonnet" in options["steps"]["ad_copy"]["providers"]["openrouter"]["models"]
    assert options["steps"]["ad_copy"]["providers"]["openrouter"]["models"]["gpt_5_5"]["model"] == "openai/gpt-5.5"
    assert "local" in options["steps"]["cover_generation"]["providers"]
    assert options["steps"]["cover_generation"]["models"]["local"]["gpt_image_2"] == "gpt-image-2"
    assert options["steps"]["cover_generation"]["models"]["openrouter"]["openai_image_2_mid"] == "openai/gpt-5.4-image-2:mid"
    assert options["steps"]["cover_generation"]["models"]["openrouter"]["nano_banana_2"] == "gemini-3.1-flash-image-preview"


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
                                "title": "Easy Daily Fix",
                                "message": "A simple upgrade for busy mornings.",
                                "description": "Upgrade Your Routine",
                            },
                            "chinese_translation": {
                                "title": "轻松日常改进",
                                "message": "适合忙碌早晨的小升级。",
                                "description": "升级你的日常",
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

    assert result["ad_copy_sets"][0]["english"]["title"] == "Easy Daily Fix"
    assert result["ad_copy_sets"][0]["english"]["message"] == "A simple upgrade for busy mornings."
    assert result["ad_copy_sets"][0]["english"]["description"] == "Upgrade Your Routine"
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
    assert "title、message、description" in prompt
    assert "headline" not in prompt


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
        assert "response_format" not in kwargs
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


def test_generate_product_analysis_does_not_send_chat_response_format(tmp_path):
    from appcore.video_cover_generation import generate_product_analysis

    product_image = tmp_path / "product.jpg"
    product_image.write_bytes(b"jpg")
    captured = {}

    def fake_invoke(use_case_code: str, **kwargs):
        captured["use_case_code"] = use_case_code
        captured.update(kwargs)
        assert "response_format" not in kwargs
        return {"text": '{"product_definition":"demo"}'}

    result = generate_product_analysis(
        product=_FakeProduct(),
        product_title="Portable Blender Pro",
        main_image_url="https://cdn.example/blender.png",
        product_image_path=product_image,
        invoke_generate_fn=fake_invoke,
    )

    assert "product_definition" in result
    assert captured["use_case_code"] == "video_cover.product_analysis"
    assert captured["media"] == str(product_image)


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

    assert "生成一张 9:16 竖版无文字封面背景图" in prompt
    assert "product_analysis: <产品核心理解>" in prompt
    assert "hand using the blender in a kitchen" in prompt
    assert "Blend Anywhere" in prompt
    assert "不要做成电商商品主图、海报、影棚产品照，也不要做成截图" in prompt
    assert "不要在图片中生成任何文字" in prompt
    assert "画面中必须且只能包含一句简短英文 hook" not in prompt
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
    assert 'data-image-count="1"' in html
    assert 'data-image-count="2"' in html
    assert 'data-image-count="3"' in html
    assert 'data-image-count="4"' in html
    assert 'name="image_count"' in html
    assert 'value="4"' in html
    assert '<button class="vc-count-pill active" type="button" data-image-count="4">4 张</button>' in html
    assert "默认配置" not in html
    assert calls == [{"user_id": 1, "is_admin": True}]


def test_video_cover_page_renders_default_config_for_superadmin(monkeypatch):
    from web.routes import video_cover

    monkeypatch.setattr(
        video_cover.video_cover_project_store,
        "list_projects",
        lambda *, user_id, is_admin: [],
    )
    monkeypatch.setattr(
        video_cover.video_cover_settings,
        "get_model_defaults",
        lambda: {
            "video_analysis": {"provider": "gemini_vertex_adc", "model_id": "gemini-3.1-pro-preview"},
            "product_analysis": {"provider": "openrouter", "model_id": "google/gemini-3-flash-preview"},
            "ad_copy": {"provider": "openrouter", "model_id": "google/gemini-3-flash-preview"},
            "cover_generation": {"provider": "local", "model_id": "gpt-image-2"},
        },
    )
    client = _make_superadmin_client_no_db(monkeypatch)

    resp = client.get("/video-cover")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "默认配置" in html
    assert 'id="vcShowDefaultConfig"' in html
    for step in ("video_analysis", "product_analysis", "ad_copy", "cover_generation"):
        assert f'name="{step}_provider"' in html
        assert f'<select class="vc-input" name="{step}_model_id"' in html
    assert 'id="vcModelOptions"' in html
    assert "Nano Banana 2" in html
    assert "openai/gpt-5.4-image-2:mid" in html


def test_video_cover_default_config_requires_superadmin(authed_client_no_db):
    get_resp = authed_client_no_db.get("/video-cover/api/default-config")
    post_resp = authed_client_no_db.post(
        "/video-cover/api/default-config",
        json={"steps": {"video_analysis": {"provider": "openrouter", "model_id": "custom"}}},
    )

    assert get_resp.status_code == 403
    assert post_resp.status_code == 403


def test_video_cover_default_config_api_saves_global_defaults(monkeypatch):
    from web.routes import video_cover

    saved = {}
    defaults = {
        "video_analysis": {"provider": "gemini_vertex_adc", "model_id": "gemini-3.1-pro-preview"},
        "product_analysis": {"provider": "openrouter", "model_id": "google/gemini-3-flash-preview"},
        "ad_copy": {"provider": "openrouter", "model_id": "google/gemini-3-flash-preview"},
        "cover_generation": {"provider": "local", "model_id": "gpt-image-2"},
    }

    monkeypatch.setattr(video_cover.video_cover_settings, "get_model_defaults", lambda: defaults)
    monkeypatch.setattr(
        video_cover.video_cover_settings,
        "save_model_defaults",
        lambda payload: saved.setdefault("payload", payload) or {
            "video_analysis": {"provider": "openrouter", "model_id": "google/gemini-3.1-pro-preview"},
            "product_analysis": {"provider": "gemini_vertex_adc", "model_id": "gemini-3-flash-preview"},
            "ad_copy": {"provider": "openrouter", "model_id": "google/gemini-3-flash-preview"},
            "cover_generation": {"provider": "openrouter", "model_id": "openai/gpt-5.4-image-2:mid"},
        },
    )
    client = _make_superadmin_client_no_db(monkeypatch)

    get_resp = client.get("/video-cover/api/default-config")
    post_resp = client.post(
        "/video-cover/api/default-config",
        json={
            "steps": {
                "video_analysis": {"provider": "openrouter", "model_id": "google/gemini-3.1-pro-preview"},
                "product_analysis": {"provider": "gemini_vertex_adc", "model_id": "gemini-3-flash-preview"},
                "ad_copy": {"provider": "openrouter", "model_id": "google/gemini-3-flash-preview"},
                "cover_generation": {"provider": "openrouter", "model_id": "openai/gpt-5.4-image-2:mid"},
            }
        },
    )

    assert get_resp.status_code == 200
    assert get_resp.get_json()["data"]["steps"] == defaults
    assert post_resp.status_code == 200
    assert saved["payload"]["video_analysis"]["provider"] == "openrouter"
    assert saved["payload"]["cover_generation"]["model_id"] == "openai/gpt-5.4-image-2:mid"
    assert post_resp.get_json()["data"]["steps"]["cover_generation"]["provider"] == "openrouter"


def test_video_cover_project_create_persists_initial_workflow(authed_client_no_db, monkeypatch, tmp_path):
    from web.routes import video_cover

    inserted = {}
    started = []
    model_defaults = {
        "video_analysis": {"provider": "openrouter", "model_id": "google/gemini-3.1-pro-preview"},
        "product_analysis": {"provider": "gemini_vertex_adc", "model_id": "gemini-3-flash-preview"},
        "ad_copy": {"provider": "openrouter", "model_id": "google/gemini-3-flash-preview"},
        "cover_generation": {"provider": "openrouter", "model_id": "openai/gpt-5.4-image-2:mid"},
    }

    def fake_insert_project(**kwargs):
        inserted.update(kwargs)

    monkeypatch.setattr(video_cover, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(video_cover, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(video_cover, "get_retention_hours", lambda project_type: 168)
    monkeypatch.setattr(video_cover.video_cover_project_store, "insert_project", fake_insert_project)
    monkeypatch.setattr(video_cover.video_cover_settings, "get_model_defaults", lambda: model_defaults)
    monkeypatch.setattr(
        video_cover,
        "_extract_product",
        lambda product_url: (_FakeProduct(), _FakeProduct.title, _FakeProduct.main_image_url),
    )
    monkeypatch.setattr(video_cover, "_fetch_product_image", lambda image_url: _png_bytes(size=(900, 240)))
    monkeypatch.setattr(
        video_cover,
        "_start_video_cover_background",
        lambda task_id, start_step="video_analysis", image_count=None: started.append((task_id, start_step, image_count)) or True,
        raising=False,
    )
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
            "image_count": "3",
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
    assert state["image_count"] == 3
    assert state["model_defaults"] == model_defaults
    assert state["product"]["title"] == "Portable Blender Pro"
    assert state["product"]["main_image_url"] == "https://cdn.example/blender.png"
    assert Path(state["product"]["product_image_path"]).is_file()
    with Image.open(state["product"]["product_image_path"]) as img:
        assert img.format == "JPEG"
        assert img.size == (400, 400)
    assert Path(state["video_path"]).is_file()
    assert state["steps"] == {
        "video_analysis": "pending",
        "product_analysis": "pending",
        "ad_copy": "pending",
        "cover_generation": "pending",
    }
    assert started == [(payload["id"], "video_analysis", 3)]


def test_video_cover_project_create_defaults_to_four_covers(authed_client_no_db, monkeypatch, tmp_path):
    from web.routes import video_cover

    inserted = {}
    started = []

    def fake_insert_project(**kwargs):
        inserted.update(kwargs)

    monkeypatch.setattr(video_cover, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(video_cover, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(video_cover, "get_retention_hours", lambda project_type: 168)
    monkeypatch.setattr(video_cover.video_cover_project_store, "insert_project", fake_insert_project)
    monkeypatch.setattr(video_cover.video_cover_settings, "get_model_defaults", lambda: {})
    monkeypatch.setattr(
        video_cover,
        "_extract_product",
        lambda product_url: (_FakeProduct(), _FakeProduct.title, _FakeProduct.main_image_url),
    )
    monkeypatch.setattr(video_cover, "_fetch_product_image", lambda image_url: _png_bytes(size=(900, 240)))
    monkeypatch.setattr(
        video_cover,
        "_start_video_cover_background",
        lambda task_id, start_step="video_analysis", image_count=None: started.append((task_id, start_step, image_count)) or True,
        raising=False,
    )
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
    assert inserted["state"]["image_count"] == 4
    assert started == [(payload["id"], "video_analysis", 4)]


def test_video_cover_background_chain_uses_project_model_default_snapshot(monkeypatch, tmp_path):
    from web.routes import video_cover

    calls = []
    state = {
        "id": "task-1",
        "type": "video_cover",
        "product_url": "https://shop.example/products/lamp",
        "video_path": str(tmp_path / "lamp.mp4"),
        "video_filename": "lamp.mp4",
        "steps": {
            "video_analysis": "pending",
            "product_analysis": "pending",
            "ad_copy": "pending",
            "cover_generation": "pending",
        },
        "step_messages": {},
        "model_defaults": {
            "video_analysis": {"provider": "openrouter", "model_id": "google/gemini-3.1-pro-preview"},
            "product_analysis": {"provider": "gemini_vertex_adc", "model_id": "gemini-3-flash-preview"},
            "ad_copy": {"provider": "openrouter", "model_id": "google/gemini-3-flash-preview"},
            "cover_generation": {"provider": "openrouter", "model_id": "openai/gpt-5.4-image-2:mid"},
        },
    }
    row = {"id": "task-1", "user_id": 8, "state_json": json.dumps(state, ensure_ascii=False)}
    monkeypatch.setattr(video_cover, "_load_project_for_background", lambda task_id: (row, state))
    monkeypatch.setattr(video_cover, "_save_state", lambda task_id, next_state, status: None)

    def fake_run_step(next_state, step, *, provider, model, user_id):
        calls.append({"step": step, "provider": provider, "model": model, "user_id": user_id})
        return {}

    monkeypatch.setattr(video_cover, "_run_project_step", fake_run_step)

    video_cover._run_video_cover_chain("task-1")

    assert calls == [
        {"step": "video_analysis", "provider": "openrouter", "model": "google/gemini-3.1-pro-preview", "user_id": 8},
        {"step": "product_analysis", "provider": "gemini_vertex_adc", "model": "gemini-3-flash-preview", "user_id": 8},
        {"step": "ad_copy", "provider": "openrouter", "model": "google/gemini-3-flash-preview", "user_id": 8},
        {"step": "cover_generation", "provider": "openrouter", "model": "openai/gpt-5.4-image-2:mid", "user_id": 8},
    ]


def test_video_cover_detail_renders_progress_restart_and_four_process_cards(authed_client_no_db, monkeypatch):
    from web.routes import video_cover

    state = {
        "product_url": "https://shop.example/products/lamp",
        "display_name": "Lamp",
        "image_count": 2,
        "steps": {
            "video_analysis": "done",
            "product_analysis": "done",
            "ad_copy": "done",
            "cover_generation": "done",
        },
        "step_timing": {
            "video_analysis": {"elapsed_seconds": 12},
            "product_analysis": {"elapsed_seconds": 8},
            "ad_copy": {"elapsed_seconds": 4},
            "cover_generation": {"elapsed_seconds": 31},
        },
        "ad_copy_sets": {
            "ad_copy_sets": [
                {
                    "id": 1,
                    "angle": "痛点解决型",
                    "english": {
                        "headline": "Hook 1",
                        "body_text": "Body copy 1",
                        "cta": "Shop Now",
                    },
                    "chinese_translation": {
                        "headline": "钩子 1",
                        "body_text": "正文 1",
                        "cta": "立即购买",
                    },
                    "usage_note": "适合封面 1。",
                }
            ]
        },
        "result": {
            "covers": [
                {
                    "platform": "social_reels_1",
                    "label": "Facebook / Instagram / TikTok / Shorts",
                    "index": 1,
                    "object_key": "artifacts/video_cover/1/task-1/social_reels_1.png",
                    "width": 1080,
                    "height": 1920,
                    "source_ad_copy_id": 1,
                    "hook": "Hook 1",
                    "formatted_copy": "标题: Hook 1\n文案: Body copy 1\n描述: Shop Now",
                    "copy": {
                        "english": {
                            "headline": "Hook 1",
                            "body_text": "Body copy 1",
                            "cta": "Shop Now",
                        },
                        "chinese_translation": {
                            "headline": "钩子 1",
                            "body_text": "正文 1",
                            "cta": "立即购买",
                        },
                    },
                },
                {
                    "platform": "social_reels_2",
                    "label": "Facebook / Instagram / TikTok / Shorts #2",
                    "index": 2,
                    "object_key": "artifacts/video_cover/1/task-1/social_reels_2.png",
                    "width": 1080,
                    "height": 1920,
                    "source_ad_copy_id": 1,
                    "hook": "Hook 2",
                    "formatted_copy": "标题: Hook 2\n文案: Body copy 2\n描述: Save Time",
                    "copy": {
                        "english": {
                            "title": "Hook 2",
                            "message": "Body copy 2",
                            "description": "Save Time",
                        },
                        "chinese_translation": {
                            "title": "钩子 2",
                            "message": "正文 2",
                            "description": "节省时间",
                        },
                    },
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
    resp = authed_client_no_db.get("/video-cover/task-1")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "强制重新开始" in html
    assert "vcd-restart-btn" in html
    assert "window.confirm" in html
    assert "selectedRestartCount()" in html
    assert "image_count: selectedRestartCount()" in html
    assert "全部报文预览" in html
    assert 'id="vcdAllPayloadModal"' in html
    assert 'id="vcdAllPayloadBody"' in html
    assert 'data-all-payload-preview' in html
    assert "normalizeCopyTextFields" in html
    assert "formattedCopyText" in html
    assert "`标题: ${en.title}`" in html
    assert "`文案: ${en.message}`" in html
    assert "`描述: ${en.description}`" in html
    assert ".vcd-input-panel { position:sticky;" in html
    assert "overflow-y:auto" in html
    assert "overscroll-behavior:contain" in html
    assert '<aside class="vcd-panel vcd-input-panel">' in html
    assert 'data-copy-ad-copy="${idx}"' in html
    assert "copyAdCopyText(btn.dataset.copyAdCopy)" in html
    assert "formattedCopyText(sets[index])" in html
    assert html.count('<section class="vcd-process-card') == 4
    for step in ("video_analysis", "product_analysis", "ad_copy", "cover_generation"):
        assert f'data-process-card="{step}"' in html
        assert f'data-prompt-step="{step}"' in html
        assert f'data-visual-step="{step}"' in html
        assert f'data-retry-step="{step}"' in html
        assert f'data-step-timer="{step}"' in html
    assert "结果展示" not in html
    assert "vcd-result-box" not in html
    assert "data-result-step" not in html
    assert "保存图片" in html
    assert "复制图片" in html
    assert "复制文案" in html
    assert "一键复制文案" not in html
    assert "vcd-cover-results-grid" in html
    assert "vcd-cover-result-card" in html
    assert "vcd-cover-copy-panel" in html
    assert "vcd-cover-copy-button" in html
    assert "covers.map((cover, idx)" in html
    assert 'data-copy-cover-text="${idx}"' in html
    assert "copyTextForCover(covers[index])" in html
    assert "selectedCoverIndex" not in html
    assert "data-cover-index" not in html
    assert "vcd-thumbs" not in html
    assert "/video-cover/api/task-1/download/social_reels_1" in html
    assert "/video-cover/api/task-1/download/social_reels_2" in html


def test_video_cover_detail_matches_multi_translate_step_status_style(authed_client_no_db, monkeypatch):
    from web.routes import video_cover

    state = {
        "product_url": "https://shop.example/products/lamp",
        "display_name": "Lamp",
        "image_count": 2,
        "steps": {
            "video_analysis": "running",
            "product_analysis": "done",
            "ad_copy": "error",
            "cover_generation": "waiting",
        },
        "step_messages": {
            "video_analysis": "运行中...",
            "product_analysis": "已完成",
            "ad_copy": "模型返回错误",
            "cover_generation": "等待确认",
        },
        "step_timing": {
            "video_analysis": {"running_seconds": 17},
            "product_analysis": {"elapsed_seconds": 8},
            "ad_copy": {"elapsed_seconds": 3},
        },
    }
    row = {"id": "task-1", "state_json": json.dumps(state, ensure_ascii=False), "display_name": "Lamp"}
    monkeypatch.setattr(
        video_cover.video_cover_project_store,
        "get_project",
        lambda task_id, *, user_id, is_admin: row,
    )

    resp = authed_client_no_db.get("/video-cover/task-1")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'class="vcd-process-card step running"' in html
    assert 'class="vcd-process-card step done"' in html
    assert 'class="vcd-process-card step error"' in html
    assert 'class="vcd-process-card step waiting"' in html
    assert 'class="vcd-step-icon step-icon running"' in html
    assert 'data-step-icon="video_analysis"' in html
    assert 'data-step-message="ad_copy"' in html
    assert 'vcd-timer-spinner spinner' in html
    assert ".vcd-process-card.running { border-color:#86efac; background:rgba(34,197,94,.10);" in html
    assert ".vcd-process-card.done { border-color:#16a34a; background:rgba(22,163,74,.18);" in html
    assert ".vcd-process-card.waiting { border-color:#fcd34d; background:rgba(217,119,6,.12);" in html
    assert ".vcd-process-card.error { border-color:#fca5a5; background:#fef2f2;" in html
    assert ".vcd-card-timer { margin-left:100px;" in html
    assert "font-weight:900;" in html
    assert "timer.innerHTML = timerHtml(step, status);" in html


def test_video_cover_detail_renders_input_card_without_get_recovery(authed_client_no_db, monkeypatch, tmp_path):
    from web.routes import video_cover

    video_path = tmp_path / "lamp.mp4"
    video_path.write_bytes(b"video")
    product_image = tmp_path / "product.jpg"
    product_image.write_bytes(b"jpg")
    state = {
        "product_url": "https://shop.example/products/lamp",
        "display_name": "Lamp",
        "video_path": str(video_path),
        "video_filename": "lamp.mp4",
        "product": {
            "title": "Portable Blender Pro",
            "main_image_url": "https://cdn.example/blender.png",
            "product_image_path": str(product_image),
        },
        "steps": {
            "video_analysis": "running",
            "product_analysis": "pending",
            "ad_copy": "pending",
            "cover_generation": "pending",
        },
        "step_messages": {"video_analysis": "运行中..."},
    }
    row = {"id": "task-1", "state_json": json.dumps(state, ensure_ascii=False), "display_name": "Lamp"}
    monkeypatch.setattr(
        video_cover.video_cover_project_store,
        "get_project",
        lambda task_id, *, user_id, is_admin: row,
    )

    resp = authed_client_no_db.get("/video-cover/task-1")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "项目输入" in html
    assert "Portable Blender Pro" in html
    assert 'data-copy-product-url="https://shop.example/products/lamp"' in html
    assert "/video-cover/api/task-1/product-image" in html
    assert "/video-cover/api/task-1/source-video" in html
    assert "vcd-product-image" in html
    assert "vcd-source-video" in html


def test_video_cover_source_media_routes_serve_project_files(authed_client_no_db, monkeypatch, tmp_path):
    from web.routes import video_cover

    video_path = tmp_path / "lamp.mp4"
    video_path.write_bytes(b"video-data")
    product_image = tmp_path / "product.jpg"
    product_image.write_bytes(b"jpg-data")
    state = {
        "video_path": str(video_path),
        "product": {"product_image_path": str(product_image)},
    }
    row = {"id": "task-1", "state_json": json.dumps(state, ensure_ascii=False), "display_name": "Lamp"}
    monkeypatch.setattr(
        video_cover.video_cover_project_store,
        "get_project",
        lambda task_id, *, user_id, is_admin: row,
    )

    video_resp = authed_client_no_db.get("/video-cover/api/task-1/source-video")
    image_resp = authed_client_no_db.get("/video-cover/api/task-1/product-image")

    assert video_resp.status_code == 200
    assert video_resp.data == b"video-data"
    assert image_resp.status_code == 200
    assert image_resp.data == b"jpg-data"
    assert image_resp.mimetype == "image/jpeg"


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
    started = []
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
    monkeypatch.setattr(
        video_cover,
        "_start_video_cover_background",
        lambda task_id, start_step="video_analysis", image_count=None: started.append((task_id, start_step, image_count)) or True,
        raising=False,
    )

    resp = authed_client_no_db.post(
        "/video-cover/api/task-1/run/video_analysis",
        data={},
    )

    assert resp.status_code == 202
    payload = resp.get_json()
    assert payload["ok"] is True
    assert started == [("task-1", "video_analysis", None)]


def test_cover_generation_step_stores_actual_image_prompts(monkeypatch, tmp_path):
    from web.routes import video_cover

    video_path = tmp_path / "lamp.mp4"
    video_path.write_bytes(b"video")
    product_image = tmp_path / "product.jpg"
    product_image.write_bytes(_png_bytes())
    state = {
        "id": "task-1",
        "type": "video_cover",
        "product_url": "https://shop.example/products/lamp",
        "video_path": str(video_path),
        "video_filename": "lamp.mp4",
        "image_count": 1,
        "product": {
            "title": "Emergency Roadside Light",
            "main_image_url": "https://cdn.example/light.png",
            "product_image_path": str(product_image),
        },
        "product_analysis": '{"product_definition":"light"}',
        "video_analysis": '{"cover_reference":"trunk scene"}',
        "ad_copy_sets": {
            "ad_copy_sets": [
                {
                    "id": 1,
                    "angle": "场景型",
                    "english": {
                        "title": "Don’t Get Stuck Unprepared",
                        "message": "Add high-visibility warning light to your trunk.",
                        "description": "Road Trips Made Safer",
                    },
                    "chinese_translation": {
                        "title": "别在紧急时毫无准备",
                        "message": "为后备箱增加高可见警示灯。",
                        "description": "让自驾更安全",
                    },
                    "usage_note": "适合后备箱场景。",
                }
            ]
        },
    }
    saved_states = []

    def fake_generate_video_covers(**kwargs):
        partial = {
            "product": state["product"],
            "reference": {"object_key": "artifacts/video_cover/8/task-1/reference.png"},
            "inputs": {},
            "models": {"cover_generation": {"provider": "local", "model_id": "gpt-image-2"}},
            "image_prompts": [
                {"index": 1, "prompt": "actual prompt without rendered text", "source_ad_copy_id": 1}
            ],
            "covers": [
                {
                    "platform": "social_reels",
                    "index": 1,
                    "object_key": "artifacts/video_cover/8/task-1/social_reels.png",
                    "copy": state["ad_copy_sets"]["ad_copy_sets"][0],
                    "formatted_copy": (
                        "标题: Don’t Get Stuck Unprepared\n"
                        "文案: Add high-visibility warning light to your trunk.\n"
                        "描述: Road Trips Made Safer"
                    ),
                    "overlay_text": "Don’t Get Stuck Unprepared",
                    "overlay_box": {"x": 52, "y": 98, "width": 800, "height": 130},
                }
            ],
        }
        kwargs["on_cover_done"](partial)
        return partial

    monkeypatch.setattr(video_cover, "generate_video_covers", fake_generate_video_covers)
    monkeypatch.setattr(video_cover, "_attach_urls", lambda payload: payload)
    monkeypatch.setattr(
        video_cover,
        "save_project_state",
        lambda task_id, next_state, status: saved_states.append(
            {"task_id": task_id, "state": json.loads(json.dumps(next_state, ensure_ascii=False)), "status": status}
        ),
    )

    video_cover._run_cover_generation_step(state, provider="local", model="gpt-image-2", user_id=8)

    request_payload = state["step_requests"]["cover_generation"]
    assert request_payload["image_prompts"][0]["prompt"] == "actual prompt without rendered text"
    assert request_payload["request_data"]["ad_copy_sets"]["ad_copy_sets"][0]["english"]["title"] == (
        "Don’t Get Stuck Unprepared"
    )
    assert saved_states[0]["task_id"] == "task-1"
    assert saved_states[0]["status"] == "running"
    assert saved_states[0]["state"]["result"]["covers"][0]["formatted_copy"].startswith("标题: Don’t Get Stuck")
    assert saved_states[0]["state"]["step_messages"]["cover_generation"] == "已生成 1/1 张封面，正在整理结果..."


def test_video_cover_state_endpoint_returns_urls_and_timing(authed_client_no_db, monkeypatch):
    from web.routes import video_cover

    state = {
        "product_url": "https://shop.example/products/lamp",
        "display_name": "Lamp",
        "image_count": 2,
        "steps": {
            "video_analysis": "done",
            "product_analysis": "done",
            "ad_copy": "running",
            "cover_generation": "pending",
        },
        "step_timing": {
            "video_analysis": {"elapsed_seconds": 12},
            "ad_copy": {"started_at": 1000.0},
        },
        "result": {
            "covers": [
                {
                    "platform": "social_reels_1",
                    "object_key": "artifacts/video_cover/1/task-1/social_reels_1.png",
                    "index": 1,
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
    monkeypatch.setattr(video_cover, "time_time", lambda: 1015.0, raising=False)

    resp = authed_client_no_db.get("/video-cover/api/task-1/state")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["ok"] is True
    assert payload["state"]["image_count"] == 2
    assert payload["state"]["step_timing"]["video_analysis"]["elapsed_seconds"] == 12
    assert payload["state"]["step_timing"]["ad_copy"]["running_seconds"] == 15
    assert payload["state"]["result"]["covers"][0]["url"]
    assert payload["state"]["result"]["covers"][0]["download_url"].endswith("/video-cover/api/task-1/download/social_reels_1")


def test_video_cover_force_restart_clears_intermediate_state_and_restarts(authed_client_no_db, monkeypatch, tmp_path):
    from web.routes import video_cover

    video_path = tmp_path / "lamp.mp4"
    video_path.write_bytes(b"video")
    saved = {}
    started = []
    state = {
        "id": "task-1",
        "type": "video_cover",
        "product_url": "https://shop.example/products/lamp",
        "video_path": str(video_path),
        "video_filename": "lamp.mp4",
        "image_count": 2,
        "steps": {
            "video_analysis": "done",
            "product_analysis": "done",
            "ad_copy": "done",
            "cover_generation": "done",
        },
        "video_analysis": "old video",
        "product_analysis": "old product",
        "ad_copy_sets": {"ad_copy_sets": []},
        "result": {"covers": []},
        "inputs": {"old": True},
        "models": {"cover_generation": {"provider": "old"}},
        "error": "old error",
        "video_analysis_structured": {"video_text": "old"},
        "product_analysis_structured": {"product_definition": "old"},
        "step_timing": {"video_analysis": {"elapsed_seconds": 1}},
        "step_requests": {"video_analysis": {"prompt": "old"}},
        "step_results": {"video_analysis": {"raw_response": "old"}},
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

    def fake_save(task_id, next_state, status=None, execute_func=None, **kwargs):
        saved["task_id"] = task_id
        saved["state"] = next_state
        saved["status"] = status

    monkeypatch.setattr(video_cover, "save_project_state", fake_save)
    monkeypatch.setattr(
        video_cover,
        "_start_video_cover_background",
        lambda task_id, start_step="video_analysis", image_count=None: started.append((task_id, start_step, image_count)) or True,
        raising=False,
    )

    resp = authed_client_no_db.post("/video-cover/api/task-1/restart", json={})

    assert resp.status_code == 202
    payload = resp.get_json()
    assert payload["ok"] is True
    assert saved["task_id"] == "task-1"
    assert saved["status"] == "running"
    next_state = saved["state"]
    assert next_state["image_count"] == 4
    assert next_state["steps"] == {
        "video_analysis": "pending",
        "product_analysis": "pending",
        "ad_copy": "pending",
        "cover_generation": "pending",
    }
    for key in (
        "video_analysis",
        "product_analysis",
        "ad_copy_sets",
        "result",
        "inputs",
        "models",
        "error",
        "video_analysis_structured",
        "product_analysis_structured",
        "step_timing",
        "step_requests",
        "step_results",
    ):
        assert key not in next_state
    assert started == [("task-1", "video_analysis", 4)]


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
