from __future__ import annotations

import logging
from typing import Any

from appcore.omni_plugin_config import DEFAULT_PLUGIN_CONFIG, validate_plugin_config

log = logging.getLogger(__name__)


def _baseline_configs() -> dict[str, dict[str, Any]]:
    omni_current = validate_plugin_config(dict(DEFAULT_PLUGIN_CONFIG))
    multi_like = validate_plugin_config({
        **omni_current,
        "asr_post": "asr_normalize",
        "source_anchored": False,
    })
    av_sync_current = validate_plugin_config({
        **omni_current,
        "asr_post": "asr_normalize",
        "translate_algo": "av_sentence",
        "source_anchored": False,
        "tts_strategy": "sentence_reconcile",
        "subtitle": "sentence_units",
    })
    lab_current = validate_plugin_config({
        **omni_current,
        "asr_post": "asr_normalize",
        "shot_decompose": True,
        "translate_algo": "shot_char_limit",
        "source_anchored": False,
    })
    return {
        "multi-like": multi_like,
        "omni-current": omni_current,
        "av-sync-current": av_sync_current,
        "lab-current": lab_current,
    }


def _match_baseline_name(cfg: dict[str, Any]) -> str | None:
    for name, baseline in _baseline_configs().items():
        if cfg == baseline:
            return name
    return None


def _resolve_default_preset() -> dict | None:
    try:
        from appcore import omni_preset_dao

        return omni_preset_dao.get_default()
    except Exception:
        log.warning("[omni-preset] default preset display fallback failed", exc_info=True)
        return None


def _summary_items(cfg: dict[str, Any]) -> list[str]:
    asr_labels = {
        "asr_clean": "ASR 原样清洗",
        "asr_normalize": "ASR 英文标准化",
    }
    translate_labels = {
        "standard": "标准翻译",
        "shot_char_limit": "镜头限字翻译",
        "av_sentence": "句级音画翻译",
    }
    tts_labels = {
        "five_round_rewrite": "五轮重写",
        "sentence_reconcile": "句级 TTS 协调",
    }
    subtitle_labels = {
        "asr_realign": "ASR 对齐字幕",
        "sentence_units": "句级时间轴字幕",
    }
    audit_labels = {
        "off": "审计关闭",
        "report_only": "只生成审计报告",
        "safe_auto": "安全自动审计",
    }
    return [
        asr_labels.get(cfg["asr_post"], cfg["asr_post"]),
        "镜头分镜开启" if cfg["shot_decompose"] else "镜头分镜关闭",
        translate_labels.get(cfg["translate_algo"], cfg["translate_algo"]),
        "Source anchored 开启" if cfg["source_anchored"] else "Source anchored 关闭",
        tts_labels.get(cfg["tts_strategy"], cfg["tts_strategy"]),
        subtitle_labels.get(cfg["subtitle"], cfg["subtitle"]),
        "人声分离开启" if cfg["voice_separation"] else "人声分离关闭",
        "响度匹配开启" if cfg["loudness_match"] else "响度匹配关闭",
        audit_labels.get(cfg["av_sync_audit"], cfg["av_sync_audit"]),
    ]


def build_plugin_config_annotation(task_id: str, task: dict | None) -> dict[str, Any]:
    """Build a compact, read-only Omni plugin_config annotation for detail pages."""
    raw_cfg = (task or {}).get("plugin_config")
    source = "snapshot"
    source_label = "任务快照"

    if not raw_cfg:
        source = "default"
        source_label = "默认配置"
        default_preset = _resolve_default_preset()
        raw_cfg = (default_preset or {}).get("plugin_config") or DEFAULT_PLUGIN_CONFIG

    try:
        cfg = validate_plugin_config(raw_cfg)
    except ValueError:
        log.warning(
            "[omni-preset] invalid plugin_config for annotation task_id=%s",
            task_id,
            exc_info=True,
        )
        source = "fallback"
        source_label = "默认配置"
        cfg = validate_plugin_config(dict(DEFAULT_PLUGIN_CONFIG))

    name = _match_baseline_name(cfg) or ("默认配置" if source != "snapshot" else "自定义配置")
    items = _summary_items(cfg)
    return {
        "name": name,
        "source": source,
        "source_label": source_label,
        "summary": " / ".join(items),
        "items": items,
    }
