"""
ElevenLabs 共享音色库分页同步
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import requests

from appcore.db import execute, query
from pipeline.voice_embedding import embed_audio_file, serialize_embedding

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


def _list_voices_without_embedding(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """查询尚未回写 embedding 的音色。"""
    sql = (
        "SELECT voice_id, preview_url FROM elevenlabs_voices "
        "WHERE preview_url IS NOT NULL AND audio_embedding IS NULL"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return query(sql)


def _download_preview(url: str, dest_path) -> str:
    """下载 preview 音频到 dest_path（pathlib.Path 或 str）。"""
    resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    # dest_path 可能是 pathlib.Path（测试里传的是 tmp_path / file）或 str
    dest_str = str(dest_path)
    with open(dest_str, "wb") as f:
        f.write(resp.content)
    return dest_str


def _update_embedding(voice_id: str, blob: bytes) -> None:
    execute(
        "UPDATE elevenlabs_voices SET audio_embedding=%s, updated_at=%s "
        "WHERE voice_id=%s",
        (blob, datetime.utcnow(), voice_id),
    )


def embed_missing_voices(cache_dir: str, limit: Optional[int] = None) -> int:
    """批量下载 preview_url 并生成 embedding，回写到数据库。

    单条失败不中断整批（日志 warning）。
    返回成功处理的条目数。
    """
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    count = 0
    for row in _list_voices_without_embedding(limit=limit):
        voice_id = row["voice_id"]
        url = row.get("preview_url")
        if not url:
            continue
        file_name = hashlib.sha1(voice_id.encode("utf-8")).hexdigest() + ".mp3"
        dest = cache_path / file_name
        try:
            _download_preview(url, dest)
            vec = embed_audio_file(str(dest))
            _update_embedding(voice_id, serialize_embedding(vec))
            count += 1
        except Exception as exc:
            # 容错：单条失败不影响批次
            print(f"[embed_missing_voices] failed {voice_id}: {exc}")
    return count
