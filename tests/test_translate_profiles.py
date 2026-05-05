"""Profile registry + runner-mount sanity tests (PR1)."""
from __future__ import annotations

import pytest

from appcore.events import EventBus
from appcore.translate_profiles import (
    AvSyncProfile,
    DefaultProfile,
    OmniProfile,
    TranslateProfile,
    available_profiles,
    get_profile,
    register_profile,
)


def test_default_omni_av_sync_registered():
    codes = {p.code for p in available_profiles()}
    assert {"default", "omni", "av_sync"} <= codes


def test_get_profile_returns_singleton_per_code():
    assert get_profile("default") is get_profile("default")
    assert isinstance(get_profile("default"), DefaultProfile)
    assert isinstance(get_profile("omni"), OmniProfile)
    assert isinstance(get_profile("av_sync"), AvSyncProfile)


def test_get_profile_unknown_raises():
    with pytest.raises(KeyError):
        get_profile("nope")


def test_register_duplicate_raises():
    class Dummy(TranslateProfile):
        code = "default"
        name = "x"
        def post_asr(self, runner, task_id):  # noqa: D401
            ...
        def translate(self, runner, task_id):
            ...
        def tts(self, runner, task_id, task_dir):
            ...
        def subtitle(self, runner, task_id, task_dir):
            ...
    with pytest.raises(ValueError):
        register_profile(Dummy())


def test_capability_flags_match_legacy_runner_behavior():
    default = get_profile("default")
    assert default.needs_separate is True
    assert default.needs_loudness_match is True
    assert default.post_asr_step_name == "asr_normalize"

    omni = get_profile("omni")
    assert omni.needs_separate is True
    assert omni.needs_loudness_match is True
    assert omni.post_asr_step_name == "asr_clean"

    av = get_profile("av_sync")
    assert av.needs_separate is False
    assert av.needs_loudness_match is False
    assert av.post_asr_step_name == "asr_normalize"


def test_multi_runner_has_default_profile():
    from appcore.runtime_multi import MultiTranslateRunner
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    assert runner.profile_code == "default"
    assert isinstance(runner.profile, DefaultProfile)


def test_omni_runner_has_omni_profile():
    from appcore.runtime_omni import OmniTranslateRunner
    runner = OmniTranslateRunner(bus=EventBus(), user_id=1)
    assert runner.profile_code == "omni"
    assert isinstance(runner.profile, OmniProfile)


def test_sentence_translate_runner_has_av_sync_profile():
    from appcore.runtime_sentence_translate import SentenceTranslateRunner
    runner = SentenceTranslateRunner(bus=EventBus(), user_id=1)
    assert runner.profile_code == "av_sync"
    assert isinstance(runner.profile, AvSyncProfile)


def test_base_pipeline_runner_defaults_to_default_profile():
    from appcore.runtime import PipelineRunner
    runner = PipelineRunner(bus=EventBus(), user_id=1)
    assert runner.profile_code == "default"
    assert isinstance(runner.profile, DefaultProfile)


# === Step-order regression: 3 个 runner 走统一 builder 后必须与历史一致 ===

EXPECTED_MULTI_STEPS = [
    "extract", "asr", "separate", "asr_normalize", "voice_match",
    "alignment", "translate", "tts", "loudness_match", "subtitle",
    "compose", "export",
]
EXPECTED_OMNI_STEPS = [
    "extract", "asr", "separate", "asr_clean", "voice_match",
    "alignment", "translate", "tts", "loudness_match", "subtitle",
    "compose", "export",
]
EXPECTED_AV_SYNC_STEPS = [
    "extract", "asr", "asr_normalize", "voice_match",
    "alignment", "translate", "tts", "subtitle",
    "compose", "export",
]


def _step_names(runner):
    steps = runner._get_pipeline_steps("t-fake", "/tmp/v.mp4", "/tmp")
    return [name for name, _fn in steps]


def test_multi_runner_step_order_unchanged():
    from appcore.runtime_multi import MultiTranslateRunner
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    assert _step_names(runner) == EXPECTED_MULTI_STEPS


def test_omni_runner_step_order_unchanged():
    from appcore.runtime_omni import OmniTranslateRunner
    runner = OmniTranslateRunner(bus=EventBus(), user_id=1)
    assert _step_names(runner) == EXPECTED_OMNI_STEPS


def test_sentence_translate_runner_step_order_unchanged():
    from appcore.runtime_sentence_translate import SentenceTranslateRunner
    runner = SentenceTranslateRunner(bus=EventBus(), user_id=1)
    assert _step_names(runner) == EXPECTED_AV_SYNC_STEPS


def test_analysis_step_inserted_when_flag_enabled():
    from appcore.runtime_multi import MultiTranslateRunner
    runner = MultiTranslateRunner(bus=EventBus(), user_id=1)
    runner.include_analysis_in_main_flow = True
    names = _step_names(runner)
    assert "analysis" in names
    # analysis 必须在 compose 后、export 前
    assert names.index("analysis") == names.index("compose") + 1
    assert names.index("analysis") + 1 == names.index("export")
