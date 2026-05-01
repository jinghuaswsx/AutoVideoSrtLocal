"""Google Gemini 通用调用层（AI Studio 与 Vertex AI 共用）。

提供文本生成、结构化 JSON、视频/图片多模态、流式输出。

凭据决策：
  - service 为 use_case code（含 '.'）：先查 llm_use_case_bindings，按 provider
    决定走 AI Studio 还是 Vertex 凭据；image 入口可在调用方传 service 改写
  - service 为传统 service 名（"gemini"、"gemini_video_analysis" 等）：默认走
    AI Studio 文本凭据（gemini_aistudio_text）
  - 所有凭据均来自 appcore.llm_provider_configs，禁止读 .env / google_api_key

调用 helper（_build_contents / _build_config / _extract_gemini_tokens / _is_retryable
/ _guess_mime / _to_part / _upload_and_wait / GeminiError / genai_types）已迁到
`appcore.llm_providers._helpers.gemini_calls`，本模块 re-export 保留对外 API；
adapter 不应再 `from appcore import gemini as gemini_api` 反向 import 业务模块。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Generator, Iterable

from google import genai

from appcore import ai_billing
from appcore.llm_provider_configs import (
    ProviderConfigError,
    get_provider_config,
)
from appcore.llm_providers._helpers.gemini_calls import (
    GeminiError,
    _FILE_ACTIVE_TIMEOUT,
    _FILE_POLL_INTERVAL,
    _GEMINI_UNSUPPORTED_SCHEMA_KEYS,
    _INLINE_MAX_BYTES,
    _RETRYABLE_STATUS,
    _build_config,
    _build_contents,
    _extract_gemini_tokens,
    _guess_mime,
    _is_retryable,
    _sanitize_schema_for_gemini,
    _to_part,
    _upload_and_wait,
    genai_errors,
    genai_types,
)

logger = logging.getLogger(__name__)

_DEFAULT_AISTUDIO_PROVIDER = "gemini_aistudio_text"
_DEFAULT_CLOUD_PROVIDER = "gemini_cloud_text"
_DEFAULT_ADC_PROVIDER = "gemini_vertex_adc_text"
_FALLBACK_MODEL = "gemini-3.1-flash-lite-preview"


_clients: dict[str, genai.Client] = {}

# 支持视频分析的 Gemini 3 系列模型
VIDEO_CAPABLE_MODELS: list[tuple[str, str]] = [
    ("gemini-3.1-pro-preview",        "Gemini 3.1 Pro"),
    ("gemini-3-flash-preview",        "Gemini 3 Flash"),
    ("gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash-Lite"),
]


def model_display_name(model_id: str) -> str:
    """根据 model_id 返回可展示的名称；找不到时回退原始 id。"""
    for mid, label in VIDEO_CAPABLE_MODELS:
        if mid == model_id:
            return label
    return model_id or ""


def _binding_lookup(service: str) -> dict | None:
    """若 service 像 use_case code（含 '.'），查 bindings；否则 None。"""
    if not isinstance(service, str) or "." not in service:
        return None
    try:
        from appcore import llm_bindings
        return llm_bindings.resolve(service)
    except KeyError:
        return None


def _resolve_provider_code(service: str) -> tuple[str, str]:
    """决定该 service 走哪个 llm_provider_configs 行。

    返回 (provider_code, "aistudio" | "cloud" | "adc")。
    """
    binding = _binding_lookup(service)
    if binding:
        provider = (binding.get("provider") or "").strip()
        if provider == "gemini_vertex":
            return _DEFAULT_CLOUD_PROVIDER, "cloud"
        if provider == "gemini_vertex_adc":
            return _DEFAULT_ADC_PROVIDER, "adc"
        if provider == "gemini_aistudio":
            return _DEFAULT_AISTUDIO_PROVIDER, "aistudio"
    if service in {"gemini_cloud", "gemini_cloud_text", "gemini_cloud_image"}:
        return _DEFAULT_CLOUD_PROVIDER, "cloud"
    if service in {"gemini_vertex_adc", "gemini_vertex_adc_text", "gemini_vertex_adc_image"}:
        provider_code = "gemini_vertex_adc_image" if service == "gemini_vertex_adc_image" else _DEFAULT_ADC_PROVIDER
        return provider_code, "adc"
    return _DEFAULT_AISTUDIO_PROVIDER, "aistudio"


def _binding_model_for_backend(service: str, backend: str) -> str:
    binding = _binding_lookup(service)
    if not binding:
        return ""
    provider = (binding.get("provider") or "").strip()
    allowed = {
        "aistudio": "gemini_aistudio",
        "cloud": "gemini_vertex",
        "adc": "gemini_vertex_adc",
    }.get(backend)
    if provider != allowed:
        return ""
    return (binding.get("model") or "").strip()


def resolve_config(user_id: int | None = None, service: str = "gemini",
                   default_model: str | None = None) -> tuple[str, str]:
    """返回 (api_key, model_id)。

    api_key 与 model_id 全部来自 llm_provider_configs；不读 .env，不读 google_api_key 文件。
    binding 命中时使用 binding 的 model_id，否则使用 DB 行 model_id 或调用方传入的 default。
    """
    provider_code, backend = _resolve_provider_code(service)
    cfg = get_provider_config(provider_code)
    api_key = "" if backend == "adc" else ((cfg.api_key if cfg else "") or "")

    model_id = _binding_model_for_backend(service, backend)
    if not model_id and cfg and cfg.model_id:
        model_id = cfg.model_id
    if not model_id:
        model_id = (default_model or "").strip() or _FALLBACK_MODEL
    return api_key, model_id


def _client_cache_key(provider_code: str, api_key: str, project: str, location: str) -> str:
    if provider_code in {
        "gemini_cloud_text",
        "gemini_cloud_image",
        "gemini_vertex_adc_text",
        "gemini_vertex_adc_image",
    }:
        if project:
            return f"cloud:{provider_code}:{project}:{location}"
        return f"cloud-legacy:{provider_code}:{api_key}"
    return f"aistudio:{provider_code}:{api_key}"


def _get_client_for_service(service: str) -> tuple[genai.Client, str]:
    """根据 service 解析出 (client, model_id)。"""
    provider_code, backend = _resolve_provider_code(service)
    cfg = get_provider_config(provider_code)
    if cfg is None:
        raise GeminiError(
            f"未配置 Gemini provider {provider_code}，请在 /settings 的「服务商接入」页填写。"
        )
    api_key = (cfg.api_key or "").strip()
    extra = cfg.extra_config or {}
    project = (extra.get("project") or "").strip() if backend in {"cloud", "adc"} else ""
    location = (extra.get("location") or "global").strip() if backend in {"cloud", "adc"} else ""

    if backend == "adc":
        api_key = ""
        if not project:
            raise GeminiError(
                f"Gemini Vertex ADC（{provider_code}）缺少 extra_config.project，"
                "请在 /settings 填写。"
            )
    if backend == "cloud" and not (project or api_key):
        raise GeminiError(
            f"Gemini Cloud（{provider_code}）缺少 api_key 或 extra_config.project，"
            "请在 /settings 填写。"
        )
    if backend == "aistudio" and not api_key:
        raise GeminiError(
            f"Gemini AI Studio（{provider_code}）缺少 api_key，请在 /settings 填写。"
        )

    cache_key = _client_cache_key(provider_code, api_key, project, location)
    client = _clients.get(cache_key)
    if client is None:
        if backend in {"cloud", "adc"}:
            if project:
                client = genai.Client(
                    vertexai=True,
                    project=project,
                    location=location or "global",
                )
            else:
                client = genai.Client(vertexai=True, api_key=api_key)
        else:
            client = genai.Client(api_key=api_key)
        _clients[cache_key] = client

    model_id = _binding_model_for_backend(service, backend) or cfg.model_id or _FALLBACK_MODEL
    return client, model_id


# helper 函数（_guess_mime / _upload_and_wait / _to_part / _build_contents /
# _GEMINI_UNSUPPORTED_SCHEMA_KEYS / _sanitize_schema_for_gemini / _build_config /
# _is_retryable / _extract_gemini_tokens）已迁到 appcore.llm_providers._helpers
# .gemini_calls，本模块顶部 import 后可直接使用。


def _resolve_billing_use_case(service: str) -> str | None:
    if not isinstance(service, str) or "." not in service:
        return None
    try:
        from appcore.llm_use_cases import get_use_case
        get_use_case(service)
        return service
    except KeyError:
        return None


def _resolve_billing_provider(use_case_code: str | None) -> str:
    if use_case_code:
        try:
            binding = _binding_lookup(use_case_code)
        except Exception:
            binding = None
        if binding and binding.get("provider"):
            return str(binding["provider"])
        try:
            from appcore.llm_use_cases import get_use_case
            return str(get_use_case(use_case_code)["default_provider"])
        except KeyError:
            pass
    return "gemini_aistudio"


def _log_gemini_usage(
    *, user_id: int | None, project_id: str | None, service: str,
    model_id: str, success: bool, resp: Any = None, error: Exception | None = None,
    request_payload: dict | None = None, response_payload: dict | None = None,
) -> None:
    if user_id is None:
        return
    try:
        use_case_code = _resolve_billing_use_case(service)
        if not use_case_code:
            return
        input_tokens, output_tokens = (None, None)
        if resp is not None:
            input_tokens, output_tokens = _extract_gemini_tokens(resp)
        extra: dict[str, Any] | None = None
        if error is not None:
            extra = {"error": str(error)[:500]}
        ai_billing.log_request(
            use_case_code=use_case_code,
            user_id=user_id,
            project_id=project_id,
            provider=_resolve_billing_provider(use_case_code),
            model=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            units_type="tokens",
            success=success,
            extra=extra,
            request_payload=request_payload,
            response_payload=response_payload,
        )
    except Exception:
        logger.debug("Gemini ai_billing record failed", exc_info=True)


def generate(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    media: Iterable[str | Path] | str | Path | None = None,
    response_schema: dict | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    max_retries: int = 3,
    user_id: int | None = None,
    project_id: str | None = None,
    service: str = "gemini",
    default_model: str | None = None,
    return_payload: bool = False,
    google_search: bool = False,
) -> str | Any:
    """一次性生成。传 response_schema 时返回解析后的 JSON。"""
    try:
        client, db_model = _get_client_for_service(service)
    except ProviderConfigError as exc:
        raise GeminiError(str(exc)) from exc
    model_id = (model or "").strip() or db_model or default_model or _FALLBACK_MODEL
    media_list = [media] if isinstance(media, (str, Path)) else list(media) if media else None
    request_payload: dict[str, Any] = {
        "type": "generate",
        "service": service,
        "model": model_id,
        "prompt": prompt,
    }
    if system:
        request_payload["system"] = system
    if media_list:
        request_payload["media"] = [str(item) for item in media_list]
    if response_schema is not None:
        request_payload["response_schema"] = response_schema
    if temperature is not None:
        request_payload["temperature"] = temperature
    if max_output_tokens is not None:
        request_payload["max_output_tokens"] = max_output_tokens
    if google_search:
        request_payload["google_search"] = True
        request_payload["tools"] = [{"google_search": {}}]
    contents = _build_contents(client, prompt, media_list)
    cfg = _build_config(
        system=system,
        temperature=temperature,
        response_schema=response_schema,
        max_output_tokens=max_output_tokens,
        google_search=google_search,
    )

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=model_id, contents=contents, config=cfg,
            )
            input_tokens, output_tokens = _extract_gemini_tokens(resp)
            usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
            if response_schema is not None:
                parsed = getattr(resp, "parsed", None)
                if parsed is not None:
                    _log_gemini_usage(
                        user_id=user_id, project_id=project_id, service=service,
                        model_id=model_id, success=True, resp=resp,
                        request_payload=request_payload,
                        response_payload={"json": parsed, "usage": usage},
                    )
                    if return_payload:
                        return {"text": None, "json": parsed, "raw": resp, "usage": usage}
                    return parsed
                payload = json.loads(resp.text)
                _log_gemini_usage(
                    user_id=user_id, project_id=project_id, service=service,
                    model_id=model_id, success=True, resp=resp,
                    request_payload=request_payload,
                    response_payload={"json": payload, "usage": usage},
                )
                if return_payload:
                    return {"text": None, "json": payload, "raw": resp, "usage": usage}
                return payload
            text = resp.text or ""
            _log_gemini_usage(
                user_id=user_id, project_id=project_id, service=service,
                model_id=model_id, success=True, resp=resp,
                request_payload=request_payload,
                response_payload={"text": text, "usage": usage},
            )
            if return_payload:
                return {"text": text, "json": None, "raw": resp, "usage": usage}
            return text
        except Exception as e:
            last_err = e
            retryable_parse_error = (
                response_schema is not None
                and google_search
                and isinstance(e, json.JSONDecodeError)
            )
            if attempt < max_retries - 1 and (_is_retryable(e) or retryable_parse_error):
                delay = 2 ** attempt
                logger.warning("Gemini 调用失败，%ds 后重试（%s/%s）：%s",
                               delay, attempt + 1, max_retries, e)
                time.sleep(delay)
                continue
            break
    _log_gemini_usage(
        user_id=user_id, project_id=project_id, service=service,
        model_id=model_id, success=False, error=last_err,
        request_payload=request_payload,
        response_payload={"error": str(last_err)[:500] if last_err else "unknown"},
    )
    raise GeminiError(f"Gemini 调用失败：{last_err}") from last_err


def generate_stream(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    media: Iterable[str | Path] | str | Path | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    user_id: int | None = None,
    project_id: str | None = None,
    service: str = "gemini",
    default_model: str | None = None,
) -> Generator[str, None, None]:
    """流式生成，yield 文本片段。不支持 response_schema。"""
    try:
        client, db_model = _get_client_for_service(service)
    except ProviderConfigError as exc:
        raise GeminiError(str(exc)) from exc
    model_id = (model or "").strip() or db_model or default_model or _FALLBACK_MODEL
    media_list = [media] if isinstance(media, (str, Path)) else list(media) if media else None
    contents = _build_contents(client, prompt, media_list)
    cfg = _build_config(
        system=system,
        temperature=temperature,
        response_schema=None,
        max_output_tokens=max_output_tokens,
    )
    request_payload = {
        "type": "generate_stream",
        "service": service,
        "model": model_id,
        "prompt": prompt,
        "system": system,
        "media": [str(item) for item in media_list] if media_list else [],
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    last_chunk: Any = None
    text_chunks: list[str] = []
    try:
        for chunk in client.models.generate_content_stream(
            model=model_id, contents=contents, config=cfg,
        ):
            last_chunk = chunk
            if chunk.text:
                text_chunks.append(chunk.text)
                yield chunk.text
    except Exception as e:
        _log_gemini_usage(
            user_id=user_id, project_id=project_id, service=service,
            model_id=model_id, success=False, error=e,
            request_payload=request_payload,
            response_payload={"error": str(e)[:500]},
        )
        raise GeminiError(f"Gemini 流式调用失败：{e}") from e
    _log_gemini_usage(
        user_id=user_id, project_id=project_id, service=service,
        model_id=model_id, success=True, resp=last_chunk,
        request_payload=request_payload,
        response_payload={"text": "".join(text_chunks), "stream": True},
    )


def is_configured(user_id: int | None = None) -> bool:
    """admin 设置页用：判断默认 Gemini AI Studio 文本通道是否已配。"""
    cfg = get_provider_config(_DEFAULT_AISTUDIO_PROVIDER)
    return bool(cfg and cfg.api_key)
