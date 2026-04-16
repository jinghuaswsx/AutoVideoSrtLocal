"""
ElevenLabs 共享音色库分页同步
"""
import json
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
import requests

from appcore.db import execute

SHARED_VOICES_URL = "https://api.elevenlabs.io/v1/shared-voices"
DEFAULT_PAGE_SIZE = 100
REQUEST_TIMEOUT = 30


def fetch_shared_voices_page(
    api_key: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    next_page_token: Optional[str] = None,
    language: Optional[str] = None,
    gender: Optional[str] = None,
    category: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """抓取一页共享音色。返回 (voices, next_page_token)。

    当 has_more 为 False 时，next_page_token 返回 None。
    """
    headers = {"xi-api-key": api_key}
    params: Dict[str, Any] = {"page_size": page_size}
    if next_page_token:
        params["next_page_token"] = next_page_token
    if language:
        params["language"] = language
    if gender:
        params["gender"] = gender
    if category:
        params["category"] = category

    resp = requests.get(
        SHARED_VOICES_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    voices = data.get("voices") or []
    has_more = bool(data.get("has_more"))
    next_token = data.get("next_page_token") if has_more else None
    return voices, next_token


def upsert_voice(voice: Dict[str, Any]) -> None:
    """将单条音色写入（或更新）elevenlabs_voices 表。"""
    labels = voice.get("labels") or {}
    now = datetime.utcnow()
    execute(
        """
        INSERT INTO elevenlabs_voices
          (voice_id, name, gender, age, language, accent, category,
           descriptive, preview_url, labels_json, public_owner_id,
           synced_at, updated_at)
        VALUES
          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          name=VALUES(name), gender=VALUES(gender), age=VALUES(age),
          language=VALUES(language), accent=VALUES(accent),
          category=VALUES(category), descriptive=VALUES(descriptive),
          preview_url=VALUES(preview_url), labels_json=VALUES(labels_json),
          public_owner_id=VALUES(public_owner_id),
          synced_at=VALUES(synced_at)
        """,
        (
            voice["voice_id"],
            voice.get("name") or "",
            voice.get("gender") or labels.get("gender"),
            voice.get("age") or labels.get("age"),
            voice.get("language") or labels.get("language"),
            voice.get("accent") or labels.get("accent"),
            voice.get("category"),
            voice.get("descriptive") or labels.get("descriptive"),
            voice.get("preview_url"),
            json.dumps(labels),
            voice.get("public_owner_id"),
            now,
            now,
        ),
    )


def sync_all_shared_voices(
    api_key: str,
    *,
    language: Optional[str] = None,
    gender: Optional[str] = None,
    category: Optional[str] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> int:
    """遍历所有分页，upsert 到数据库。返回处理的条目总数。"""
    total = 0
    next_token: Optional[str] = None
    while True:
        voices, next_token = fetch_shared_voices_page(
            api_key=api_key,
            page_size=page_size,
            next_page_token=next_token,
            language=language,
            gender=gender,
            category=category,
        )
        for voice in voices:
            if not voice.get("voice_id"):
                continue
            upsert_voice(voice)
            total += 1
        if not next_token:
            break
    return total
