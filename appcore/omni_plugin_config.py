"""Omni-translate experimental plugin config validator.

每个 task / preset 的能力点配置都是一个 JSON 对象（schema 见
``docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`` §4.4）。
本模块负责：

- ``validate_plugin_config(cfg)`` —— 校验 + silent fix（缺字段补默认 +
  互斥关系自动收正），非法值抛 ``ValueError``（中文消息）
- ``DEFAULT_PLUGIN_CONFIG`` —— 全部走基线 omni 当前行为的默认配置
- ``CAPABILITY_GROUPS`` —— 给 UI 渲染用的元数据（分组 + 选项 + 中文说明）
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# 能力点元数据（UI 渲染用）
# ---------------------------------------------------------------------------

# 每组 4 元组结构: (字段 key, 选项 value, 中文说明, 选择方式: "radio"|"checkbox")
# 顺序即 UI 上的展示顺序。
CAPABILITY_GROUPS: list[dict[str, Any]] = [
    {
        "id": "asr_post",
        "label": "① ASR 后处理",
        "kind": "radio",
        "options": [
            {"value": "asr_clean",     "label": "asr_clean",     "desc": "按源语言原样清洗文本（去口误、补标点），不翻译"},
            {"value": "asr_normalize", "label": "asr_normalize", "desc": "ASR 文本统一翻成英文，给下游翻译走同一英文基线"},
        ],
        "default": "asr_clean",
    },
    {
        "id": "shot_decompose",
        "label": "② 镜头分镜",
        "kind": "checkbox",
        "options": [
            {"value": True,  "label": "shot_decompose", "desc": "用 Gemini 视觉分析视频，切出『一个镜头一段话』的镜头列表 + 时间轴"},
        ],
        "default": False,
    },
    {
        "id": "translate_algo",
        "label": "③ 翻译算法",
        "kind": "radio",
        "options": [
            {"value": "standard",        "label": "standard",        "desc": "整段一次性翻译，靠 prompt 控制风格和长度"},
            {"value": "shot_char_limit", "label": "shot_char_limit", "desc": "每镜头独立翻译，按『镜头时长 × cps』算字符上限，让初译就贴合时长（cps 基准 voice_match 时自动初始化）"},
            {"value": "av_sentence",     "label": "av_sentence",     "desc": "句级翻译，先用 Gemini 给每句打『画面笔记』再逐句翻，贴合画面（shot_notes 内置）"},
        ],
        "default": "standard",
    },
    {
        "id": "source_anchored",
        "label": "④ 翻译 prompt 增强",
        "kind": "checkbox",
        "options": [
            {"value": True, "label": "source_anchored", "desc": "system prompt 加 INPUT NOTICE，告诉 LLM 输入是 ASR 文本不要捏造原视频之外的内容（仅对 standard / shot_char_limit 生效）"},
        ],
        "default": True,
    },
    {
        "id": "tts_strategy",
        "label": "⑤ TTS 收敛策略",
        "kind": "radio",
        "options": [
            {"value": "five_round_rewrite",   "label": "five_round_rewrite",   "desc": "5 轮 rewrite + 变速短路：每轮按音频实际时长反向重译，直到落进时长窗口"},
            {"value": "sentence_reconcile",   "label": "sentence_reconcile",   "desc": "句级 reconcile：每句独立 TTS 测时长，逐句调速率或重译，不做整段 rewrite"},
        ],
        "default": "five_round_rewrite",
    },
    {
        "id": "subtitle",
        "label": "⑥ 字幕生成",
        "kind": "radio",
        "options": [
            {"value": "asr_realign",     "label": "asr_realign",     "desc": "TTS 后再跑一次 ASR 拿词级时间戳，按词重新对齐字幕，最准"},
            {"value": "sentence_units",  "label": "sentence_units",  "desc": "直接用句级 TTS 的时间轴出 SRT，跳过二次 ASR（依赖 ⑤ 选 sentence_reconcile）"},
        ],
        "default": "asr_realign",
    },
    {
        "id": "voice_separation",
        "label": "⑦ 人声分离",
        "kind": "checkbox",
        "options": [
            {"value": True, "label": "voice_separation", "desc": "用 audio-separator 分离人声和背景音，配音后跟原 BGM 重新混音"},
        ],
        "default": True,
    },
    {
        "id": "loudness_match",
        "label": "⑧ 响度匹配",
        "kind": "checkbox",
        "options": [
            {"value": True, "label": "loudness_match", "desc": "配音整体响度按 EBU R128 匹配原视频，避免音量突兀（依赖 ⑦）"},
        ],
        "default": True,
    },
]

# 4 个 radio 字段的合法取值集合
_RADIO_VALID_VALUES: dict[str, set[str]] = {
    "asr_post":       {"asr_clean", "asr_normalize"},
    "translate_algo": {"standard", "shot_char_limit", "av_sentence"},
    "tts_strategy":   {"five_round_rewrite", "sentence_reconcile"},
    "subtitle":       {"asr_realign", "sentence_units"},
}

_BOOL_FIELDS = ("shot_decompose", "source_anchored", "voice_separation", "loudness_match")

# 全部走基线 omni 当前行为
DEFAULT_PLUGIN_CONFIG: dict[str, Any] = {grp["id"]: grp["default"] for grp in CAPABILITY_GROUPS}


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _coerce_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int,)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in ("true", "false", "1", "0"):
        return value.lower() in ("true", "1")
    raise ValueError(f"字段 {field!r} 必须是布尔值，收到 {value!r}")


def validate_plugin_config(cfg: dict | None) -> dict:
    """校验 + silent fix；返回 fix 后的副本（不改入参）。

    校验失败抛 ``ValueError``（中文消息）。校验项：

    1. 4 个 radio 字段（``asr_post`` / ``translate_algo`` / ``tts_strategy`` /
       ``subtitle``）必须存在且取值合法
    2. 4 个 boolean 字段（``shot_decompose`` / ``source_anchored`` /
       ``voice_separation`` / ``loudness_match``）缺失时用默认值补齐
    3. 依赖关系（任一不满足直接拒绝）：
        * ``translate_algo == "shot_char_limit"`` 必须 ``shot_decompose == True``
        * ``subtitle == "sentence_units"`` 必须 ``tts_strategy == "sentence_reconcile"``
        * ``loudness_match == True`` 必须 ``voice_separation == True``
    4. silent fix:
        * ``translate_algo == "av_sentence"`` 时 ``source_anchored`` 强制改成 ``False``
          （av_sentence 走完全不同的 prompt 体系，INPUT NOTICE 不适用）
    """
    if cfg is None:
        cfg = {}
    if not isinstance(cfg, dict):
        raise ValueError(f"plugin_config 必须是 JSON 对象，收到 {type(cfg).__name__}")

    out: dict[str, Any] = {}

    # 1. radio 字段
    for field, allowed in _RADIO_VALID_VALUES.items():
        if field not in cfg:
            # 用默认值补齐
            out[field] = DEFAULT_PLUGIN_CONFIG[field]
            continue
        value = cfg[field]
        if not isinstance(value, str) or value not in allowed:
            raise ValueError(
                f"字段 {field!r} 取值不合法，允许 {sorted(allowed)}，收到 {value!r}"
            )
        out[field] = value

    # 2. boolean 字段
    for field in _BOOL_FIELDS:
        if field not in cfg:
            out[field] = DEFAULT_PLUGIN_CONFIG[field]
            continue
        out[field] = _coerce_bool(cfg[field], field)

    # 3. 依赖关系
    if out["translate_algo"] == "shot_char_limit" and not out["shot_decompose"]:
        raise ValueError(
            "translate_algo=shot_char_limit 必须同时启用 shot_decompose（按镜头字符上限翻译需要先有镜头列表）"
        )
    if out["subtitle"] == "sentence_units" and out["tts_strategy"] != "sentence_reconcile":
        raise ValueError(
            "subtitle=sentence_units 必须配合 tts_strategy=sentence_reconcile（句级字幕需要句级 TTS 时间轴）"
        )
    if out["loudness_match"] and not out["voice_separation"]:
        raise ValueError(
            "loudness_match 启用时必须同时启用 voice_separation（响度匹配只在分离后的纯人声轨上工作）"
        )

    # 4. silent fix
    if out["translate_algo"] == "av_sentence" and out["source_anchored"]:
        out["source_anchored"] = False

    return out
