from __future__ import annotations

import json
import mimetypes
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from google import genai
from google.genai import types as genai_types

from link_check_desktop import settings


_INLINE_MAX_BYTES = 20 * 1024 * 1024
_FILE_ACTIVE_TIMEOUT = 300
_FILE_POLL_INTERVAL = 2
_CLIENT: genai.Client | None = None


class GeminiClientError(RuntimeError):
    pass


def _get_client() -> genai.Client:
    global _CLIENT
    if not settings.GEMINI_API_KEY:
        raise GeminiClientError("GEMINI_API_KEY 未配置")
    if _CLIENT is None:
        _CLIENT = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _CLIENT


def _guess_mime(path: Path) -> str:
    mt, _ = mimetypes.guess_type(str(path))
    if mt:
        return mt
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".avif": "image/avif",
    }.get(path.suffix.lower(), "application/octet-stream")


def _upload_and_wait(client: genai.Client, path: Path) -> genai_types.File:
    safe_name = path.stem.encode("utf-8").hex()[:16] + (path.suffix or ".bin")
    tmp_path = Path(tempfile.gettempdir()) / f"gemini_upload_{safe_name}"
    try:
        shutil.copy2(path, tmp_path)
        uploaded = client.files.upload(file=str(tmp_path))
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    deadline = time.time() + _FILE_ACTIVE_TIMEOUT
    while uploaded.state and uploaded.state.name == "PROCESSING":
        if time.time() > deadline:
            raise GeminiClientError(f"Gemini 文件处理超时：{path.name}")
        time.sleep(_FILE_POLL_INTERVAL)
        uploaded = client.files.get(name=uploaded.name)
    if uploaded.state and uploaded.state.name == "FAILED":
        raise GeminiClientError(f"Gemini 文件处理失败：{path.name}")
    return uploaded


def _to_part(client: genai.Client, media: str | Path) -> genai_types.Part:
    path = Path(media)
    if not path.is_file():
        raise GeminiClientError(f"文件不存在：{path}")
    mime = _guess_mime(path)
    size = path.stat().st_size
    if size <= _INLINE_MAX_BYTES:
        return genai_types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)
    uploaded = _upload_and_wait(client, path)
    return genai_types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime)


def _build_contents(client: genai.Client, prompt: str, media: Iterable[str | Path] | None) -> list[genai_types.Part]:
    parts: list[genai_types.Part] = []
    if media:
        for item in media:
            parts.append(_to_part(client, item))
    parts.append(genai_types.Part.from_text(text=prompt))
    return parts


def generate_json(
    *,
    model: str,
    prompt: str,
    media: Iterable[str | Path] | None = None,
    response_schema: dict | None = None,
    temperature: float | None = 0,
    system: str | None = None,
    max_output_tokens: int | None = None,
) -> dict[str, Any]:
    client = _get_client()
    config_kwargs: dict[str, Any] = {
        "response_mime_type": "application/json",
        "response_schema": response_schema or {"type": "object"},
    }
    if system:
        config_kwargs["system_instruction"] = system
    if temperature is not None:
        config_kwargs["temperature"] = temperature
    if max_output_tokens is not None:
        config_kwargs["max_output_tokens"] = max_output_tokens

    response = client.models.generate_content(
        model=model,
        contents=_build_contents(client, prompt, media),
        config=genai_types.GenerateContentConfig(**config_kwargs),
    )
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    if response.text:
        payload = json.loads(response.text)
        if isinstance(payload, dict):
            return payload
    return {}
