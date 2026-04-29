from __future__ import annotations

from pathlib import Path


def test_build_fingerprints_changes_when_copy_changes(monkeypatch):
    from appcore import push_quality_checks as qc

    item = {
        "id": 7,
        "product_id": 3,
        "lang": "de",
        "object_key": "videos/v1.mp4",
        "cover_object_key": "covers/c1.jpg",
    }
    product = {"id": 3, "name": "Demo"}
    monkeypatch.setattr(
        qc.pushes,
        "resolve_localized_text_payload",
        lambda item: {
            "title": "Titel",
            "message": "Hallo",
            "description": "Beschreibung",
            "lang": "德语",
        },
    )

    first = qc.build_fingerprints(item, product)
    monkeypatch.setattr(
        qc.pushes,
        "resolve_localized_text_payload",
        lambda item: {
            "title": "Titel",
            "message": "Neu",
            "description": "Beschreibung",
            "lang": "德语",
        },
    )
    second = qc.build_fingerprints(item, product)

    assert first.copy_fingerprint != second.copy_fingerprint
    assert first.cover_fingerprint == second.cover_fingerprint
    assert first.video_fingerprint == second.video_fingerprint


def test_find_reusable_auto_result_returns_existing_same_fingerprint(monkeypatch):
    from appcore import push_quality_checks as qc

    captured = {}
    monkeypatch.setattr(qc, "ensure_table", lambda: None)

    def fake_query_one(sql, args):
        captured["args"] = args
        return {
            "id": 11,
            "item_id": 9,
            "product_id": 3,
            "lang": "de",
            "attempt_source": "auto",
            "status": "failed",
            "summary": "bad",
            "failed_reasons": '["文案混入英文"]',
            "copy_result_json": "{}",
            "cover_result_json": "{}",
            "video_result_json": "{}",
            "provider": "openrouter",
            "model": qc.MODEL,
            "started_at": None,
            "finished_at": None,
            "created_at": None,
            "updated_at": None,
        }

    monkeypatch.setattr(qc, "query_one", fake_query_one)

    result = qc.find_reusable_auto_result(9, "copy", "cover", "video")

    assert result["id"] == 11
    assert result["failed_reasons"] == ["文案混入英文"]
    assert captured["args"][:5] == (9, "copy", "cover", "video", "auto")


def test_evaluate_item_auto_reuses_existing_without_llm(monkeypatch):
    from appcore import push_quality_checks as qc

    monkeypatch.setattr(
        qc.medias,
        "get_item",
        lambda item_id: {"id": item_id, "product_id": 1, "lang": "de"},
    )
    monkeypatch.setattr(qc.medias, "get_product", lambda product_id: {"id": product_id})
    monkeypatch.setattr(
        qc,
        "build_fingerprints",
        lambda item, product: qc.QualityFingerprints("copy", "cover", "video"),
    )
    monkeypatch.setattr(
        qc,
        "find_reusable_auto_result",
        lambda item_id, copy_fp, cover_fp, video_fp: {"id": 5, "status": "passed"},
    )
    monkeypatch.setattr(
        qc,
        "run_three_checks",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not call LLM")),
    )

    assert qc.evaluate_item(9, source="auto")["id"] == 5


def test_evaluate_item_manual_ignores_auto_reuse(monkeypatch):
    from appcore import push_quality_checks as qc

    calls = []
    monkeypatch.setattr(
        qc.medias,
        "get_item",
        lambda item_id: {"id": item_id, "product_id": 1, "lang": "de"},
    )
    monkeypatch.setattr(qc.medias, "get_product", lambda product_id: {"id": product_id})
    monkeypatch.setattr(
        qc,
        "build_fingerprints",
        lambda item, product: qc.QualityFingerprints("copy", "cover", "video"),
    )
    monkeypatch.setattr(
        qc,
        "find_reusable_auto_result",
        lambda item_id, copy_fp, cover_fp, video_fp: {"id": 5, "status": "passed"},
    )
    monkeypatch.setattr(qc, "_record_running", lambda *args, **kwargs: 22)
    monkeypatch.setattr(
        qc,
        "_record_finish",
        lambda check_id, result: calls.append((check_id, result)) or {"id": check_id, **result},
    )
    monkeypatch.setattr(
        qc,
        "run_three_checks",
        lambda item, product, fingerprints: {
            "status": "passed",
            "summary": "ok",
            "failed_reasons": [],
            "copy_result": {"status": "passed"},
            "cover_result": {"status": "passed"},
            "video_result": {"status": "passed"},
        },
    )

    result = qc.evaluate_item(9, source="manual")

    assert result["id"] == 22
    assert calls


