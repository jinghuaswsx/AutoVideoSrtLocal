"""Google Gemini 通用调用层（AI Studio 与 Vertex AI 共用）。

提供文本生成、结构化 JSON、视频/图片多模态、流式输出。

凭据决策：
  - service 为 use_case code（含 '.'）：先查 llm_use_case_bindings，按 provider
    决定走 AI Studio 还是 Vertex 凭据；image 入口可在调用方传 service 改写
  - service 为传统 service 名（"gemini"、"gemini_video_analysis" 等）：默认走
    AI Studio 文本凭据（gemini_aistudio_text）
  - 所有凭据均来自 appcore.llm_provider_configs，禁止读 .env / google_api_key
"""
from __future__ import annotations

import logging
import json
import mimetypes
import time
from pathlib import Path
from typing import Any, Generator, Iterable

from google import genai
from google.genai import types as genai_types
from google.genai import errors as genai_errors

from appcore import ai_billing
from appcore.llm_provider_configs import (
    ProviderConfigError,
    get_provider_config,
)

logger = logging.getLogger(__name__)

_INLINE_MAX_BYTES = 20 * 1024 * 1024  # 小于 20MB 走 inline，避免 Files API 往返
_FILE_ACTIVE_TIMEOUT = 900
_FILE_POLL_INTERVAL = 2
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

_DEFAULT_AISTUDIO_PROVIDER = "gemini_aistudio_text"
_DEFAULT_CLOUD_PROVIDER = "gemini_cloud_text"
_FALLBACK_MODEL = "gemini-3.1-flash-lite-preview"


