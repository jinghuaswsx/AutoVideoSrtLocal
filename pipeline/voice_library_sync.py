"""
ElevenLabs 共享音色库分页同步
"""
import hashlib
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable
import requests

from appcore.db import execute, query
from pipeline.voice_embedding import embed_audio_file, serialize_embedding

log = logging.getLogger(__name__)

SHARED_VOICES_URL = "https://api.elevenlabs.io/v1/shared-voices"
DEFAULT_PAGE_SIZE = 100
REQUEST_TIMEOUT = 30
DOWNLOAD_RETRIES = 3
DOWNLOAD_RETRY_SLEEP_SECONDS = 1.0


def ensure_voice_variants_table() -> None:
    """Create per-language voice variant storage if the DB has not migrated yet."""
    execute(
        """
        CREATE TABLE IF NOT EXISTS `elevenlabs_voice_variants` (
          `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
          `voice_id` VARCHAR(64) NOT NULL,
          `name` VARCHAR(255) NOT NULL,
          `gender` VARCHAR(32) DEFAULT NULL,
          `age` VARCHAR(32) DEFAULT NULL,
          `language` VARCHAR(32) NOT NULL,
          `accent` VARCHAR(64) DEFAULT NULL,
          `category` VARCHAR(64) DEFAULT NULL,
          `descriptive` VARCHAR(255) DEFAULT NULL,
          `use_case` VARCHAR(128) DEFAULT NULL,
          `preview_url` TEXT DEFAULT NULL,
          `audio_embedding` MEDIUMBLOB DEFAULT NULL,
          `labels_json` JSON DEFAULT NULL,
          `public_owner_id` VARCHAR(128) DEFAULT NULL,
          `synced_at` DATETIME NOT NULL,
          `updated_at` DATETIME NOT NULL,
          UNIQUE KEY `uq_voice_language` (`voice_id`, `language`),
          KEY `idx_language` (`language`),
          KEY `idx_gender_language` (`gender`, `language`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def fetch_shared_voices_page(
    api_key: str,
    page: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
    language: Optional[str] = None,
    gender: Optional[str] = None,
    category: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool, int]:
    """抓取一页共享音色。返回 (voices, has_more, total_count)。

    page: 0-based。ElevenLabs API 用 page 整数参数翻页，不是 next_page_token。
    total_count: 该 filter 下的远端总量（每次请求都会返回；首次请求取值写 stats 表）。
    """
    headers = {"xi-api-key": api_key}
    params: Dict[str, Any] = {"page": int(page), "page_size": int(page_size)}
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
    total_count = int(data.get("total_count") or 0)
    return voices, has_more, total_count


def upsert_voice(voice: Dict[str, Any]) -> None:
    """将单条音色写入（或更新）elevenlabs_voices 表。

    兼容两种 API 响应格式：
    - 新版：所有字段（use_case/accent/age/descriptive/gender/language）都在顶层
    - 旧版：嵌套在 `labels` 对象里
    labels_json 列存储整条原始 voice dict，便于未来扩展（verified_languages 等）。
    """
    labels = voice.get("labels") or {}
    now = datetime.utcnow()
    execute(
        """
        INSERT INTO elevenlabs_voices
          (voice_id, name, gender, age, language, accent, category,
           descriptive, use_case, preview_url, labels_json, public_owner_id,
           synced_at, updated_at)
        VALUES
          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          name=VALUES(name), gender=VALUES(gender), age=VALUES(age),
          language=VALUES(language), accent=VALUES(accent),
          category=VALUES(category), descriptive=VALUES(descriptive),
          use_case=VALUES(use_case), preview_url=VALUES(preview_url),
          labels_json=VALUES(labels_json), public_owner_id=VALUES(public_owner_id),
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
            voice.get("use_case") or labels.get("use_case"),
            voice.get("preview_url"),
            json.dumps(voice, ensure_ascii=False),
            voice.get("public_owner_id"),
            now,
            now,
        ),
    )


def _verified_languages(voice: Dict[str, Any]) -> list[dict]:
    values = voice.get("verified_languages") or []
    return [item for item in values if isinstance(item, dict)]


def _pick_language_variant(
    voice: Dict[str, Any],
    language: str,
) -> Optional[Dict[str, Any]]:
    language = (language or "").strip().lower()
    if not language:
        return None

    matches = [
        item for item in _verified_languages(voice)
        if (item.get("language") or "").strip().lower() == language
    ]
    if matches:
        preferred_models = {
            "eleven_turbo_v2_5": 0,
            "eleven_multilingual_v2": 1,
            "eleven_flash_v2_5": 2,
            "eleven_v2_5_flash": 3,
        }
        matches.sort(key=lambda item: preferred_models.get(item.get("model_id"), 99))
        return matches[0]

    if (voice.get("language") or "").strip().lower() == language:
        return {
            "language": language,
            "accent": voice.get("accent") or (voice.get("labels") or {}).get("accent"),
            "preview_url": voice.get("preview_url"),
        }
    return None


