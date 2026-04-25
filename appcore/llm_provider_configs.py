"""LLM / API 供应商配置 DAO。

本模块是所有模型与 API 供应商凭据、base_url、默认模型、extra_config 的
**唯一运行时来源**。上层业务必须通过这里读取：

  * 禁止直接 import config 里的供应商常量
  * 禁止读取进程环境变量
  * 禁止从本地配置文件回落凭据

设计要点：
  * 每个业务入口一条独立 provider_code。即使当前真实 Key 相同，也分开存储，
    方便后续单独换 key 不影响其他功能。
  * 缺配置时抛 ProviderConfigError，信息里明确带上 provider_code 与字段，
    指引运维在 /settings -> 服务商接入 页填补。
  * 不做进程级缓存。每次调用都命中 DB（连接池单行主键查询开销可忽略），
    保证 admin 在 /settings 保存后新请求立即生效。
  * 现有 llm_use_case_bindings 表继续负责 "use_case -> provider_code/model"
    的路由；本模块只负责 "给某个 provider_code 提供凭据与默认连接信息"。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from appcore.db import execute, query, query_one


GROUP_TEXT_LLM = "text_llm"
GROUP_IMAGE = "image"
GROUP_ASR = "asr"
GROUP_VIDEO = "video"
GROUP_TTS = "tts"
GROUP_AUX = "aux"


# provider_code -> (display_name, group_code)
# 与 db/migrations/2026_04_25_llm_provider_configs.sql 的 INSERT IGNORE 列表对齐。
# 这里同时作为 Python 侧"合法 provider_code 白名单"：save_provider_config 只接受
# 这里声明过的 code，避免前端随意写入未定义 provider_code。
_KNOWN_PROVIDERS: dict[str, tuple[str, str]] = {
    "openrouter_text":       ("OpenRouter 文本 / 本土化 LLM",     GROUP_TEXT_LLM),
    "openrouter_image":      ("OpenRouter 图片模型",                GROUP_IMAGE),
    "gemini_aistudio_text":  ("Google Gemini · AI Studio（文本）",  GROUP_TEXT_LLM),
    "gemini_aistudio_image": ("Google Gemini · AI Studio（图片）",  GROUP_IMAGE),
    "gemini_cloud_text":     ("Google Cloud / Vertex AI（文本）",   GROUP_TEXT_LLM),
    "gemini_cloud_image":    ("Google Cloud / Vertex AI（图片）",   GROUP_IMAGE),
    "doubao_llm":            ("豆包 ARK 文本模型",                  GROUP_TEXT_LLM),
    "doubao_seedream":       ("豆包 Seedream 图片生成",             GROUP_IMAGE),
    "doubao_asr":            ("火山 ASR 语音识别",                   GROUP_ASR),
    "seedance_video":        ("Seedance 视频生成",                  GROUP_VIDEO),
    "apimart_image":         ("APIMART / GPT Image 2",              GROUP_IMAGE),
    "elevenlabs_tts":        ("ElevenLabs 配音",                    GROUP_TTS),
    "subtitle_removal":      ("字幕移除服务",                        GROUP_AUX),
    "openapi_materials":     ("素材 OpenAPI",                        GROUP_AUX),
}


# adapter provider_code -> (credential_provider_code_for_text, credential_provider_code_for_image)
# adapter provider_code 是 llm_use_case_bindings 里保存的 provider 枚举；
# llm_client / 业务调用传 media_kind="image" 时路由到 *_image 凭据行。
_ADAPTER_CREDENTIAL_MAP: dict[str, tuple[str, str | None]] = {
    "openrouter":      ("openrouter_text",      "openrouter_image"),
    "doubao":          ("doubao_llm",           None),
    "gemini_aistudio": ("gemini_aistudio_text", "gemini_aistudio_image"),
    "gemini_vertex":   ("gemini_cloud_text",    "gemini_cloud_image"),
    "doubao_asr":      ("doubao_asr",           None),
    "doubao_seedream": ("doubao_seedream",      None),
    "apimart":         ("apimart_image",        None),
    "seedance":        ("seedance_video",       None),
    "elevenlabs":      ("elevenlabs_tts",       None),
}


class ProviderConfigError(RuntimeError):
    """未声明的 provider_code、缺凭据，或类似的配置错误。"""


@dataclass(frozen=True)
class LlmProviderConfig:
    provider_code: str
    display_name: str
    group_code: str
    api_key: str | None = None
    base_url: str | None = None
    model_id: str | None = None
    extra_config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    updated_by: int | None = None

    def require_api_key(self) -> str:
        if not self.api_key:
            raise ProviderConfigError(
                f"缺少供应商配置 {self.provider_code}.api_key，"
                f"请在 /settings 的「服务商接入」页填写（{self.display_name}）。"
            )
        return self.api_key

    def require_base_url(self, default: str | None = None) -> str:
        url = (self.base_url or "").strip() or (default or "").strip()
        if not url:
            raise ProviderConfigError(
                f"缺少供应商配置 {self.provider_code}.base_url，"
                f"请在 /settings 的「服务商接入」页填写（{self.display_name}）。"
            )
        return url

    def resolved_model_id(self, default: str | None = None) -> str | None:
        value = (self.model_id or "").strip()
        if value:
            return value
        return (default or "").strip() or None


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _nullable_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _row_to_config(row: dict) -> LlmProviderConfig:
    extra_raw = row.get("extra_config")
    if isinstance(extra_raw, str) and extra_raw:
        try:
            extra = json.loads(extra_raw)
            if not isinstance(extra, dict):
                extra = {}
        except json.JSONDecodeError:
            extra = {}
    elif isinstance(extra_raw, dict):
        extra = dict(extra_raw)
    else:
        extra = {}

    return LlmProviderConfig(
        provider_code=str(row["provider_code"]),
        display_name=str(row.get("display_name") or row["provider_code"]),
        group_code=str(row.get("group_code") or "llm"),
        api_key=_nullable_str(row.get("api_key")),
        base_url=_nullable_str(row.get("base_url")),
        model_id=_nullable_str(row.get("model_id")),
        extra_config=extra,
        enabled=bool(int(row.get("enabled") or 0)),
        updated_by=(int(row["updated_by"])
                    if row.get("updated_by") is not None else None),
    )


def _coalesce_field(fields: dict, key: str, existing: Any) -> Any:
    if key not in fields:
        return existing
    value = fields[key]
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------

def list_provider_configs() -> list[LlmProviderConfig]:
    """返回全部 provider 行，按 group_code, provider_code 排序。"""
    rows = query(
        "SELECT provider_code, display_name, group_code, api_key, base_url, "
        "       model_id, extra_config, enabled, updated_by "
        "FROM llm_provider_configs "
        "ORDER BY group_code, provider_code"
    )
    return [_row_to_config(r) for r in rows]


def get_provider_config(provider_code: str) -> LlmProviderConfig | None:
    """返回单个 provider 行；不存在返回 None，不抛错。"""
    row = query_one(
        "SELECT provider_code, display_name, group_code, api_key, base_url, "
        "       model_id, extra_config, enabled, updated_by "
        "FROM llm_provider_configs WHERE provider_code = %s",
        (provider_code,),
    )
    return _row_to_config(row) if row else None


def require_provider_config(provider_code: str) -> LlmProviderConfig:
    """DB 里必须有这行，否则抛 ProviderConfigError。"""
    cfg = get_provider_config(provider_code)
    if cfg is None:
        raise ProviderConfigError(
            f"未知 provider_code={provider_code}；"
            "请先在 db/migrations/ 中注册或检查 migration 是否应用。"
        )
    return cfg


def require_provider_api_key(provider_code: str) -> str:
    """快捷方式：拿到非空 api_key；缺失抛错时错误信息包含 provider_code。"""
    return require_provider_config(provider_code).require_api_key()


def save_provider_config(
    provider_code: str,
    fields: dict[str, Any],
    updated_by: int | None,
) -> None:
    """部分字段更新。

    支持的 fields 字段：
      api_key / base_url / model_id        → 字符串，空白串自动规整为 NULL
      extra_config                         → dict 或 str（JSON）
      enabled                              → bool
      display_name / group_code            → 字符串，覆盖默认
    未传的字段保留 DB 当前值；首次写入时 display_name / group_code 回到
    _KNOWN_PROVIDERS 注册值。

    拒绝保存未声明的 provider_code，避免前端塞入野 provider。
    """
    if provider_code not in _KNOWN_PROVIDERS:
        raise ProviderConfigError(
            f"拒绝保存未声明的 provider_code={provider_code}；"
            "请先在 appcore.llm_provider_configs._KNOWN_PROVIDERS "
            "与对应 migration 里注册。"
        )

    existing = get_provider_config(provider_code)
    default_display, default_group = _KNOWN_PROVIDERS[provider_code]

    display_name = fields.get(
        "display_name",
        existing.display_name if existing else default_display,
    )
    group_code = fields.get(
        "group_code",
        existing.group_code if existing else default_group,
    )
    api_key = _coalesce_field(
        fields, "api_key",
        existing.api_key if existing else None,
    )
    base_url = _coalesce_field(
        fields, "base_url",
        existing.base_url if existing else None,
    )
    model_id = _coalesce_field(
        fields, "model_id",
        existing.model_id if existing else None,
    )
    enabled = int(bool(fields.get(
        "enabled", existing.enabled if existing else True,
    )))

    if "extra_config" in fields:
        extra_value = fields["extra_config"]
        if extra_value is None:
            extra_json: str | None = None
        elif isinstance(extra_value, dict):
            extra_json = json.dumps(extra_value, ensure_ascii=False) if extra_value else None
        elif isinstance(extra_value, str):
            stripped = extra_value.strip()
            extra_json = stripped or None
        else:
            extra_json = json.dumps(extra_value, ensure_ascii=False)
    else:
        if existing and existing.extra_config:
            extra_json = json.dumps(existing.extra_config, ensure_ascii=False)
        else:
            extra_json = None

    execute(
        "INSERT INTO llm_provider_configs "
        "  (provider_code, display_name, group_code, api_key, base_url, "
        "   model_id, extra_config, enabled, updated_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  display_name = VALUES(display_name), "
        "  group_code = VALUES(group_code), "
        "  api_key = VALUES(api_key), "
        "  base_url = VALUES(base_url), "
        "  model_id = VALUES(model_id), "
        "  extra_config = VALUES(extra_config), "
        "  enabled = VALUES(enabled), "
        "  updated_by = VALUES(updated_by)",
        (provider_code, display_name, group_code, api_key, base_url,
         model_id, extra_json, enabled, updated_by),
    )


def credential_provider_for_adapter(
    adapter_provider: str,
    media_kind: str | None = None,
) -> str:
    """把 llm_use_case_bindings.provider_code 映射到 llm_provider_configs.provider_code。

    media_kind="image" 且该 adapter 有独立的 image 凭据行时，返回 *_image；
    否则返回该 adapter 的主凭据行。
    """
    if adapter_provider not in _ADAPTER_CREDENTIAL_MAP:
        raise ProviderConfigError(
            f"未声明的 adapter provider_code={adapter_provider}；"
            "请在 appcore.llm_provider_configs._ADAPTER_CREDENTIAL_MAP 里注册。"
        )
    text_code, image_code = _ADAPTER_CREDENTIAL_MAP[adapter_provider]
    if (media_kind or "").strip().lower() == "image" and image_code:
        return image_code
    return text_code


def known_provider_codes() -> list[str]:
    return list(_KNOWN_PROVIDERS)


def known_providers_by_group() -> dict[str, list[tuple[str, str]]]:
    """UI 用：按 group_code 分组返回 [(provider_code, display_name), ...]。"""
    grouped: dict[str, list[tuple[str, str]]] = {}
    for code, (display, group) in _KNOWN_PROVIDERS.items():
        grouped.setdefault(group, []).append((code, display))
    return grouped


def provider_display_name(provider_code: str) -> str:
    spec = _KNOWN_PROVIDERS.get(provider_code)
    return spec[0] if spec else provider_code


def provider_group_code(provider_code: str) -> str:
    spec = _KNOWN_PROVIDERS.get(provider_code)
    return spec[1] if spec else "llm"