class GeminiError(RuntimeError):
    """Gemini 调用错误，业务侧捕获用。"""


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

    返回 (provider_code, "aistudio" | "cloud")。
    """
    binding = _binding_lookup(service)
    if binding:
        provider = (binding.get("provider") or "").strip()
        if provider == "gemini_vertex":
            return _DEFAULT_CLOUD_PROVIDER, "cloud"
        if provider == "gemini_aistudio":
            return _DEFAULT_AISTUDIO_PROVIDER, "aistudio"
    if service in {"gemini_cloud", "gemini_cloud_text", "gemini_cloud_image"}:
        return _DEFAULT_CLOUD_PROVIDER, "cloud"
    return _DEFAULT_AISTUDIO_PROVIDER, "aistudio"


def resolve_config(user_id: int | None = None, service: str = "gemini",
                   default_model: str | None = None) -> tuple[str, str]:
    """返回 (api_key, model_id)。

    api_key 与 model_id 全部来自 llm_provider_configs；不读 .env，不读 google_api_key 文件。
    binding 命中时使用 binding 的 model_id，否则使用 DB 行 model_id 或调用方传入的 default。
    """
    provider_code, _ = _resolve_provider_code(service)
    cfg = get_provider_config(provider_code)
    api_key = (cfg.api_key if cfg else "") or ""

    binding = _binding_lookup(service)
    model_id = ""
    if binding and binding.get("model"):
        model_id = (binding["model"] or "").strip()
    if not model_id and cfg and cfg.model_id:
        model_id = cfg.model_id
    if not model_id:
        model_id = (default_model or "").strip() or _FALLBACK_MODEL
    return api_key, model_id


def _client_cache_key(provider_code: str, api_key: str, project: str, location: str) -> str:
    if provider_code in {"gemini_cloud_text", "gemini_cloud_image"}:
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
    project = (extra.get("project") or "").strip() if backend == "cloud" else ""
    location = (extra.get("location") or "global").strip() if backend == "cloud" else ""

    if backend == "cloud" and not (project or api_key):
        raise GeminiError(
            f"Gemini Cloud（{provider_code}）缺少 api_key 或 extra_config.project，"
            "请在 /settings 填写。"
        )
    if backend != "cloud" and not api_key:
        raise GeminiError(
            f"Gemini AI Studio（{provider_code}）缺少 api_key，请在 /settings 填写。"
        )

    cache_key = _client_cache_key(provider_code, api_key, project, location)
    client = _clients.get(cache_key)
    if client is None:
        if backend == "cloud":
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

    model_id = cfg.model_id or _FALLBACK_MODEL
    return client, model_id


def _guess_mime(path: Path) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        return mt
    suffix = path.suffix.lower()
    return {
        ".mp4": "video/mp4", ".mov": "video/quicktime",
        ".webm": "video/webm", ".mkv": "video/x-matroska",
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")


def _upload_and_wait(client: genai.Client, path: Path) -> genai_types.File:
    # google-genai SDK 会把文件名塞到 HTTP header；中文/非 ASCII 会触发
    # UnicodeEncodeError（httpx headers 要求 ASCII）。为稳妥，总是拷贝到
    # 一个 ASCII 安全的临时文件再上传。
    import hashlib
    import shutil
    import tempfile

    safe_name = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:16] + (path.suffix or ".bin")
    tmp_path = Path(tempfile.gettempdir()) / f"gemini_upload_{safe_name}"
    try:
        shutil.copy2(path, tmp_path)
        f = client.files.upload(file=str(tmp_path))
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    deadline = time.time() + _FILE_ACTIVE_TIMEOUT
    while f.state and f.state.name == "PROCESSING":
        if time.time() > deadline:
            raise GeminiError(f"视频上传超时未就绪：{path.name}")
        time.sleep(_FILE_POLL_INTERVAL)
        f = client.files.get(name=f.name)
    if f.state and f.state.name == "FAILED":
        raise GeminiError(f"视频处理失败：{path.name}")
    return f


def _to_part(client: genai.Client, media: str | Path) -> genai_types.Part:
    p = Path(media)
    if not p.is_file():
        raise GeminiError(f"文件不存在：{p}")
    mime = _guess_mime(p)
    size = p.stat().st_size
    if size <= _INLINE_MAX_BYTES:
        return genai_types.Part.from_bytes(data=p.read_bytes(), mime_type=mime)
    uploaded = _upload_and_wait(client, p)
    return genai_types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime)


def _build_contents(client: genai.Client, prompt: str,
                    media: Iterable[str | Path] | None) -> list[genai_types.Part]:
    parts: list[genai_types.Part] = []
    if media:
        for m in media:
            parts.append(_to_part(client, m))
    parts.append(genai_types.Part.from_text(text=prompt))
    return parts


_GEMINI_UNSUPPORTED_SCHEMA_KEYS = frozenset({
    "additionalProperties", "additional_properties",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "default",
    "minItems", "maxItems", "uniqueItems",
})


def _sanitize_schema_for_gemini(schema: dict | list | Any) -> Any:
    """递归清洗 JSON Schema，去掉 Gemini API 不支持的关键字。"""
    if isinstance(schema, dict):
        return {
            k: _sanitize_schema_for_gemini(v)
            for k, v in schema.items()
            if k not in _GEMINI_UNSUPPORTED_SCHEMA_KEYS
        }
    if isinstance(schema, list):
        return [_sanitize_schema_for_gemini(item) for item in schema]
    return schema


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    for method in ("model_dump", "to_json_dict", "to_dict"):
        fn = getattr(value, method, None)
        if callable(fn):
            try:
                return _jsonable(fn(exclude_none=True))
            except TypeError:
                try:
                    return _jsonable(fn())
                except TypeError:
                    pass
    if hasattr(value, "__dict__"):
        return {
            str(k): _jsonable(v)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
    return str(value)


def _extract_grounding_metadata(resp: Any) -> dict | None:
    candidates = getattr(resp, "candidates", None) or []
    if not candidates:
        return None
    metadata = getattr(candidates[0], "grounding_metadata", None)
    if metadata is None:
        return None
    payload = _jsonable(metadata)
    return payload if isinstance(payload, dict) else {"value": payload}


def _parse_json_text(raw: str) -> Any:
    content = (raw or "").strip()
    if content.startswith("```"):
        parts = content.split("```")
        content = parts[1] if len(parts) > 1 else content
        if content.lstrip().startswith("json"):
            content = content.lstrip()[4:]
    try:
        return json.loads(content.strip())
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start >= 0 and end > start:
            return json.loads(content[start:end + 1])
        raise


def _build_config(
    *,
    system: str | None,
    temperature: float | None,
    response_schema: dict | None,
    max_output_tokens: int | None,
    enable_google_search: bool = False,
) -> genai_types.GenerateContentConfig:
    kwargs: dict[str, Any] = {}
    if system:
        kwargs["system_instruction"] = system
    if temperature is not None:
        kwargs["temperature"] = temperature
    if max_output_tokens is not None:
        kwargs["max_output_tokens"] = max_output_tokens
    if enable_google_search:
        kwargs["tools"] = [genai_types.Tool(google_search=genai_types.GoogleSearch())]
    if response_schema is not None and not enable_google_search:
        kwargs["response_mime_type"] = "application/json"
        kwargs["response_schema"] = _sanitize_schema_for_gemini(response_schema)
    return genai_types.GenerateContentConfig(**kwargs)


def _is_retryable(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, int) and code in _RETRYABLE_STATUS:
        return True
    return isinstance(exc, genai_errors.ServerError)


def _extract_gemini_tokens(resp: Any) -> tuple[int | None, int | None]:
    meta = getattr(resp, "usage_metadata", None)
    if not meta:
        return None, None
    prompt = getattr(meta, "prompt_token_count", None)
    output = getattr(meta, "candidates_token_count", None)
    try:
        return (int(prompt) if prompt is not None else None,
                int(output) if output is not None else None)
    except (TypeError, ValueError):
        return None, None


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
    enable_google_search: bool = False,
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
    if enable_google_search:
        request_payload["enable_google_search"] = True
    contents = _build_contents(client, prompt, media_list)
    cfg = _build_config(
        system=system,
        temperature=temperature,
        response_schema=response_schema,
        max_output_tokens=max_output_tokens,
        enable_google_search=enable_google_search,
    )

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=model_id, contents=contents, config=cfg,
            )
            input_tokens, output_tokens = _extract_gemini_tokens(resp)
            usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
            grounding_metadata = _extract_grounding_metadata(resp)
            if response_schema is not None:
                parsed = getattr(resp, "parsed", None)
                if parsed is not None:
                    response_payload = {"json": parsed, "usage": usage}
                    if grounding_metadata is not None:
                        response_payload["grounding_metadata"] = grounding_metadata
                    _log_gemini_usage(
                        user_id=user_id, project_id=project_id, service=service,
                        model_id=model_id, success=True, resp=resp,
                        request_payload=request_payload,
                        response_payload=response_payload,
                    )
                    if return_payload:
                        return {
                            "text": None,
                            "json": parsed,
                            "raw": resp,
                            "usage": usage,
                            "grounding_metadata": grounding_metadata,
                        }
                    return parsed
                payload = _parse_json_text(resp.text or "{}")
                response_payload = {"json": payload, "usage": usage}
                if grounding_metadata is not None:
                    response_payload["grounding_metadata"] = grounding_metadata
                _log_gemini_usage(
                    user_id=user_id, project_id=project_id, service=service,
                    model_id=model_id, success=True, resp=resp,
                    request_payload=request_payload,
                    response_payload=response_payload,
                )
                if return_payload:
                    return {
                        "text": None,
                        "json": payload,
                        "raw": resp,
                        "usage": usage,
                        "grounding_metadata": grounding_metadata,
                    }
                return payload
            text = resp.text or ""
            response_payload = {"text": text, "usage": usage}
            if grounding_metadata is not None:
                response_payload["grounding_metadata"] = grounding_metadata
            _log_gemini_usage(
                user_id=user_id, project_id=project_id, service=service,
                model_id=model_id, success=True, resp=resp,
                request_payload=request_payload,
                response_payload=response_payload,
            )
            if return_payload:
                return {
                    "text": text,
                    "json": None,
                    "raw": resp,
                    "usage": usage,
                    "grounding_metadata": grounding_metadata,
                }
            return text
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1 and _is_retryable(e):
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
