import base64
import io
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _patch_bulk_translate_startup_recovery(monkeypatch):
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)


def test_list_page_renders(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query", return_value=[]), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/multi-translate")
    assert resp.status_code == 200
    assert "多语种视频翻译".encode("utf-8") in resp.data


def test_list_filters_by_lang(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query") as m_q, \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        m_q.return_value = []
        authed_client_no_db.get("/multi-translate?lang=de")
    sql = m_q.call_args.args[0]
    assert "type = 'multi_translate'" in sql
    args = m_q.call_args.args[1]
    assert "de" in args


def test_list_query_selects_creator_name(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query", return_value=[]) as m_q, \
         patch("web.routes.multi_translate.medias._media_product_owner_name_expr", return_value="u.username"), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/multi-translate")

    assert resp.status_code == 200
    sql = m_q.call_args.args[0]
    assert "FROM projects p" in sql
    assert "LEFT JOIN users u ON u.id = p.user_id" in sql
    assert "u.username AS creator_name" in sql


def test_list_page_renders_creator_name(authed_client_no_db):
    project = {
        "id": "task-1",
        "original_filename": "demo.mp4",
        "display_name": "示例项目",
        "thumbnail_path": "",
        "status": "done",
        "state_json": "{}",
        "created_at": datetime(2026, 4, 23, 22, 1),
        "expires_at": None,
        "deleted_at": None,
        "creator_name": "张三",
    }

    with patch("web.routes.multi_translate.db_query", return_value=[project]), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/multi-translate")

    assert resp.status_code == 200
    assert "创建人：张三".encode("utf-8") in resp.data


def test_detail_404_for_other_user(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query_one", return_value=None), \
         patch("appcore.task_recovery.recover_project_if_needed"):
        resp = authed_client_no_db.get("/multi-translate/unknown")
    assert resp.status_code == 404


def test_admin_list_does_not_scope_multi_translate_projects_to_self(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query", return_value=[]) as m_q, \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("web.routes.multi_translate.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/multi-translate")

    assert resp.status_code == 200
    sql = m_q.call_args.args[0].lower()
    args = m_q.call_args.args[1]
    assert "user_id = %s" not in sql
    assert "user_id=%s" not in sql
    assert args == ()


def test_admin_detail_can_view_other_users_multi_translate_project(authed_client_no_db):
    project = {
        "id": "foreign-multi-task",
        "user_id": 237,
        "type": "multi_translate",
        "display_name": "Foreign multi",
        "original_filename": "foreign.mp4",
        "status": "done",
        "deleted_at": None,
        "state_json": json.dumps({"target_lang": "de"}, ensure_ascii=False),
    }

    def fake_query_one(sql, args):
        if "user_id = %s" in sql.lower() or "user_id=%s" in sql.lower():
            return None
        return project

    with patch("web.routes.multi_translate.db_query_one", side_effect=fake_query_one), \
         patch("web.routes.multi_translate.recover_project_if_needed"), \
         patch("appcore.api_keys.get_key", return_value="openrouter"):
        resp = authed_client_no_db.get("/multi-translate/foreign-multi-task")

    assert resp.status_code == 200


def test_admin_can_get_other_users_multi_translate_task(authed_client_no_db, monkeypatch):
    from web.routes import multi_translate as r

    task = {
        "id": "foreign-multi-task",
        "type": "multi_translate",
        "status": "done",
        "_user_id": 237,
    }
    monkeypatch.setattr(r.store, "get", lambda task_id: task if task_id == task["id"] else None)
    monkeypatch.setattr(r, "recover_task_if_needed", lambda task_id: None)

    resp = authed_client_no_db.get("/api/multi-translate/foreign-multi-task")

    assert resp.status_code == 200
    assert resp.get_json()["id"] == "foreign-multi-task"


def test_normal_user_cannot_get_other_users_multi_translate_task(authed_user_client_no_db, monkeypatch):
    from web.routes import multi_translate as r

    task = {
        "id": "foreign-multi-task",
        "type": "multi_translate",
        "status": "done",
        "_user_id": 237,
    }
    monkeypatch.setattr(r.store, "get", lambda task_id: task if task_id == task["id"] else None)
    monkeypatch.setattr(r, "recover_task_if_needed", lambda task_id: None)

    resp = authed_user_client_no_db.get("/api/multi-translate/foreign-multi-task")

    assert resp.status_code == 404


def test_admin_can_read_other_users_multi_translate_subtitle_preview(authed_client_no_db, monkeypatch):
    def fake_query_one(sql, args):
        if "user_id = %s" in sql.lower() or "user_id=%s" in sql.lower():
            return None
        return {"id": args[0], "user_id": 237}

    monkeypatch.setattr("web.routes.multi_translate.db_query_one", fake_query_one)
    monkeypatch.setattr(
        "web.routes.multi_translate.build_multi_translate_preview_payload",
        lambda task_id, user_id: {"video_url": "/media/demo.mp4", "sample_lines": []},
    )

    resp = authed_client_no_db.get("/api/multi-translate/foreign-multi-task/subtitle-preview")

    assert resp.status_code == 200
    assert resp.get_json()["video_url"] == "/media/demo.mp4"


def test_admin_can_read_other_users_multi_translate_voice_library(authed_client_no_db, monkeypatch):
    def fake_query_one(sql, args):
        if "user_id = %s" in sql.lower() or "user_id=%s" in sql.lower():
            return None
        return {"state_json": json.dumps({"target_lang": "de"}, ensure_ascii=False)}

    monkeypatch.setattr("web.routes.multi_translate.db_query_one", fake_query_one)
    monkeypatch.setattr(
        "appcore.voice_library_browse.list_voices",
        lambda **kwargs: {"items": [{"voice_id": "v1"}], "total": 1},
    )
    monkeypatch.setattr("appcore.video_translate_defaults.resolve_default_voice", lambda *args, **kwargs: None)

    resp = authed_client_no_db.get("/api/multi-translate/foreign-multi-task/voice-library")

    assert resp.status_code == 200
    assert resp.get_json()["items"] == [{"voice_id": "v1"}]


def test_rematch_excludes_default_voice_from_top10(authed_client_no_db):
    state = {
        "target_lang": "de",
        "voice_match_query_embedding": base64.b64encode(b"fake-embedding").decode("ascii"),
    }
    with patch(
        "web.routes.multi_translate.db_query_one",
        return_value={"state_json": json.dumps(state, ensure_ascii=False)},
    ), patch(
        "web.routes.multi_translate.db_execute",
    ), patch(
        "appcore.video_translate_defaults.resolve_default_voice",
        return_value="default-voice-id",
    ), patch(
        "pipeline.voice_embedding.deserialize_embedding",
        return_value="decoded-embedding",
    ), patch(
        "pipeline.voice_match.match_candidates",
        return_value=[{"voice_id": "voice-b", "similarity": 0.91}],
    ) as m_match, patch(
        "appcore.voice_library_browse.fetch_voices_by_ids",
        return_value=[{"voice_id": "voice-b", "name": "B", "gender": "female"}],
    ) as m_fetch:
        resp = authed_client_no_db.post(
            "/api/multi-translate/task-1/rematch",
            json={"gender": "female"},
        )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["candidates"][0]["voice_id"] == "voice-b"
    assert payload["extra_items"][0]["voice_id"] == "voice-b"
    assert m_match.call_args.kwargs["exclude_voice_ids"] == {"default-voice-id"}
    assert m_match.call_args.kwargs["top_k"] == 10
    assert m_fetch.call_args.kwargs["voice_ids"] == ["voice-b"]


def test_multi_translate_start_accepts_local_multipart_and_marks_local_primary(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.multi_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.multi_translate.db_execute", lambda sql, args: None)
    started = {}
    monkeypatch.setattr(
        "web.routes.multi_translate.multi_pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "de",
            "video": (io.BytesIO(b"multi-video"), "demo.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    from web import store

    task = store.get(payload["task_id"])
    assert task["type"] == "multi_translate"
    assert task["target_lang"] == "de"
    assert task["delivery_mode"] == "local_primary"
    assert task["source_tos_key"] == ""
    assert task["source_object_info"]["content_type"] == "video/mp4"
    assert task["source_object_info"]["file_size"] == len(b"multi-video")
    assert task["source_object_info"]["storage_backend"] == "local"
    assert task["source_object_info"]["original_filename"] == "demo.mp4"
    assert task["source_object_info"]["uploaded_at"]
    assert started["task_id"] == payload["task_id"]


def test_multi_translate_start_keeps_ja_on_multi_translate(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.multi_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.multi_translate.db_execute", lambda sql, args: None)
    monkeypatch.setattr(
        "web.routes.multi_translate.create_ja_translate_task_from_upload",
        lambda *args, **kwargs: pytest.fail("ja uploads should stay on multi_translate"),
        raising=False,
    )
    started = {}
    monkeypatch.setattr(
        "web.routes.multi_translate.multi_pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "ja",
            "video": (io.BytesIO(b"ja-multi-video"), "demo-ja.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    from web import store

    task = store.get(payload["task_id"])
    assert task["type"] == "multi_translate"
    assert task["target_lang"] == "ja"
    assert started["task_id"] == payload["task_id"]


def test_multi_translate_start_generates_thumbnail_from_uploaded_video(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.multi_translate.db_query_one", lambda sql, args: None)
    db_updates = []
    monkeypatch.setattr("web.routes.multi_translate.db_execute", lambda sql, args: db_updates.append((sql, args)))
    monkeypatch.setattr("web.routes.multi_translate.multi_pipeline_runner.start", lambda task_id, user_id=None: None)

    def fake_extract_thumbnail(video_path, output_dir, scale=None):
        thumb = Path(output_dir) / "thumbnail.jpg"
        thumb.write_bytes(b"first-frame")
        return str(thumb)

    monkeypatch.setattr("pipeline.ffutil.extract_thumbnail", fake_extract_thumbnail)

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "de",
            "video": (io.BytesIO(b"multi-video"), "demo.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    from web import store

    task = store.get(payload["task_id"])
    expected_thumb = str(tmp_path / "output" / payload["task_id"] / "thumbnail.jpg")
    assert task["thumbnail_path"] == expected_thumb
    assert ("UPDATE projects SET thumbnail_path = %s WHERE id = %s", (expected_thumb, payload["task_id"])) in db_updates


def test_multi_translate_list_page_uses_local_multipart_upload():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "multi_translate_list.html").read_text(encoding="utf-8")

    assert "new FormData(uploadForm)" in template
    assert "fetch('/api/multi-translate/start'" in template
    assert "/api/multi-translate/bootstrap" not in template
    assert "/api/multi-translate/complete" not in template
    assert "/api/multi-translate/compat-bootstrap" not in template
    assert "/api/multi-translate/compat-complete" not in template
    assert "xhr.open('PUT'" not in template


def test_multi_translate_create_modal_uses_visible_target_language_select():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "multi_translate_list.html").read_text(encoding="utf-8")

    assert 'id="targetLangSelect"' in template
    assert 'name="target_lang"' in template
    assert "document.getElementById('targetLangSelect')" in template
    assert "formData.set('target_lang', targetLang)" in template
    assert "var targetLang = _getTargetLang();" not in template


def test_multi_translate_create_modal_uses_pill_buttons_and_dropzone():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "multi_translate_list.html").read_text(encoding="utf-8")

    # 红字粗体提示
    assert 'id="langWarning"' in template
    assert "先选择目标语言" in template

    # 胶囊按钮代替 select
    assert 'id="modalLangPills"' in template
    assert 'class="modal-lang-pill"' in template
    assert 'data-lang="{{ lang }}"' in template
    # 不再有 <select> 元素
    assert "<select id=\"targetLangSelect\"" not in template
    # 没有默认选中（不再用 current_lang or 'de'）
    assert "(current_lang or 'de')" not in template
    # 隐藏 input 保留接口名称
    assert 'type="hidden" id="targetLangSelect" name="target_lang" value=""' in template

    # 拖拽区与 270x480 预览
    assert 'id="videoDropzone"' in template
    assert "拖拽视频到这里" in template
    assert 'id="videoPreviewWrap"' in template
    assert 'id="videoPreview"' in template
    assert "width: 270px; height: 480px" in template

    # 拖放 + 预览 JS
    assert "URL.createObjectURL" in template
    assert "URL.revokeObjectURL" in template
    assert "addEventListener('drop'" in template
    assert "new DataTransfer()" in template

    # 校验：未选语言时弹窗 + 抖动
    assert "没有选择语言" in template
    assert "warning.classList.add('shake')" in template

    # 大号弹窗
    assert 'class="modal-box modal-box-wide"' in template

    # 项目名输入框（视频下方，可选）
    assert 'id="projectNameField"' in template
    assert 'id="projectName"' in template
    assert 'name="display_name"' in template
    # 中文语言名映射
    assert "LANG_ZH_NAMES" in template
    assert "de: '德语'" in template
    # 文件名解析正则：YYYY.MM.DD-产品名-...
    assert "/^\\d{4}\\.\\d{1,2}\\.\\d{1,2}-([^-]+)-/" in template
    # MMDD-HHmm 拼接
    assert "_formatMMDDHHmm" in template
    # 提交时附带 display_name
    assert "formData.set('display_name'" in template


def test_multi_translate_start_uses_user_display_name(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.multi_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.multi_translate.db_execute", lambda sql, args: None)
    monkeypatch.setattr(
        "web.routes.multi_translate.multi_pipeline_runner.start",
        lambda task_id, user_id=None: None,
    )

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "de",
            "display_name": "德语ABC-0425-1200",
            "video": (io.BytesIO(b"multi-video"), "demo.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    from web import store
    task = store.get(payload["task_id"])
    assert task["display_name"] == "德语ABC-0425-1200"


def test_multi_translate_index_filters_pills_by_enabled_languages(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.db_query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.settings.get_retention_hours", lambda *_args, **_kw: 72)
    monkeypatch.setattr("appcore.task_recovery.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr(
        "appcore.medias.list_enabled_language_codes",
        lambda: ["de", "ja"],
    )

    resp = authed_client_no_db.get("/multi-translate")

    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert 'data-lang="de"' in body
    assert 'data-lang="ja"' in body
    # fi 被排除（未启用）
    assert 'data-lang="fi"' not in body
    assert 'data-lang="fr"' not in body


def test_multi_translate_top_filter_includes_enabled_languages_plus_english(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.db_query", lambda *args, **kwargs: [])
    monkeypatch.setattr("appcore.settings.get_retention_hours", lambda *_args, **_kw: 72)
    monkeypatch.setattr("appcore.task_recovery.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["de", "ja"])

    resp = authed_client_no_db.get("/multi-translate")

    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    # 顶部 lang-pills 链接：启用语言 + 英语
    assert 'href="/multi-translate?lang=de"' in body
    assert 'href="/multi-translate?lang=ja"' in body
    assert 'href="/multi-translate?lang=en"' in body
    # 未启用的 fi/fr 不在顶部
    assert 'href="/multi-translate?lang=fi"' not in body
    assert 'href="/multi-translate?lang=fr"' not in body
    # 英语标签
    assert "🇬🇧 英语" in body
    # 弹窗胶囊也含英语（en 作为兜底目标语言强制追加到末尾）
    assert 'data-lang="en"' in body


def test_list_enabled_target_langs_forces_en_at_tail(monkeypatch):
    """无论 media_languages 启用何种集合，创建模态语言列表都必须以 en 收尾。"""
    from web.routes import multi_translate as r

    # 启用 de/ja：交集 = [de, ja]，强制追加 en 到末尾
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["de", "ja"])
    assert r._list_enabled_target_langs() == ("de", "ja", "en")

    # 启用集合已含 en：先剥离再放末尾，不重复
    monkeypatch.setattr(
        "appcore.medias.list_enabled_language_codes",
        lambda: ["en", "de", "fr"],
    )
    assert r._list_enabled_target_langs() == ("de", "fr", "en")

    # 启用集合为空：退回 SUPPORTED_LANGS（最后一项已是 en）
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: [])
    assert r._list_enabled_target_langs()[-1] == "en"


def test_multi_translate_index_accepts_english_lang_filter(authed_client_no_db, monkeypatch):
    monkeypatch.setattr("appcore.task_recovery.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("appcore.settings.get_retention_hours", lambda *_args, **_kw: 72)
    monkeypatch.setattr("appcore.medias.list_enabled_language_codes", lambda: ["de"])
    with patch("web.routes.multi_translate.db_query") as m_q:
        m_q.return_value = []
        resp = authed_client_no_db.get("/multi-translate?lang=en")

    assert resp.status_code == 200
    args = m_q.call_args.args[1]
    assert "en" in args


def test_multi_translate_start_rejects_disabled_target_lang(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.multi_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.multi_translate.db_execute", lambda sql, args: None)
    monkeypatch.setattr(
        "web.routes.multi_translate.multi_pipeline_runner.start",
        lambda task_id, user_id=None: None,
    )
    monkeypatch.setattr(
        "appcore.medias.list_enabled_language_codes",
        lambda: ["de"],  # 仅启用德语
    )

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "fi",
            "video": (io.BytesIO(b"multi-video"), "demo.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "target_lang" in response.get_json().get("error", "")


def test_voice_selector_multi_exposes_single_frame_subtitle_preview():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_voice_selector_multi.html").read_text(encoding="utf-8")
    script = (root / "web" / "static" / "voice_selector_multi.js").read_text(encoding="utf-8")

    assert 'id="vsPreviewFrame"' in template
    assert 'id="vsPreviewVideo"' in template
    assert 'preload="metadata"' in template
    assert 'id="vsPreviewSubtitle"' in template
    assert 'id="vsPreviewNote"' in template
    assert "loadSubtitlePreviewPayload" in script
    assert "const apiBase = ((window.TASK_WORKBENCH_CONFIG || {}).apiBase || '/api/multi-translate').replace(/\\/$/, '');" in script
    assert "const subtitlePreviewUrl = `${apiBase}/${taskId}/subtitle-preview`;" in script
    assert "fetch(subtitlePreviewUrl, { cache: \"no-store\" })" in script
    assert "applySubtitlePreviewPayload" in script
    assert "attachPreviewVideo" in script
    assert "tryAttachPreviewVideo" in script
    assert "vsPreviewSubtitle" in script
    assert "pointerdown" in script


def test_voice_selector_multi_uses_configured_api_base_for_shared_endpoints():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "voice_selector_multi.js").read_text(encoding="utf-8")

    assert "fetch(`${apiBase}/${taskId}/voice-library`)" in script
    assert "fetch(userDefaultVoiceApi, {" in script
    assert "fetch(`${apiBase}/${taskId}/confirm-voice`, {" in script
    assert "fetch(`${apiBase}/${taskId}/rematch`, {" in script
    assert "`/api/multi-translate/${taskId}/voice-library`" not in script
    assert "`/api/multi-translate/${taskId}/confirm-voice`" not in script
    assert "`/api/multi-translate/${taskId}/rematch`" not in script


def test_voice_selector_multi_does_not_autoplay_result_video_after_compose():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "voice_selector_multi.js").read_text(encoding="utf-8")

    assert "function loadResultVideo(src)" in script
    assert "resultVideo.play().catch(() => {});" not in script


def test_multi_translate_subtitle_preview_route(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.multi_translate.build_multi_translate_preview_payload",
        lambda task_id, user_id: {
            "video_url": "/media/demo.mp4",
            "subtitle_font": "Impact",
            "subtitle_size": 14,
            "subtitle_position_y": 0.68,
            "sample_lines": [
                "Tiktok and facebook shot videos!",
                "Tiktok and facebook shot videos!",
            ],
        },
    )
    monkeypatch.setattr(
        "web.routes.multi_translate.db_query_one",
        lambda sql, args: {"id": args[0], "state_json": "{}"},
    )

    resp = authed_client_no_db.get("/api/multi-translate/task-1/subtitle-preview")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["video_url"] == "/media/demo.mp4"
    assert payload["sample_lines"] == [
        "Tiktok and facebook shot videos!",
        "Tiktok and facebook shot videos!",
    ]


def test_multi_translate_detail_removes_top_shared_subtitle_preview_assets():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "multi_translate_detail.html").read_text(encoding="utf-8")
    shared_shell = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")
    preview_panel = (root / "web" / "templates" / "_subtitle_preview_panel.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")
    workbench = (root / "web" / "templates" / "_task_workbench.html").read_text(encoding="utf-8")

    assert "_subtitle_preview_panel.html" not in template
    assert "subtitle_preview.js" not in template
    assert 'id="sharedSubtitlePreviewMount"' not in template
    assert "voice_selector_multi.js" in shared_shell
    assert "--subtitle-preview-w: 270px;" in preview_panel
    assert "--subtitle-preview-h: 480px;" in preview_panel
    assert "sharedSubtitlePreviewMount" in workbench
    assert 'data-role="upload-cta"' in preview_panel
    assert 'data-role="file"' in preview_panel
    assert "拖拽视频到这里" in preview_panel
    assert "openPhonePickerBtn" not in scripts
    assert "phoneFrame" not in scripts
    assert "pfSubtitleBar" not in scripts
    assert "createSubtitlePreviewController" in scripts


def test_shared_subtitle_preview_supports_local_video_upload():
    root = Path(__file__).resolve().parents[1]
    preview_panel = (root / "web" / "templates" / "_subtitle_preview_panel.html").read_text(encoding="utf-8")
    script = (root / "web" / "static" / "subtitle_preview.js").read_text(encoding="utf-8")

    assert 'accept="video/*,.mp4,.mov,.m4v,.webm,.avi,.mkv"' in preview_panel
    assert "仅用于当前浏览器预览，不会替换任务源视频" in preview_panel
    assert "URL.createObjectURL(file)" in script
    assert "URL.revokeObjectURL" in script
    assert 'addEventListener("drop"' in script
    assert "视频加载失败，拖拽本地视频重新预览" in script


def test_multi_translate_detail_displays_asr_result_before_extracted_audio():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "_translate_detail_shell.html").read_text(encoding="utf-8")
    scripts = (root / "web" / "templates" / "_task_workbench_scripts.html").read_text(encoding="utf-8")

    assert "#pipelineCard .steps > #step-asr" in template
    assert "#pipelineCard .steps > #step-extract" in template
    assert 'const STEP_ORDER = ["extract", "asr"' in scripts
def test_multi_translate_complete_rejects_new_pure_tos_creation(authed_client_no_db):
    resp = authed_client_no_db.post(
        "/api/multi-translate/complete",
        json={
            "task_id": "multi-task-from-tos",
            "object_key": "uploads/1/multi-task-from-tos/demo.mp4",
            "original_filename": "demo.mp4",
            "target_lang": "de",
        },
    )

    assert resp.status_code == 410
    assert "本地" in resp.get_json()["error"]
def test_confirm_voice_resumes_parent_bulk_translate_scheduler(authed_client_no_db):
    state = {
        "target_lang": "de",
        "medias_context": {
            "parent_task_id": "bulk-parent-1",
        },
    }
    resume_calls = []
    bg_calls = []

    with patch(
        "web.routes.multi_translate.db_query_one",
        return_value={"state_json": json.dumps(state, ensure_ascii=False)},
    ), patch(
        "web.routes.multi_translate.db_execute",
    ), patch(
        "web.routes.multi_translate.task_state.update",
    ), patch(
        "web.routes.multi_translate.task_state.set_step",
    ), patch(
        "web.routes.multi_translate.task_state.set_current_review_step",
    ), patch(
        "web.routes.multi_translate.multi_pipeline_runner.resume",
        side_effect=lambda task_id, start_step, user_id=None: resume_calls.append((task_id, start_step, user_id)),
    ), patch(
        "web.background.start_background_task",
        side_effect=lambda fn, task_id: bg_calls.append((fn, task_id)),
    ):
        resp = authed_client_no_db.post(
            "/api/multi-translate/task-voice-1/confirm-voice",
            json={"voice_id": "voice-1", "voice_name": "Voice 1"},
        )

    assert resp.status_code == 200
    assert resume_calls == [("task-voice-1", "alignment", 1)]
    assert len(bg_calls) == 1
    assert callable(bg_calls[0][0])
    assert bg_calls[0][1] == "bulk-parent-1"


def test_multi_translate_start_accepts_target_lang_en(tmp_path, authed_client_no_db, monkeypatch):
    monkeypatch.setattr("web.routes.multi_translate.OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr("web.routes.multi_translate.UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr("web.routes.multi_translate.db_query_one", lambda sql, args: None)
    monkeypatch.setattr("web.routes.multi_translate.db_execute", lambda sql, args: None)
    started = {}
    monkeypatch.setattr(
        "web.routes.multi_translate.multi_pipeline_runner.start",
        lambda task_id, user_id=None: started.update({"task_id": task_id, "user_id": user_id}),
    )

    response = authed_client_no_db.post(
        "/api/multi-translate/start",
        data={
            "target_lang": "en",
            "video": (io.BytesIO(b"english-video"), "demo-en.mp4"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 201
    payload = response.get_json()
    from web import store

    task = store.get(payload["task_id"])
    assert task["type"] == "multi_translate"
    assert task["target_lang"] == "en"
    assert started["task_id"] == payload["task_id"]


def test_multi_translate_list_template_exposes_en_label_in_lang_label_map():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "multi_translate_list.html").read_text(encoding="utf-8")

    # 全局 lang_label_map 含 EN（统一驱动顶部筛选 pill 与模态目标语言 pill）
    assert "'en':'🇬🇧 英语'" in template


def test_multi_translate_list_renders_en_pill_when_supported(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query", return_value=[]), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"), \
         patch("web.routes.multi_translate._list_filter_langs",
               return_value=("de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en")), \
         patch("web.routes.multi_translate._list_enabled_target_langs",
               return_value=("de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en")):
        resp = authed_client_no_db.get("/multi-translate")

    assert resp.status_code == 200
    assert "🇬🇧 英语".encode("utf-8") in resp.data
