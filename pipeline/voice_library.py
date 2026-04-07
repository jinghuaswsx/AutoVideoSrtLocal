"""Voice library backed by user_voices database table."""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from appcore.db import query as db_query, execute as db_execute, query_one as db_query_one

_DEFAULT_VOICES = {
    "en": [
        {
            "name": "Adam",
            "gender": "male",
            "elevenlabs_voice_id": "pNInz6obpgDQGcFmaJgB",
            "description": "美式男声，自然有力，适合卖货展示类视频",
            "style_tags": ["energetic", "trustworthy", "casual"],
            "is_default": True,
        },
        {
            "name": "Rachel",
            "gender": "female",
            "elevenlabs_voice_id": "21m00Tcm4TlvDq8ikWAM",
            "description": "美式女声，亲切自然，适合美妆护肤生活类视频",
            "style_tags": ["warm", "friendly", "expressive"],
            "is_default": True,
        },
    ],
    "de": [
        {
            "name": "Toby",
            "gender": "male",
            "elevenlabs_voice_id": "eEmoQJhC4SAEQpCINUov",
            "description": "德语男声，友好自信，适合产品展示类视频",
            "style_tags": ["friendly", "confident", "german"],
            "is_default": True,
        },
        {
            "name": "Annika",
            "gender": "female",
            "elevenlabs_voice_id": "ViKqgJNeCiWZlYgHiAOO",
            "description": "德语女声，平静自信，适合生活类视频",
            "style_tags": ["calm", "confident", "german"],
            "is_default": True,
        },
    ],
    "fr": [
        {
            "name": "Antoine",
            "gender": "male",
            "elevenlabs_voice_id": "Xb7hH8MSUJpSbSDYk0k2",
            "description": "法语男声，年轻巴黎口音，适合旁白和叙述",
            "style_tags": ["young", "parisian", "french"],
            "is_default": True,
        },
        {
            "name": "Jeanne",
            "gender": "female",
            "elevenlabs_voice_id": "cgSgspJ2msm6clMCkdW9",
            "description": "法语女声，专业温暖，适合叙述类视频",
            "style_tags": ["professional", "warm", "french"],
            "is_default": True,
        },
    ],
}


