"""Omni profile = experimental video-translate dispatcher (Phase 2).

omni 是合并实验大本营。本 profile 不做"一种固定行为"，而是按 task 的
``plugin_config`` 把每步 dispatch 到对应算法实现：

    post_asr  → runner._step_asr_clean / runner._step_asr_normalize
    translate → runner._step_translate_standard / _step_translate_shot_limit /
                AvSyncProfile.translate (av_sentence)
    tts       → tts_strategies.get_strategy(cfg["tts_strategy"]).run(...)
    subtitle  → runner._step_subtitle_asr_realign /
                AvSyncProfile.subtitle (sentence_units)

per-target rewrite tolerance / max attempts 仍走基类 hook（PR3 引入）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .base import TranslateProfile

if TYPE_CHECKING:
    from appcore.runtime import PipelineRunner


# 慢收敛目标语言（de/ja/fi）放宽 word_tolerance + 提高 max_rewrite_attempts，
# 让外层 5 轮循环不至于在边角语言上把 attempt 全部烧光。其他目标语言保持基线。
_OMNI_WORD_TOLERANCE_BY_TARGET: dict[str, float] = {
    "en": 0.10, "de": 0.15, "fr": 0.12, "es": 0.12, "it": 0.12, "pt": 0.12,
    "ja": 0.18, "nl": 0.12, "sv": 0.12, "fi": 0.15,
}

_OMNI_MAX_REWRITE_ATTEMPTS_BY_TARGET: dict[str, int] = {
    "en": 5, "de": 7, "fr": 5, "es": 5, "it": 5, "pt": 5,
    "ja": 7, "nl": 5, "sv": 5, "fi": 7,
}


def _resolve_cfg(runner) -> dict | None:
    """读 runner 上当前 task 的 plugin_config（dispatch 时用）。

    runner 在 _run() 调每个 step 之前已经把 task_id 给了 step callable；
    OmniProfile.X 都接收 task_id 作为入参。这里通过 runner._resolve_plugin_config
    走兜底逻辑。
    """
    return None  # 实际读取在 dispatch 时按 task_id 做


class OmniProfile(TranslateProfile):
    code = "omni"
    name = "全能实验（合并版）"
    # post_asr_step_name 对 omni 不再固定 — 由 _get_pipeline_steps 动态决定
    # 写的 step name（asr_clean / asr_normalize 二选一）。这里保留默认仅给
    # legacy 调用用（_build_steps_from_profile 已不被 omni 走）。
    post_asr_step_name = "asr_clean"

    needs_separate = True
    needs_loudness_match = True

    # ------------------------------------------------------------------
    # post_asr: cfg["asr_post"] 二选一
    # ------------------------------------------------------------------
    def post_asr(self, runner: "PipelineRunner", task_id: str) -> None:
        cfg = runner._resolve_plugin_config(task_id)
        if cfg["asr_post"] == "asr_clean":
            runner._step_asr_clean(task_id)
        else:
            runner._step_asr_normalize(task_id)

    # ------------------------------------------------------------------
    # translate: cfg["translate_algo"] 三选一
    # ------------------------------------------------------------------
    def translate(self, runner: "PipelineRunner", task_id: str) -> None:
        cfg = runner._resolve_plugin_config(task_id)
        algo = cfg["translate_algo"]
        if algo == "standard":
            runner._step_translate_standard(
                task_id, source_anchored=cfg["source_anchored"],
            )
        elif algo == "shot_char_limit":
            runner._step_translate_shot_limit(task_id)
        elif algo == "av_sentence":
            # 直接复用 AvSyncProfile.translate（不需要物理复制——本来就是为
            # omni 抽象设计的 profile，不是另一套 production runtime）。
            from .av_sync_profile import AvSyncProfile
            AvSyncProfile().translate(runner, task_id)
        else:
            raise ValueError(f"unknown translate_algo {algo!r}")

    # ------------------------------------------------------------------
    # tts: cfg["tts_strategy"] 二选一
    # ------------------------------------------------------------------
    def tts(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        cfg = runner._resolve_plugin_config(task_id)
        from appcore.tts_strategies import get_strategy
        strategy = get_strategy(cfg["tts_strategy"])
        strategy.run(runner, self, task_id, task_dir)

    # ------------------------------------------------------------------
    # subtitle: cfg["subtitle"] 二选一
    # ------------------------------------------------------------------
    def subtitle(self, runner: "PipelineRunner", task_id: str, task_dir: str) -> None:
        cfg = runner._resolve_plugin_config(task_id)
        if cfg["subtitle"] == "asr_realign":
            runner._step_subtitle_asr_realign(task_id, task_dir)
        elif cfg["subtitle"] == "sentence_units":
            from .av_sync_profile import AvSyncProfile
            AvSyncProfile().subtitle(runner, task_id, task_dir)
        else:
            raise ValueError(f"unknown subtitle {cfg['subtitle']!r}")

    # ------------------------------------------------------------------
    # per-target tunables（PR3，给 5-round rewrite loop 用）
    # ------------------------------------------------------------------
    def word_tolerance_for(self, target_lang: str) -> float:
        return _OMNI_WORD_TOLERANCE_BY_TARGET.get(
            target_lang, self.DEFAULT_WORD_TOLERANCE,
        )

    def max_rewrite_attempts_for(self, target_lang: str) -> int:
        return _OMNI_MAX_REWRITE_ATTEMPTS_BY_TARGET.get(
            target_lang, self.DEFAULT_MAX_REWRITE_ATTEMPTS,
        )
