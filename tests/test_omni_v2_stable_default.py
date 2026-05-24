from __future__ import annotations

import io
import json

import pytest


OMNI_STANDARD_CFG = {
    "asr_post": "asr_clean",
    "shot_decompose": False,
    "translate_algo": "standard",
    "source_anchored": True,
    "tts_strategy": "five_round_rewrite",
    "subtitle": "asr_realign",
    "voice_separation": True,
    "loudness_match": True,
    "av_sync_audit": "off",
}


AV_SENTENCE_CFG = {
    "asr_post": "asr_clean",
    "shot_decompose": False,
    "translate_algo": "av_sentence",
    "source_anchored": False,
    "tts_strategy": "sentence_reconcile",
    "subtitle": "sentence_units",
    "voice_separation": True,
    "loudness_match": True,
    "av_sync_audit": "off",
}


def _posted_plugin_config() -> dict:
    cfg = dict(OMNI_STANDARD_CFG)
    cfg.update(
        {
            "asr_post": "asr_normalize",
            "shot_decompose": True,
            "translate_algo": "shot_char_limit",
            "source_anchored": False,
        }
    )
    return cfg


def _post_create(client, *, plugin_config: dict | None = None, preset_id: str | None = None):
    form = {
        "source_language": "es",
        "target_lang": "de",
        "display_name": "v2 stable",
    }
    if plugin_config is not None:
        form["plugin_config"] = json.dumps(plugin_config)
    if preset_id is not None:
        form["preset_id"] = preset_id
    return client.post(
        "/api/omni-translate-v2/start",
        data={
            **form,
            "video": (io.BytesIO(b"fake-video-bytes"), "test.mp4"),
        },
        content_type="multipart/form-data",
    )


@pytest.fixture
def patched_v2_create(monkeypatch):
    monkeypatch.setattr(
        "web.upload_util.save_uploaded_video",
        lambda f, *a, **kw: ("/tmp/fake-v2.mp4", 16, "video/mp4"),
    )
    monkeypatch.setattr("web.upload_util.validate_video_extension", lambda fn: True)
    monkeypatch.setattr(
        "web.upload_util.build_source_object_info",
        lambda **kw: {"original_filename": kw.get("original_filename", "x")},
    )
    monkeypatch.setattr(
        "web.routes.omni_translate_v2._list_enabled_target_langs",
        lambda: ("de", "en", "fr"),
    )
    monkeypatch.setattr(
        "web.routes.omni_translate_v2._ensure_uploaded_video_thumbnail",
        lambda *a, **kw: "",
    )
    monkeypatch.setattr(
        "web.routes.omni_translate_v2._resolve_name_conflict",
        lambda user_id, name: name,
    )

    captured = {"create_args": None, "update_kwargs": None, "runner_args": None}

    class StoreStub:
        def create(self, task_id, video_path, task_dir, **kw):
            captured["create_args"] = (task_id, video_path, task_dir, kw)

        def update(self, task_id, **kw):
            captured["update_kwargs"] = kw

        def set_preview_file(self, task_id, key, path):
            pass

    class RunnerStub:
        def start(self, task_id, *, user_id):
            captured["runner_args"] = (task_id, user_id)

    monkeypatch.setattr("web.routes.omni_translate_v2.store", StoreStub())
    monkeypatch.setattr("web.routes.omni_translate_v2.omni_pipeline_runner", RunnerStub())
    monkeypatch.setattr(
        "appcore.omni_v2_config.system_settings.get_setting",
        lambda key: None,
    )
    return captured


def test_v2_create_ignores_inline_plugin_config_and_preset_id(
    authed_client_no_db,
    patched_v2_create,
    monkeypatch,
):
    def fail_get_preset(_preset_id):
        raise AssertionError("V2 creation must not read omni presets")

    monkeypatch.setattr("appcore.omni_preset_dao.get", fail_get_preset)

    resp = _post_create(
        authed_client_no_db,
        plugin_config=_posted_plugin_config(),
        preset_id="123",
    )

    assert resp.status_code == 201
    saved = patched_v2_create["update_kwargs"]
    assert saved["type"] == "omni_translate_v2"
    assert saved["plugin_config"] == OMNI_STANDARD_CFG


def test_v2_create_uses_hidden_av_sentence_mode(
    authed_client_no_db,
    patched_v2_create,
    monkeypatch,
):
    monkeypatch.setattr(
        "appcore.omni_v2_config.system_settings.get_setting",
        lambda key: "av_sentence",
    )

    resp = _post_create(authed_client_no_db)

    assert resp.status_code == 201
    assert patched_v2_create["update_kwargs"]["plugin_config"] == AV_SENTENCE_CFG


