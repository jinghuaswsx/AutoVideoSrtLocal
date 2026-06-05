from __future__ import annotations

from appcore import task_state
from web import store
from web.routes import omni_translate


def _baseline_cfg() -> dict:
    return {
        "asr_post": "asr_clean",
        "shot_decompose": False,
        "translate_algo": "standard",
        "source_anchored": True,
        "tts_strategy": "five_round_rewrite",
        "subtitle": "asr_realign",
        "voice_separation": True,
        "loudness_match": True,
        "auto_voice_selection": True,
        "sentence_tts_loudness_calibration": True,
        "av_sync_audit": "off",
    }


def test_restart_step_order_uses_updated_plugin_config():
    task = {"plugin_config": _baseline_cfg()}
    order = omni_translate._restart_step_order_for_task("omni-restart", task, None)
    assert "asr_clean" in order
    assert "asr_normalize" not in order

    updated_cfg = _baseline_cfg()
    updated_cfg["asr_post"] = "asr_normalize"
    order = omni_translate._restart_step_order_for_task(
        "omni-restart",
        task,
        {"plugin_config": updated_cfg},
    )
    assert "asr_normalize" in order
    assert "asr_clean" not in order


def test_heals_completed_omni_status_with_inactive_asr_step_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(task_state, "_db_upsert", lambda *args, **kwargs: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *args, **kwargs: None)

    task_id = "omni-heal-complete"
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    task_dir = tmp_path / task_id
    task_dir.mkdir()

    cfg = _baseline_cfg()
    actual_steps = omni_translate._omni_pipeline_steps_for_task(
        task_id,
        {"plugin_config": cfg},
    )
    steps = {step: "done" for step in actual_steps}
    steps["asr_normalize"] = "pending"

    store.create(task_id, str(video_path), str(task_dir), user_id=1)
    store.update(
        task_id,
        type="omni_translate",
        plugin_config=cfg,
        status="interrupted",
        error="old restart interruption",
        steps=steps,
        step_messages={step: "" for step in steps},
        current_step=None,
    )

    healed = omni_translate._heal_completed_omni_task_status(task_id, store.get(task_id))

    assert healed["status"] == "done"
    assert healed["error"] == ""
    assert healed["steps"]["asr_normalize"] == "pending"
    assert store.get(task_id)["status"] == "done"
