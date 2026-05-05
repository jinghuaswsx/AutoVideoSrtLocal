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
    assert default.needs_alignment is False
    assert default.needs_loudness_match is True

    omni = get_profile("omni")
    assert omni.needs_separate is True
    assert omni.needs_alignment is False
    assert omni.needs_loudness_match is True

    av = get_profile("av_sync")
    assert av.needs_separate is False
    assert av.needs_alignment is True
    assert av.needs_loudness_match is False


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
