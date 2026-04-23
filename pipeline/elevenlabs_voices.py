"""ElevenLabs Voice Library import integration.

Resolves voice IDs from raw strings or ElevenLabs URLs, fetches metadata
from the ElevenLabs shared-voices API, and registers voices locally.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import parse_qs, urlparse

import requests

log = logging.getLogger(__name__)

_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{10,30}$")
_ELEVENLABS_BASE = "https://api.elevenlabs.io"


def _default_api_key() -> str | None:
    return os.getenv("ELEVENLABS_API_KEY") or None


# ------------------------------------------------------------------
# Parsing
# ------------------------------------------------------------------

def extract_voice_id(source: str) -> str:
    """Extract an ElevenLabs voiceId from a raw ID or a Voice Library URL.

    Accepted forms:
      - ``zDBYcuJrpuZ6YQ7AgRUw``
      - ``https://elevenlabs.io/app/voice-library?voiceId=zDBYcuJrpuZ6YQ7AgRUw``
      - any URL whose query string contains ``voiceId``

    Raises ``ValueError`` if no valid voice ID can be found.
    """
    source = source.strip()
    if _VOICE_ID_RE.match(source):
        return source

    try:
        parsed = urlparse(source)
        qs = parse_qs(parsed.query)
        candidates = qs.get("voiceId") or qs.get("voice_id") or []
        for candidate in candidates:
            candidate = candidate.strip()
            if _VOICE_ID_RE.match(candidate):
                return candidate
    except Exception:
        pass

    # Try extracting from path segments (e.g. /voices/XXXX)
    parts = source.split("/")
    for part in reversed(parts):
        part = part.strip()
        if _VOICE_ID_RE.match(part):
            return part

    raise ValueError(f"无法从输入中解析 voiceId: {source!r}")


# ------------------------------------------------------------------
# ElevenLabs API calls
# ------------------------------------------------------------------

def find_shared_voice(voice_id: str, api_key: str | None = None) -> dict:
    """Search ElevenLabs shared voices to resolve *voice_id*.

    Returns the first matching voice dict from the API response.
    Raises ``LookupError`` if not found, ``RuntimeError`` on API error.
    """
    api_key = api_key or _default_api_key()
    if not api_key:
        raise RuntimeError("缺少 ElevenLabs API Key，无法查询共享音色")

    url = f"{_ELEVENLABS_BASE}/v1/shared-voices"
    headers = {"xi-api-key": api_key}
    params = {"page_size": 25, "search": voice_id}

    resp = requests.get(url, headers=headers, params=params, timeout=15)
    if resp.status_code == 401:
        raise RuntimeError("ElevenLabs API Key 无效或无权访问 Voice Library API")
    if resp.status_code != 200:
        raise RuntimeError(f"ElevenLabs API 错误 ({resp.status_code}): {resp.text[:200]}")

    data = resp.json()
    voices = data.get("voices") or []
    for voice in voices:
        if voice.get("voice_id") == voice_id:
            return voice

    raise LookupError(f"未找到该 ElevenLabs 共享音色: {voice_id}")


def add_shared_voice_to_account(
    public_user_id: str, voice_id: str, api_key: str | None = None
) -> dict:
    """Add a shared voice to the current ElevenLabs account's 'My Voices'."""
    api_key = api_key or _default_api_key()
    if not api_key:
        raise RuntimeError("缺少 ElevenLabs API Key")

    url = f"{_ELEVENLABS_BASE}/v1/voices/add/{public_user_id}/{voice_id}"
    headers = {"xi-api-key": api_key}
    resp = requests.post(url, headers=headers, timeout=15)
    if resp.status_code not in (200, 201):
        log.warning("add_shared_voice_to_account failed: %s %s", resp.status_code, resp.text[:200])
        raise RuntimeError(f"添加到 ElevenLabs My Voices 失败 ({resp.status_code})")
    return resp.json()


def get_voice_detail(voice_id: str, api_key: str | None = None) -> dict | None:
    """Fetch a single voice's details from the account."""
    api_key = api_key or _default_api_key()
    if not api_key:
        return None
    url = f"{_ELEVENLABS_BASE}/v1/voices/{voice_id}"
    headers = {"xi-api-key": api_key}
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code == 200:
        return resp.json()
    return None


# ------------------------------------------------------------------
# High-level import
# ------------------------------------------------------------------

def _map_shared_voice_to_local(shared: dict, overrides: dict | None = None) -> dict:
    """Map an ElevenLabs shared-voice payload to our local voice schema."""
    overrides = overrides or {}
    name = overrides.get("name") or shared.get("name") or "Imported Voice"
    labels = shared.get("labels") or {}
    language = (
        overrides.get("language")
        or shared.get("language")
        or labels.get("language")
        or "en"
    )

    # Determine gender: override > labels > default male
    gender = overrides.get("gender") or ""
    if not gender:
        gender_label = labels.get("gender", "").lower()
        if "female" in gender_label:
            gender = "female"
        else:
            gender = "male"

    # Build style tags from labels
    style_tags = list(overrides.get("style_tags") or [])
    for key in ("accent", "age", "descriptive", "use_case"):
        val = labels.get(key)
        if val and val not in style_tags:
            style_tags.append(val)

    preview_url = shared.get("preview_url") or ""

    return {
        "name": name,
        "gender": gender,
        "language": language,
        "elevenlabs_voice_id": shared.get("voice_id") or "",
        "description": overrides.get("description") or shared.get("description") or "Imported from ElevenLabs Voice Library",
        "style_tags": style_tags,
        "is_default": bool(overrides.get("is_default", False)),
        "source": "elevenlabs_voice_library",
        "preview_url": preview_url,
        "labels": labels,
    }


def import_voice(
    source: str,
    *,
    user_id: int,
    api_key: str | None = None,
    save_to_elevenlabs: bool = False,
    overrides: dict | None = None,
) -> dict:
    """Full import flow: parse source → resolve via API → store locally.

    Returns the local voice record dict.
    Raises ValueError / LookupError / RuntimeError on failure.
    """
    from pipeline.voice_library import get_voice_library

    voice_id = extract_voice_id(source)
    shared = find_shared_voice(voice_id, api_key=api_key)

    if save_to_elevenlabs:
        public_user_id = shared.get("public_owner_id") or ""
        if public_user_id:
            try:
                add_shared_voice_to_account(public_user_id, voice_id, api_key=api_key)
            except RuntimeError:
                log.warning("Could not add voice %s to ElevenLabs account, continuing anyway", voice_id)

    local_payload = _map_shared_voice_to_local(shared, overrides)

    lib = get_voice_library()

    # Idempotent: if same elevenlabs_voice_id already exists, update it
    existing = lib.get_voice_by_elevenlabs_id(voice_id, user_id)

    if existing:
        voice = lib.update_voice(existing["id"], user_id, local_payload)
    else:
        voice = lib.create_voice(user_id, local_payload)

    return voice
