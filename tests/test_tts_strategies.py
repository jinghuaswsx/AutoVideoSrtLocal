"""TtsConvergenceStrategy registry + profile wiring tests (PR6)."""
from __future__ import annotations

import pytest

from appcore.tts_strategies import (
    FiveRoundRewriteLoopStrategy,
    SentenceReconcileStrategy,
    TtsConvergenceStrategy,
    available_strategies,
    get_strategy,
    register_strategy,
)


def test_default_strategies_registered():
    codes = {s.code for s in available_strategies()}
    assert {"five_round_rewrite", "sentence_reconcile"} <= codes


def test_get_strategy_returns_singleton():
    assert get_strategy("five_round_rewrite") is get_strategy("five_round_rewrite")
    assert isinstance(get_strategy("five_round_rewrite"), FiveRoundRewriteLoopStrategy)
    assert isinstance(get_strategy("sentence_reconcile"), SentenceReconcileStrategy)


def test_get_strategy_unknown_raises():
    with pytest.raises(KeyError):
        get_strategy("nope")


def test_register_duplicate_raises():
    class Dummy(TtsConvergenceStrategy):
        code = "five_round_rewrite"
        name = "dummy"

        def run(self, runner, profile, task_id, task_dir):
            ...

    with pytest.raises(ValueError):
        register_strategy(Dummy())


# === Profile.get_tts_strategy wiring ===


def test_default_profile_uses_five_round_rewrite():
    from appcore.translate_profiles import get_profile
    p = get_profile("default")
    assert p.tts_strategy_code == "five_round_rewrite"
    assert isinstance(p.get_tts_strategy(), FiveRoundRewriteLoopStrategy)


def test_omni_profile_uses_five_round_rewrite():
    from appcore.translate_profiles import get_profile
    p = get_profile("omni")
    assert p.tts_strategy_code == "five_round_rewrite"
    assert isinstance(p.get_tts_strategy(), FiveRoundRewriteLoopStrategy)


def test_av_sync_profile_uses_sentence_reconcile():
    from appcore.translate_profiles import get_profile
    p = get_profile("av_sync")
    assert p.tts_strategy_code == "sentence_reconcile"
    assert isinstance(p.get_tts_strategy(), SentenceReconcileStrategy)


def test_five_round_rewrite_dispatches_to_runner_default_loop():
    """FiveRoundRewriteLoopStrategy.run 应 dispatch 到 ``runner._run_default_tts_loop``。"""
    from unittest.mock import MagicMock

    runner = MagicMock()
    profile = MagicMock()
    strat = FiveRoundRewriteLoopStrategy()
    strat.run(runner, profile, "t-fake", "/tmp/x")

    runner._run_default_tts_loop.assert_called_once_with("t-fake", "/tmp/x")


def test_profile_can_swap_strategy_via_class_attr():
    """新 profile 想换收敛策略只需覆盖 ``tts_strategy_code`` 类属性。"""
    from appcore.translate_profiles.base import TranslateProfile

    class StubStrategy(TtsConvergenceStrategy):
        code = "stub_strategy_for_swap_test"
        name = "stub strategy"

        def __init__(self):
            self.calls = []

        def run(self, runner, profile, task_id, task_dir):
            self.calls.append((task_id, task_dir))

    stub = StubStrategy()
    register_strategy(stub)

    class StubProfile(TranslateProfile):
        code = "stub_strategy_swap_profile"
        name = "stub"
        tts_strategy_code = "stub_strategy_for_swap_test"

        def post_asr(self, runner, task_id): ...
        def translate(self, runner, task_id): ...
        def tts(self, runner, task_id, task_dir):
            self.get_tts_strategy().run(runner, self, task_id, task_dir)
        def subtitle(self, runner, task_id, task_dir): ...

    p = StubProfile()
    p.tts(None, "t-stub", "/tmp/stub")
    assert stub.calls == [("t-stub", "/tmp/stub")]
