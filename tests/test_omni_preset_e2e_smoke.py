"""End-to-end smoke for 4 baseline omni presets (Phase 7).

按 spec §7 验收：4 个等价 preset (multi-like / omni-current / av-sync-current /
lab-current) 各创建一个 task with plugin_config，验证：

1. ``_get_pipeline_steps`` 按 cfg 生成的 step 列表名字 / 顺序符合预期
2. 模拟 _run() 循环：把每步标 done，最终 task.steps 全部 done（即不会有 step
   被遗漏）
3. cross-preset 不变量：第一步 extract、最后一步 export；asr_post step name
   按 cfg 切换；voice_separation 关掉时 separate / loudness_match 都消失

不真跑 step body — Phase 2 ``test_runtime_omni_dispatch.py`` 已经覆盖
dispatch 行为，本 smoke 聚焦"4 个 preset 端到端可跑通"的最外层不变量。
真跑 step body 需要 mock 几十个 LLM/ASR/TTS pipeline 函数 + 处理 voice_match
waiting 暂停语义；那是手动验收 + 真生产任务的范畴（spec §7.1）。
"""
from __future__ import annotations

import uuid

import pytest

from appcore import task_state
from appcore.events import EventBus
from appcore.runtime_omni import OmniTranslateRunner


# ---------------------------------------------------------------------------
# Baseline preset cfgs (跟 db migration 2026_05_07_omni_translate_presets 一致)
# ---------------------------------------------------------------------------

CFG_MULTI_LIKE = {
    "asr_post": "asr_normalize", "shot_decompose": False,
    "translate_algo": "standard", "source_anchored": False,
    "tts_strategy": "five_round_rewrite", "subtitle": "asr_realign",
    "voice_separation": True, "loudness_match": True,
}
CFG_OMNI_CURRENT = {
    "asr_post": "asr_clean", "shot_decompose": False,
    "translate_algo": "standard", "source_anchored": True,
    "tts_strategy": "five_round_rewrite", "subtitle": "asr_realign",
    "voice_separation": True, "loudness_match": True,
}
CFG_AV_SYNC_CURRENT = {
    "asr_post": "asr_normalize", "shot_decompose": False,
    "translate_algo": "av_sentence", "source_anchored": False,
    "tts_strategy": "sentence_reconcile", "subtitle": "sentence_units",
    "voice_separation": True, "loudness_match": True,
}
CFG_LAB_CURRENT = {
    "asr_post": "asr_normalize", "shot_decompose": True,
    "translate_algo": "shot_char_limit", "source_anchored": False,
    "tts_strategy": "five_round_rewrite", "subtitle": "asr_realign",
    "voice_separation": True, "loudness_match": True,
}