class VoiceLibrary:
    def ensure_defaults(self, user_id: int, language: str = "en") -> None:
        """Insert default voices for a user+language if they have none."""
        existing = db_query(
            "SELECT id FROM user_voices WHERE user_id = %s AND language = %s LIMIT 1",
            (user_id, language),
        )
        if existing:
            return
        for voice in _DEFAULT_VOICES.get(language, []):
            db_execute(
                """INSERT INTO user_voices
                   (user_id, name, gender, elevenlabs_voice_id, language, description, style_tags, is_default, source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'manual')
                   ON DUPLICATE KEY UPDATE name=VALUES(name)""",
                (user_id, voice["name"], voice["gender"], voice["elevenlabs_voice_id"],
                 language, voice["description"], json.dumps(voice["style_tags"]), voice["is_default"]),
            )

    def list_voices(self, user_id: int, language: str = "en") -> List[Dict]:
        rows = db_query(
            "SELECT * FROM user_voices WHERE user_id = %s AND language = %s ORDER BY is_default DESC, created_at",
            (user_id, language),
        )
        return [_row_to_voice(r) for r in rows]

    def get_voice(self, voice_id: int, user_id: int) -> Optional[Dict]:
        row = db_query_one(
            "SELECT * FROM user_voices WHERE id = %s AND user_id = %s",
            (voice_id, user_id),
        )
        return _row_to_voice(row) if row else None

    def get_voice_by_elevenlabs_id(self, elevenlabs_voice_id: str, user_id: int) -> Optional[Dict]:
        row = db_query_one(
            "SELECT * FROM user_voices WHERE elevenlabs_voice_id = %s AND user_id = %s",
            (elevenlabs_voice_id, user_id),
        )
        return _row_to_voice(row) if row else None

    def get_default_voice(self, user_id: int, gender: str = "male", language: str = "en") -> Optional[Dict]:
        row = db_query_one(
            "SELECT * FROM user_voices WHERE user_id = %s AND language = %s AND gender = %s AND is_default = TRUE LIMIT 1",
            (user_id, language, gender),
        )
        if row:
            return _row_to_voice(row)
        row = db_query_one(
            "SELECT * FROM user_voices WHERE user_id = %s AND language = %s AND gender = %s LIMIT 1",
            (user_id, language, gender),
        )
        if row:
            return _row_to_voice(row)
        row = db_query_one(
            "SELECT * FROM user_voices WHERE user_id = %s AND language = %s LIMIT 1",
            (user_id, language),
        )
        return _row_to_voice(row) if row else None

    def create_voice(self, user_id: int, payload: Dict) -> Dict:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        gender = (payload.get("gender") or "").strip().lower()
        if gender not in ("male", "female"):
            raise ValueError("gender must be 'male' or 'female'")
        elevenlabs_voice_id = (payload.get("elevenlabs_voice_id") or "").strip()
        if not elevenlabs_voice_id:
            raise ValueError("elevenlabs_voice_id is required")

        language = payload.get("language", "en")
        row_id = db_execute(
            """INSERT INTO user_voices
               (user_id, name, gender, elevenlabs_voice_id, language, description, style_tags,
                preview_url, source, labels, is_default)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, name, gender, elevenlabs_voice_id, language,
             (payload.get("description") or "").strip(),
             json.dumps(payload.get("style_tags") or []),
             (payload.get("preview_url") or "").strip(),
             payload.get("source", "manual"),
             json.dumps(payload.get("labels") or {}),
             bool(payload.get("is_default", False))),
        )
        return self.get_voice(row_id, user_id)

    def update_voice(self, voice_id: int, user_id: int, payload: Dict) -> Dict:
        sets = []
        args = []
        for col in ("name", "gender", "description", "preview_url", "source", "language"):
            if col in payload:
                sets.append(f"{col} = %s")
                args.append(payload[col])
        if "style_tags" in payload:
            sets.append("style_tags = %s")
            args.append(json.dumps(payload["style_tags"]))
        if "labels" in payload:
            sets.append("labels = %s")
            args.append(json.dumps(payload["labels"]))
        if "is_default" in payload:
            sets.append("is_default = %s")
            args.append(bool(payload["is_default"]))
        if not sets:
            return self.get_voice(voice_id, user_id)
        args.extend([voice_id, user_id])
        db_execute(f"UPDATE user_voices SET {', '.join(sets)} WHERE id = %s AND user_id = %s", tuple(args))
        return self.get_voice(voice_id, user_id)

    def set_default_voice(self, voice_id: int, user_id: int) -> Optional[Dict]:
        """Set a single voice as the user's default within its language, clearing others."""
        voice = self.get_voice(voice_id, user_id)
        if not voice:
            return None
        lang = voice.get("language", "en")
        db_execute(
            "UPDATE user_voices SET is_default = FALSE WHERE user_id = %s AND language = %s",
            (user_id, lang),
        )
        db_execute("UPDATE user_voices SET is_default = TRUE WHERE id = %s AND user_id = %s", (voice_id, user_id))
        return self.get_voice(voice_id, user_id)

    def get_user_default_voice(self, user_id: int, language: str = "en") -> Optional[Dict]:
        """Get the user's single default voice for a language, or fall back to the first voice."""
        row = db_query_one(
            "SELECT * FROM user_voices WHERE user_id = %s AND language = %s AND is_default = TRUE LIMIT 1",
            (user_id, language),
        )
        if row:
            return _row_to_voice(row)
        row = db_query_one(
            "SELECT * FROM user_voices WHERE user_id = %s AND language = %s ORDER BY created_at LIMIT 1",
            (user_id, language),
        )
        return _row_to_voice(row) if row else None

    def delete_voice(self, voice_id: int, user_id: int) -> None:
        db_execute("DELETE FROM user_voices WHERE id = %s AND user_id = %s", (voice_id, user_id))

    def recommend_voice(self, user_id: int, text: str) -> Optional[Dict]:
        voices = self.list_voices(user_id)
        if not voices:
            return None
        normalized = text.lower()
        keyword_sets = {
            "beauty": ["beauty", "makeup", "skincare", "serum", "cream", "护肤", "精华", "面霜", "妆"],
            "tech": ["tech", "gadget", "drone", "tool", "电子", "科技", "无人机"],
            "warm": ["family", "mom", "baby", "soft", "亲和", "温柔", "宝宝"],
        }
        best_voice = None
        best_score = -1
        for voice in voices:
            haystack = " ".join([
                voice.get("name", ""),
                voice.get("description", ""),
                " ".join(voice.get("style_tags") or []),
            ]).lower()
            score = 0
            for tag, keywords in keyword_sets.items():
                if any(keyword in normalized for keyword in keywords):
                    if tag in haystack:
                        score += 2
                    if any(keyword in haystack for keyword in keywords):
                        score += 1
            if voice.get("gender") == "female" and any(keyword in normalized for keyword in keyword_sets["beauty"]):
                score += 3
            if voice.get("gender") == "male" and any(keyword in normalized for keyword in keyword_sets["tech"]):
                score += 2
            if score > best_score:
                best_score = score
                best_voice = voice
        return best_voice or self.get_default_voice(user_id, "male")


def _row_to_voice(row: dict) -> dict:
    """Convert a DB row to a voice dict."""
    voice = dict(row)
    if isinstance(voice.get("style_tags"), str):
        try:
            voice["style_tags"] = json.loads(voice["style_tags"])
        except (json.JSONDecodeError, TypeError):
            voice["style_tags"] = []
    if isinstance(voice.get("labels"), str):
        try:
            voice["labels"] = json.loads(voice["labels"])
        except (json.JSONDecodeError, TypeError):
            voice["labels"] = {}
    voice["is_default"] = bool(voice.get("is_default"))
    return voice


def get_voice_library() -> VoiceLibrary:
    return VoiceLibrary()