def upsert_voice_variant(voice: Dict[str, Any], language: str) -> bool:
    """Upsert one target-language variant for a shared voice.

    ElevenLabs may return voices whose top-level language is `en`/`es` but whose
    `verified_languages` includes `nl`, `sv`, `fi`, etc.  The variants table keeps
    that per-language preview and embedding without moving the base voice row.
    """
    variant = _pick_language_variant(voice, language)
    if not variant:
        return False

    labels = voice.get("labels") or {}
    now = datetime.utcnow()
    target_language = (language or "").strip().lower()
    preview_url = variant.get("preview_url") or voice.get("preview_url")
    accent = variant.get("accent") or voice.get("accent") or labels.get("accent")
    payload = dict(voice)
    payload["sync_language"] = target_language
    payload["language_variant"] = variant

    execute(
        """
        INSERT INTO elevenlabs_voice_variants
          (voice_id, name, gender, age, language, accent, category,
           descriptive, use_case, preview_url, labels_json, public_owner_id,
           synced_at, updated_at)
        VALUES
          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          name=VALUES(name), gender=VALUES(gender), age=VALUES(age),
          accent=VALUES(accent), category=VALUES(category),
          descriptive=VALUES(descriptive), use_case=VALUES(use_case),
          preview_url=VALUES(preview_url), labels_json=VALUES(labels_json),
          public_owner_id=VALUES(public_owner_id), synced_at=VALUES(synced_at)
        """,
        (
            voice["voice_id"],
            voice.get("name") or "",
            voice.get("gender") or labels.get("gender"),
            voice.get("age") or labels.get("age"),
            target_language,
            accent,
            voice.get("category"),
            voice.get("descriptive") or labels.get("descriptive"),
            voice.get("use_case") or labels.get("use_case"),
            preview_url,
            json.dumps(payload, ensure_ascii=False),
            voice.get("public_owner_id"),
            now,
            now,
        ),
    )
    return True


def sync_all_shared_voices(
    api_key: str,
    *,
    language: Optional[str] = None,
    gender: Optional[str] = None,
    category: Optional[str] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_voices: Optional[int] = None,
    on_page: Optional[Callable[[int, List[Dict[str, Any]]], None]] = None,
    on_total_count: Optional[Callable[[int], None]] = None,
) -> int:
    """翻页 upsert 到数据库。返回实际 upsert 的条目数。

    - page 用 0-based 整数递增（ElevenLabs API 真实分页方式）。
    - max_voices 达到即 break，超量不再 upsert。
    - on_total_count：仅在 page_index=0 时回调一次，传远端 total_count。
    - on_page：每处理完一页后回调 (page_index, voices)。回调异常仅记 warning。
    """
    total = 0
    page_index = 0
    while True:
        voices, has_more, total_count = fetch_shared_voices_page(
            api_key=api_key,
            page=page_index,
            page_size=page_size,
            language=language,
            gender=gender,
            category=category,
        )
        if page_index == 0 and on_total_count is not None:
            try:
                on_total_count(total_count)
            except Exception as exc:
                log.warning("on_total_count callback failed: %s", exc)

        reached_cap = False
        for voice in voices:
            if not voice.get("voice_id"):
                continue
            upsert_voice(voice)
            total += 1
            if max_voices is not None and total >= max_voices:
                reached_cap = True
                break

        if on_page is not None:
            try:
                on_page(page_index, voices)
            except Exception as exc:
                log.warning("on_page callback failed at page %s: %s",
                            page_index, exc)

        if reached_cap:
            break
        if not has_more:
            break
        page_index += 1
    return total


def sync_shared_voice_variants(
    api_key: str,
    *,
    language: str,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_voices: Optional[int] = None,
    on_page: Optional[Callable[[int, List[Dict[str, Any]]], None]] = None,
    on_total_count: Optional[Callable[[int], None]] = None,
) -> int:
    """Sync voices that are usable for a target language into variants storage."""
    total = 0
    page_index = 0
    while True:
        voices, has_more, total_count = fetch_shared_voices_page(
            api_key=api_key,
            page=page_index,
            page_size=page_size,
            language=language,
        )
        if page_index == 0 and on_total_count is not None:
            try:
                on_total_count(total_count)
            except Exception as exc:
                log.warning("on_total_count callback failed: %s", exc)

        reached_cap = False
        for voice in voices:
            if not voice.get("voice_id"):
                continue
            upsert_voice(voice)
            if upsert_voice_variant(voice, language):
                total += 1
            if max_voices is not None and total >= max_voices:
                reached_cap = True
                break

        if on_page is not None:
            try:
                on_page(page_index, voices)
            except Exception as exc:
                log.warning("on_page callback failed at page %s: %s",
                            page_index, exc)

        if reached_cap:
            break
        if not has_more:
            break
        page_index += 1
    return total