EXPECTED_MULTI_LIKE_STEPS = [
    "extract", "asr", "separate", "asr_normalize",
    "voice_match", "alignment", "translate", "tts",
    "loudness_match", "subtitle", "compose", "export",
]
EXPECTED_OMNI_CURRENT_STEPS = [
    "extract", "asr", "separate", "asr_clean",
    "voice_match", "alignment", "translate", "tts",
    "loudness_match", "subtitle", "compose", "export",
]
EXPECTED_AV_SYNC_CURRENT_STEPS = [
    # av_sentence 跳过 alignment（spec §6.1）
    "extract", "asr", "separate", "asr_normalize",
    "voice_match", "translate", "tts",
    "loudness_match", "subtitle", "compose", "export",
]
EXPECTED_LAB_CURRENT_STEPS = [
    "extract", "asr", "separate", "shot_decompose", "asr_normalize",
    "voice_match", "alignment", "translate", "tts",
    "loudness_match", "subtitle", "compose", "export",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def in_memory_task_state(monkeypatch):
    """Mock task_state DB writes — 任务只活在内存里。"""
    monkeypatch.setattr(task_state, "_db_upsert", lambda *a, **kw: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *a, **kw: None)
    monkeypatch.setattr(task_state, "set_expires_at", lambda *a, **kw: None)
    if hasattr(task_state, "_TASKS"):
        task_state._TASKS.clear()
    return task_state


def _create_omni_task(plugin_config: dict) -> str:
    task_id = "smoke-" + uuid.uuid4().hex[:8]
    task_state.create(task_id, "/tmp/x/video.mp4", "/tmp/x", user_id=1)
    task_state.update(
        task_id,
        type="omni_translate",
        target_lang="de",
        source_language="es",
        user_specified_source_language=True,
        plugin_config=plugin_config,
        status="running",
    )
    return task_id


def _resolve_steps_for(plugin_config: dict) -> tuple[str, list[str]]:
    """创建任务 + 跑 _get_pipeline_steps 拿 step list。"""
    bus = EventBus()
    runner = OmniTranslateRunner(bus=bus, user_id=1)
    task_id = _create_omni_task(plugin_config)
    steps = runner._get_pipeline_steps(task_id, "/tmp/x/video.mp4", "/tmp/x")
    return task_id, [name for name, _fn in steps]


def _simulate_run(plugin_config: dict) -> tuple[str, list[str], dict]:
    """模拟 _run 循环：标每步 done，最终读 task。

    不真调 step fn（避开 LLM/IO/voice_match 暂停等复杂分支），但走真的
    _get_pipeline_steps（即真的从 task.plugin_config 解析 + 生成动态 step list），
    所以 dispatch 路径都被覆盖。
    """
    task_id, step_order = _resolve_steps_for(plugin_config)
    for name in step_order:
        # 模拟 step 完成 → 标 done
        existing = (task_state.get(task_id) or {}).get("steps") or {}
        existing[name] = "done"
        task_state.update(task_id, steps=existing)
    return task_id, step_order, task_state.get(task_id) or {}


# ---------------------------------------------------------------------------
# 4 baseline preset E2E smokes
# ---------------------------------------------------------------------------


def test_e2e_multi_like_preset_runs_to_export(in_memory_task_state):
    _, step_order, task = _simulate_run(CFG_MULTI_LIKE)
    assert step_order == EXPECTED_MULTI_LIKE_STEPS
    steps = task.get("steps") or {}
    for s in EXPECTED_MULTI_LIKE_STEPS:
        assert steps.get(s) == "done", f"step {s!r} not done in multi-like"


def test_e2e_omni_current_preset_runs_to_export(in_memory_task_state):
    _, step_order, task = _simulate_run(CFG_OMNI_CURRENT)
    assert step_order == EXPECTED_OMNI_CURRENT_STEPS
    steps = task.get("steps") or {}
    for s in EXPECTED_OMNI_CURRENT_STEPS:
        assert steps.get(s) == "done", f"step {s!r} not done in omni-current"


def test_e2e_av_sync_current_preset_runs_to_export(in_memory_task_state):
    _, step_order, task = _simulate_run(CFG_AV_SYNC_CURRENT)
    assert step_order == EXPECTED_AV_SYNC_CURRENT_STEPS
    assert "alignment" not in step_order  # av_sentence 跳过 alignment
    steps = task.get("steps") or {}
    for s in EXPECTED_AV_SYNC_CURRENT_STEPS:
        assert steps.get(s) == "done", f"step {s!r} not done in av-sync-current"


def test_e2e_lab_current_preset_runs_to_export(in_memory_task_state):
    _, step_order, task = _simulate_run(CFG_LAB_CURRENT)
    assert step_order == EXPECTED_LAB_CURRENT_STEPS
    assert "shot_decompose" in step_order  # lab-current 必含 shot_decompose
    steps = task.get("steps") or {}
    for s in EXPECTED_LAB_CURRENT_STEPS:
        assert steps.get(s) == "done", f"step {s!r} not done in lab-current"


# ---------------------------------------------------------------------------
# Cross-preset 不变量
# ---------------------------------------------------------------------------


def test_all_4_presets_have_extract_first_export_last(in_memory_task_state):
    for cfg in (CFG_MULTI_LIKE, CFG_OMNI_CURRENT, CFG_AV_SYNC_CURRENT, CFG_LAB_CURRENT):
        _, step_order = _resolve_steps_for(cfg)
        assert step_order[0] == "extract", f"first step != extract for cfg={cfg}"
        assert step_order[-1] == "export", f"last step != export for cfg={cfg}"


def test_post_asr_step_name_follows_cfg(in_memory_task_state):
    """asr_post=asr_clean → 'asr_clean'；asr_post=asr_normalize → 'asr_normalize'。"""
    for cfg in (CFG_MULTI_LIKE, CFG_AV_SYNC_CURRENT, CFG_LAB_CURRENT):
        _, step_order = _resolve_steps_for(cfg)
        assert "asr_normalize" in step_order
        assert "asr_clean" not in step_order
    _, step_order = _resolve_steps_for(CFG_OMNI_CURRENT)
    assert "asr_clean" in step_order
    assert "asr_normalize" not in step_order


def test_voice_separation_off_drops_separate_and_loudness(in_memory_task_state):
    """voice_separation=False 时 separate / loudness_match 都不应出现。"""
    cfg = dict(CFG_OMNI_CURRENT)
    cfg["voice_separation"] = False
    cfg["loudness_match"] = False  # 依赖
    _, step_order = _resolve_steps_for(cfg)
    assert "separate" not in step_order
    assert "loudness_match" not in step_order


def test_av_sentence_translate_omits_alignment(in_memory_task_state):
    cfg = dict(CFG_OMNI_CURRENT)
    cfg["translate_algo"] = "av_sentence"
    cfg["source_anchored"] = False  # av_sentence + source_anchored 不兼容
    cfg["tts_strategy"] = "sentence_reconcile"  # 配套
    cfg["subtitle"] = "sentence_units"  # 配套
    _, step_order = _resolve_steps_for(cfg)
    assert "alignment" not in step_order


def test_shot_decompose_inserts_after_separate_before_post_asr(in_memory_task_state):
    """shot_decompose 必须在 separate 后、asr_normalize 前（spec §6.1）。"""
    _, step_order = _resolve_steps_for(CFG_LAB_CURRENT)
    sep_idx = step_order.index("separate")
    sd_idx = step_order.index("shot_decompose")
    norm_idx = step_order.index("asr_normalize")
    assert sep_idx < sd_idx < norm_idx
