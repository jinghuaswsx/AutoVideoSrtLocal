"""Fixed flow configuration for Omni Translate V2.

V2 is intentionally not preset-driven. It stores one validated config on each
task so resume/detail step ordering stays deterministic while original omni
keeps its full preset system.
"""
from __future__ import annotations

import logging

from appcore import settings as system_settings
from appcore.omni_plugin_config import validate_plugin_config

log = logging.getLogger(__name__)

SETTING_PIPELINE_MODE = "omni_v2.pipeline_mode"
MODE_OMNI_STANDARD = "omni_standard"
MODE_AV_SENTENCE = "av_sentence"
ALLOWED_PIPELINE_MODES = {MODE_OMNI_STANDARD, MODE_AV_SENTENCE}

OMNI_STANDARD_PLUGIN_CONFIG = validate_plugin_config(
    {
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
)

AV_SENTENCE_PLUGIN_CONFIG = validate_plugin_config(
    {
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
)


def normalize_pipeline_mode(raw: str | None) -> str:
    mode = str(raw or "").strip()
    if mode in ALLOWED_PIPELINE_MODES:
        return mode
    return MODE_OMNI_STANDARD


def get_pipeline_mode() -> str:
    try:
        return normalize_pipeline_mode(system_settings.get_setting(SETTING_PIPELINE_MODE))
    except Exception:
        log.warning("[omni_v2] failed to read pipeline mode; using omni_standard", exc_info=True)
        return MODE_OMNI_STANDARD


def fixed_plugin_config_for_mode(mode: str | None) -> dict:
    normalized = normalize_pipeline_mode(mode)
    if normalized == MODE_AV_SENTENCE:
        return dict(AV_SENTENCE_PLUGIN_CONFIG)
    return dict(OMNI_STANDARD_PLUGIN_CONFIG)


def current_fixed_plugin_config() -> dict:
    return fixed_plugin_config_for_mode(get_pipeline_mode())


def stored_or_fixed_plugin_config(task: dict | None) -> dict:
    task = task or {}
    cfg = task.get("plugin_config")
    if cfg:
        try:
            return validate_plugin_config(cfg)
        except ValueError:
            log.warning(
                "[omni_v2] invalid stored plugin_config task=%s; using fixed config",
                task.get("id") or "?",
                exc_info=True,
            )
    return current_fixed_plugin_config()