def test_run_three_checks_reuses_copy_result_for_same_copy(monkeypatch):
    from appcore import push_quality_checks as qc

    item = {"id": 7, "product_id": 3, "lang": "de"}
    product = {"id": 3, "name": "Demo"}
    fingerprints = qc.QualityFingerprints("same-copy", "new-cover", "new-video")
    reused_copy = {
        "status": "passed",
        "summary": "复用文案检查",
        "issues": [],
        "reused": True,
    }
    monkeypatch.setattr(qc, "find_reusable_copy_result", lambda product_id, lang, copy_fp: reused_copy)
    monkeypatch.setattr(
        qc,
        "check_copy",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("copy should be reused")),
    )
    monkeypatch.setattr(qc, "check_cover", lambda item, product: {"status": "passed", "summary": "cover ok"})
    monkeypatch.setattr(qc, "check_video", lambda item, product: {"status": "passed", "summary": "video ok"})

    result = qc.run_three_checks(item, product, fingerprints)

    assert result["status"] == "passed"
    assert result["copy_result"] is reused_copy


def test_run_three_checks_keeps_per_factor_error(monkeypatch):
    from appcore import push_quality_checks as qc

    item = {"id": 7, "product_id": 3, "lang": "de"}
    product = {"id": 3, "name": "Demo"}
    fingerprints = qc.QualityFingerprints("copy", "cover", "video")

    monkeypatch.setattr(qc, "find_reusable_copy_result", lambda product_id, lang, copy_fp: None)
    monkeypatch.setattr(qc.pushes, "resolve_localized_text_payload", lambda item_arg: {"title": "Titel"})
    monkeypatch.setattr(qc, "check_copy", lambda item_arg, product_arg, payload: {"status": "passed", "summary": "copy ok"})
    monkeypatch.setattr(qc, "check_cover", lambda item_arg, product_arg: (_ for _ in ()).throw(RuntimeError("cover model timeout")))
    monkeypatch.setattr(qc, "check_video", lambda item_arg, product_arg: {"status": "passed", "summary": "video ok"})

    result = qc.run_three_checks(item, product, fingerprints)

    assert result["status"] == "error"
    assert result["copy_result"]["status"] == "passed"
    assert result["cover_result"]["status"] == "error"
    assert result["video_result"]["status"] == "passed"
    assert result["failed_reasons"] == ["封面图: cover model timeout"]


def test_copy_check_uses_openrouter_gemini_flash_lite(monkeypatch):
    from appcore import push_quality_checks as qc

    captured = {}

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {
            "json": {
                "status": "passed",
                "is_clean": True,
                "summary": "纯净德语文案",
                "issues": [],
            }
        }

    monkeypatch.setattr(qc.llm_client, "invoke_chat", fake_invoke_chat)

    result = qc.check_copy(
        {"id": 7, "product_id": 3, "lang": "de"},
        {"id": 3, "name": "Demo", "product_code": "demo-rjc"},
        {
            "title": "Titel",
            "message": "Hallo Welt",
            "description": "Beschreibung",
            "lang": "德语",
        },
    )

    assert result["status"] == "passed"
    assert captured["use_case_code"] == qc.USE_CASE_CODE
    assert captured["kwargs"]["provider_override"] == "openrouter"
    assert captured["kwargs"]["model_override"] == qc.MODEL


