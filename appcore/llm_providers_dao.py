"""Provider 前端视图 DAO。

封装"供应商卡片"视角所需的全部读写：
- 用户级 LLM / 音频类 provider：复用 api_keys 表（service 作为 provider_code）
- 全局级 provider（仅管理员可改）：存 system_settings

注意 provider_code 与 adapter provider_code 的命名对应：
    UI 层 gemini       == api_keys.service="gemini"       (AI Studio)
    UI 层 gemini_cloud == api_keys.service="gemini_cloud" (Vertex Express, 新增)
    UI 层 doubao_llm   == api_keys.service="doubao_llm"   (豆包 ARK)

LLM adapter 层的 provider_code（openrouter / doubao / gemini_aistudio / gemini_vertex）
通过 bindings 表与 UI 层 service 解耦，见 llm_use_cases.py。
"""
from __future__ import annotations

from appcore.api_keys import get_all, set_key
from appcore.settings import get_setting, set_setting

# (service_code, 显示名, [(field_key, field_label, input_type)])
USER_LEVEL_PROVIDERS: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    ("openrouter", "OpenRouter", [
        ("key_value", "API Key", "password"),
        ("base_url", "Base URL", "text"),
    ]),
    ("doubao_llm", "豆包 ARK", [
        ("key_value", "API Key", "password"),
        ("base_url", "Base URL", "text"),
    ]),
    ("gemini", "Google Gemini (AI Studio)", [
        ("key_value", "API Key", "password"),
    ]),
    ("gemini_cloud", "Google Gemini (Vertex Express)", [
        ("key_value", "API Key", "password"),
    ]),
    ("elevenlabs", "ElevenLabs", [
        ("key_value", "API Key", "password"),
    ]),
]

GLOBAL_PROVIDERS: list[tuple[str, str, list[tuple[str, str, str]]]] = [
    ("volc_asr", "火山引擎 ASR", [
        ("api_key", "API Key", "password"),
    ]),
]

_VOLC_ASR_KEY = "provider.volc_asr.api_key"


def load_user_providers(user_id: int) -> dict[str, dict[str, str]]:
    """返回 {provider_code: {field_key: value}} 供模板展示。"""
    raw = get_all(user_id)  # {service: {key_value, extra}}
    out: dict[str, dict[str, str]] = {}
    for code, _, fields in USER_LEVEL_PROVIDERS:
        entry = raw.get(code) or {}
        field_values: dict[str, str] = {"key_value": entry.get("key_value", "")}
        extra = entry.get("extra") or {}
        for fname, _, _ in fields:
            if fname != "key_value":
                field_values[fname] = extra.get(fname, "")
        out[code] = field_values
    return out


def save_user_provider(user_id: int, code: str, fields: dict[str, str]) -> None:
    matched = next((f for c, _, f in USER_LEVEL_PROVIDERS if c == code), None)
    if matched is None:
        raise ValueError(f"unknown provider: {code}")
    key_value = (fields.get("key_value") or "").strip()
    extra: dict[str, str] = {}
    for fname, _, _ in matched:
        if fname == "key_value":
            continue
        v = (fields.get(fname) or "").strip()
        if v:
            extra[fname] = v
    set_key(user_id, code, key_value, extra or None)


def load_global_providers() -> dict[str, dict[str, str]]:
    return {
        "volc_asr": {"api_key": get_setting(_VOLC_ASR_KEY) or ""},
    }


def save_global_provider(code: str, fields: dict[str, str]) -> None:
    if code == "volc_asr":
        set_setting(_VOLC_ASR_KEY, (fields.get("api_key") or "").strip())
    else:
        raise ValueError(f"unknown global provider: {code}")