def test_v2_invalid_hidden_mode_falls_back_to_omni_standard(
    authed_client_no_db,
    patched_v2_create,
    monkeypatch,
):
    monkeypatch.setattr(
        "appcore.omni_v2_config.system_settings.get_setting",
        lambda key: "unknown-mode",
    )

    resp = _post_create(authed_client_no_db)

    assert resp.status_code == 201
    assert patched_v2_create["update_kwargs"]["plugin_config"] == OMNI_STANDARD_CFG


def test_v2_duplicate_uses_current_fixed_config_not_source_history(
    authed_client_no_db,
    monkeypatch,
    tmp_path,
):
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"fake-video")
    historical_cfg = _posted_plugin_config()

    monkeypatch.setattr("web.routes.omni_translate_v2.OUTPUT_DIR", str(tmp_path / "tasks"))
    monkeypatch.setattr(
        "web.routes.omni_translate_v2._query_viewable_project",
        lambda task_id, columns, include_deleted=False: {
            "id": task_id,
            "user_id": 1,
            "original_filename": "source.mp4",
            "display_name": "source task",
            "task_dir": str(tmp_path / "source-task"),
            "state_json": json.dumps(
                {
                    "video_path": str(source_video),
                    "target_lang": "de",
                    "source_language": "es",
                    "plugin_config": historical_cfg,
                }
            ),
        },
    )
    monkeypatch.setattr(
        "web.routes.omni_translate_v2._copy_source_video_for_duplicate",
        lambda **kw: (str(tmp_path / "copy.mp4"), 10, "video/mp4"),
    )
    monkeypatch.setattr(
        "web.upload_util.build_source_object_info",
        lambda **kw: {"original_filename": kw.get("original_filename", "x")},
    )
    monkeypatch.setattr(
        "web.routes.omni_translate_v2._ensure_uploaded_video_thumbnail",
        lambda *a, **kw: "",
    )
    monkeypatch.setattr(
        "web.routes.omni_translate_v2._resolve_name_conflict",
        lambda user_id, name: name,
    )
    monkeypatch.setattr(
        "appcore.omni_v2_config.system_settings.get_setting",
        lambda key: None,
    )

    captured = {"update_kwargs": None, "runner_args": None}

    class StoreStub:
        def get(self, task_id):
            return {
                "id": task_id,
                "video_path": str(source_video),
                "original_filename": "source.mp4",
                "display_name": "source task",
                "target_lang": "de",
                "source_language": "es",
                "plugin_config": historical_cfg,
            }

        def create(self, task_id, video_path, task_dir, **kw):
            pass

        def update(self, task_id, **kw):
            captured["update_kwargs"] = kw

        def set_preview_file(self, task_id, key, path):
            pass

    class RunnerStub:
        def start(self, task_id, *, user_id):
            captured["runner_args"] = (task_id, user_id)

    monkeypatch.setattr("web.routes.omni_translate_v2.store", StoreStub())
    monkeypatch.setattr("web.routes.omni_translate_v2.omni_pipeline_runner", RunnerStub())

    resp = authed_client_no_db.post("/api/omni-translate-v2/source-task/duplicate")

    assert resp.status_code == 201
    assert captured["update_kwargs"]["plugin_config"] == OMNI_STANDARD_CFG
    assert captured["update_kwargs"]["plugin_config"] != historical_cfg


def test_v2_step_resolution_ignores_global_omni_default_preset(monkeypatch):
    from web.routes import omni_translate_v2

    def fail_get_default():
        raise AssertionError("V2 step resolution must not read omni default preset")

    monkeypatch.setattr("appcore.omni_preset_dao.get_default", fail_get_default)
    monkeypatch.setattr(
        "appcore.omni_v2_config.system_settings.get_setting",
        lambda key: None,
    )

    steps = omni_translate_v2._omni_pipeline_steps_for_task("task-v2", {})

    assert "shot_decompose" not in steps
    assert steps == [
        "extract",
        "asr",
        "separate",
        "asr_clean",
        "voice_match",
        "alignment",
        "translate",
        "tts",
        "loudness_match",
        "subtitle",
        "compose",
        "export",
    ]


def test_v2_runtime_uses_fixed_config_when_task_missing_plugin_config(monkeypatch):
    from appcore import task_state
    from appcore.events import EventBus
    from appcore.runtime_omni_v2 import OmniV2TranslateRunner

    task_id = "v2-fixed-runtime"
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    task_state.create(task_id, "/tmp/source.mp4", "/tmp/task", user_id=1)
    monkeypatch.setattr(
        "appcore.omni_v2_config.system_settings.get_setting",
        lambda key: None,
    )

    runner = OmniV2TranslateRunner(EventBus(), user_id=1)

    assert runner._resolve_plugin_config(task_id) == OMNI_STANDARD_CFG