def test_visual_checks_use_openrouter_gemini_flash_lite(monkeypatch, tmp_path):
    from appcore import push_quality_checks as qc

    media_path = tmp_path / "media.jpg"
    media_path.write_bytes(b"image")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"video")
    calls = []

    def fake_invoke_generate(use_case_code, **kwargs):
        calls.append((use_case_code, kwargs))
        return {
            "json": {
                "status": "passed",
                "is_clean": True,
                "summary": "素材干净",
                "issues": [],
            }
        }

    monkeypatch.setattr(qc, "_materialize_media", lambda object_key: media_path)
    monkeypatch.setattr(qc, "_make_video_clip_5s", lambda source, item_id: clip_path)
    monkeypatch.setattr(qc.llm_client, "invoke_generate", fake_invoke_generate)

    item = {
        "id": 7,
        "product_id": 3,
        "lang": "de",
        "cover_object_key": "covers/demo.jpg",
        "object_key": "videos/demo.mp4",
    }
    product = {"id": 3, "name": "Demo", "product_code": "demo-rjc"}

    assert qc.check_cover(item, product)["status"] == "passed"
    assert qc.check_video(item, product)["status"] == "passed"

    assert [call[0] for call in calls] == [qc.USE_CASE_CODE, qc.USE_CASE_CODE]
    assert all(call[1]["provider_override"] == "openrouter" for call in calls)
    assert all(call[1]["model_override"] == qc.MODEL for call in calls)
    assert calls[1][1]["media"] == [clip_path]
    assert calls[1][1]["billing_extra"]["clip_seconds"] == 5


def test_push_quality_schema_requires_language_evidence_fields():
    from appcore import push_quality_checks as qc

    schema = qc._response_schema()

    assert "target_language_match" in schema["required"]
    assert "detected_languages" in schema["required"]
    assert "evidence" in schema["required"]
    assert "speech_language" in schema["properties"]
    assert "subtitle_language" in schema["properties"]
    assert "ocr_text" in schema["properties"]
    assert "checked_scope" in schema["properties"]


def test_copy_prompt_demands_each_copy_field_target_language(monkeypatch):
    from appcore import push_quality_checks as qc

    captured = {}

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["messages"] = kwargs["messages"]
        return {
            "json": {
                "status": "passed",
                "is_clean": True,
                "summary": "ok",
                "issues": [],
                "target_language_match": True,
                "detected_languages": ["German"],
                "evidence": ["title/message/description are German"],
            }
        }

    monkeypatch.setattr(qc.llm_client, "invoke_chat", fake_invoke_chat)

    qc.check_copy(
        {"id": 7, "product_id": 3, "lang": "de"},
        {"id": 3, "name": "Demo", "product_code": "demo-rjc"},
        {"title": "Titel", "message": "Hallo Welt", "description": "Beschreibung"},
    )

    prompt = captured["messages"][-1]["content"]
    assert "逐字段" in prompt
    assert "title" in prompt
    assert "message" in prompt
    assert "description" in prompt
    assert "非目标语种营销文案" in prompt


def test_video_prompt_requires_speech_subtitle_and_visual_language_checks():
    from appcore import push_quality_checks as qc

    prompt = qc._visual_prompt(
        "视频抽样片段",
        {"id": 7, "product_id": 3, "lang": "de"},
        {"id": 3, "name": "Demo", "product_code": "demo-rjc"},
    )

    assert "语音" in prompt
    assert "旁白" in prompt
    assert "字幕" in prompt
    assert "画面文字" in prompt
    assert "目标语种" in prompt
    assert "不要默认通过" in prompt


def test_normalize_model_result_preserves_language_evidence_fields():
    from appcore import push_quality_checks as qc

    result = qc._normalize_model_result({
        "status": "warning",
        "is_clean": False,
        "summary": "视频语音不是目标语种",
        "issues": ["旁白为英文"],
        "target_language_match": False,
        "detected_languages": ["English"],
        "evidence": ["heard English narration"],
        "speech_language": "English",
        "subtitle_language": "German",
        "ocr_text": ["Deutsch text"],
        "checked_scope": "视频抽样片段",
    })

    assert result["target_language_match"] is False
    assert result["detected_languages"] == ["English"]
    assert result["evidence"] == ["heard English narration"]
    assert result["speech_language"] == "English"
    assert result["subtitle_language"] == "German"
    assert result["ocr_text"] == ["Deutsch text"]
    assert result["checked_scope"] == "视频抽样片段"


def test_video_clip_uses_first_five_seconds(monkeypatch, tmp_path):
    from appcore import push_quality_checks as qc

    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    captured = {}

    def fake_run(cmd, capture_output, timeout, check):
        captured["cmd"] = cmd
        output = Path(cmd[-1])
        output.write_bytes(b"clip")

        class Result:
            returncode = 0
            stderr = b""

        return Result()

    monkeypatch.setattr(qc.subprocess, "run", fake_run)

    clip = qc._make_video_clip_5s(source, item_id=12)

    assert clip.is_file()
    assert "-t" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("-t") + 1] == "5"