def _list_voices_without_embedding(
    limit: Optional[int] = None,
    language: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """查询尚未回写 embedding 的音色；可按 language 过滤。"""
    sql = (
        "SELECT voice_id, preview_url FROM elevenlabs_voices "
        "WHERE preview_url IS NOT NULL AND audio_embedding IS NULL"
    )
    params: tuple = ()
    if language:
        sql += " AND language = %s"
        params = (language,)
    if limit:
        sql += f" LIMIT {int(limit)}"
    return query(sql, params) if params else query(sql)


def _list_voice_variants_without_embedding(
    limit: Optional[int] = None,
    language: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sql = (
        "SELECT voice_id, language, preview_url FROM elevenlabs_voice_variants "
        "WHERE preview_url IS NOT NULL AND audio_embedding IS NULL"
    )
    params: list[Any] = []
    if language:
        sql += " AND language = %s"
        params.append(language)
    if limit:
        sql += f" LIMIT {int(limit)}"
    return query(sql, tuple(params)) if params else query(sql)


def _download_preview(url: str, dest_path) -> str:
    """下载 preview 音频到 dest_path（pathlib.Path 或 str）。"""
    for attempt in range(DOWNLOAD_RETRIES):
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException:
            if attempt + 1 >= DOWNLOAD_RETRIES:
                raise
            time.sleep(DOWNLOAD_RETRY_SLEEP_SECONDS * (attempt + 1))
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


def _update_variant_embedding(voice_id: str, language: str, blob: bytes) -> None:
    execute(
        "UPDATE elevenlabs_voice_variants SET audio_embedding=%s, updated_at=%s "
        "WHERE voice_id=%s AND language=%s",
        (blob, datetime.utcnow(), voice_id, language),
    )


def embed_missing_voices(
    cache_dir: str,
    limit: Optional[int] = None,
    on_progress: Optional[Callable[[int, int, str, bool], None]] = None,
    *,
    language: Optional[str] = None,
) -> int:
    """批量下载 preview_url 并生成 embedding，回写到数据库。

    单条失败不中断整批（日志 warning）。
    language 过滤：仅处理该语种的音色。None 时处理全部语种。
    on_progress(done_index, total, voice_id, ok)：每处理完一条后回调，
    done_index 从 1 开始。回调抛异常只记 warning，不中断批次。
    返回成功处理的条目数。
    """
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    rows = _list_voices_without_embedding(limit=limit, language=language)
    total_rows = len(rows)

    count = 0
    done_index = 0
    for row in rows:
        voice_id = row["voice_id"]
        url = row.get("preview_url")
        if not url:
            continue
        done_index += 1
        file_name = hashlib.sha1(voice_id.encode("utf-8")).hexdigest() + ".mp3"
        dest = cache_path / file_name
        ok = False
        try:
            _download_preview(url, dest)
            vec = embed_audio_file(str(dest))
            _update_embedding(voice_id, serialize_embedding(vec))
            count += 1
            ok = True
        except Exception as exc:
            # 容错：单条失败不影响批次
            log.warning("[embed_missing_voices] failed %s: %s", voice_id, exc)
        if on_progress is not None:
            try:
                on_progress(done_index, total_rows, voice_id, ok)
            except Exception as exc:
                log.warning(
                    "on_progress callback failed at %s: %s", voice_id, exc
                )
    return count


def embed_missing_voice_variants(
    cache_dir: str,
    limit: Optional[int] = None,
    on_progress: Optional[Callable[[int, int, str, bool], None]] = None,
    *,
    language: Optional[str] = None,
) -> int:
    """Download target-language previews and store per-language embeddings."""
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)

    rows = _list_voice_variants_without_embedding(limit=limit, language=language)
    total_rows = len(rows)

    count = 0
    done_index = 0
    for row in rows:
        voice_id = row["voice_id"]
        lang = row.get("language") or language or ""
        url = row.get("preview_url")
        if not url:
            continue
        done_index += 1
        cache_key = f"{lang}:{voice_id}"
        file_name = hashlib.sha1(cache_key.encode("utf-8")).hexdigest() + ".mp3"
        dest = cache_path / file_name
        ok = False
        try:
            _download_preview(url, dest)
            vec = embed_audio_file(str(dest))
            _update_variant_embedding(voice_id, lang, serialize_embedding(vec))
            count += 1
            ok = True
        except Exception as exc:
            log.warning("[embed_missing_voice_variants] failed %s/%s: %s",
                        lang, voice_id, exc)
        if on_progress is not None:
            try:
                on_progress(done_index, total_rows, voice_id, ok)
            except Exception as exc:
                log.warning(
                    "on_progress callback failed at %s: %s", voice_id, exc
                )
    return count


def upsert_library_stats(language: str, total_available: int) -> None:
    """写入/更新某语种的远端共享库总量（来自 API total_count）。"""
    execute(
        """
        INSERT INTO elevenlabs_voice_library_stats
          (language, total_available, last_counted_at)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
          total_available=VALUES(total_available),
          last_counted_at=VALUES(last_counted_at)
        """,
        (language, int(total_available), datetime.utcnow()),
    )
