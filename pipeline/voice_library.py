import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from config import VOICES_FILE


class VoiceLibrary:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or os.getenv("VOICES_FILE") or VOICES_FILE)

    def list_voices(self) -> List[Dict]:
        return self._read()["voices"]

    def get_voice(self, voice_id: str) -> Optional[Dict]:
        for voice in self.list_voices():
            if voice.get("id") == voice_id:
                return voice
        return None

    def get_default_voice(self, gender: str = "male") -> Optional[Dict]:
        key = "is_default_male" if gender == "male" else "is_default_female"
        voices = self.list_voices()
        for voice in voices:
            if voice.get(key):
                return voice
        for voice in voices:
            if voice.get("gender") == gender:
                return voice
        return voices[0] if voices else None

    def create_voice(self, payload: Dict) -> Dict:
        data = self._read()
        voice = self._normalize_voice(payload, existing_id=None)
        if any(item["id"] == voice["id"] for item in data["voices"]):
            raise ValueError(f"Voice '{voice['id']}' already exists")
        data["voices"].append(voice)
        data["voices"] = self._enforce_defaults(data["voices"])
        self._write(data)
        return voice

    def update_voice(self, voice_id: str, payload: Dict) -> Dict:
        data = self._read()
        for idx, existing in enumerate(data["voices"]):
            if existing["id"] != voice_id:
                continue
            updated = dict(existing)
            updated.update(payload)
            updated["id"] = voice_id
            data["voices"][idx] = self._normalize_voice(updated, existing_id=voice_id)
            data["voices"] = self._enforce_defaults(data["voices"])
            self._write(data)
            return data["voices"][idx]
        raise KeyError(voice_id)

    def delete_voice(self, voice_id: str):
        data = self._read()
        data["voices"] = [voice for voice in data["voices"] if voice.get("id") != voice_id]
        self._write(data)

    def recommend_voice(self, text: str) -> Optional[Dict]:
        voices = self.list_voices()
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
            haystack = " ".join(
                [
                    voice.get("name", ""),
                    voice.get("description", ""),
                    " ".join(voice.get("style_tags", [])),
                ]
            ).lower()
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

        return best_voice or self.get_default_voice("male")

    def _normalize_voice(self, payload: Dict, existing_id: str | None) -> Dict:
        name = (payload.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        gender = (payload.get("gender") or "").strip().lower()
        if gender not in {"male", "female"}:
            raise ValueError("gender must be 'male' or 'female'")
        elevenlabs_voice_id = (payload.get("elevenlabs_voice_id") or "").strip()
        if not elevenlabs_voice_id:
            raise ValueError("elevenlabs_voice_id is required")

        result = {
            "id": existing_id or _slugify(name),
            "name": name,
            "gender": gender,
            "elevenlabs_voice_id": elevenlabs_voice_id,
            "description": (payload.get("description") or "").strip(),
            "style_tags": list(payload.get("style_tags") or []),
            "is_default_male": bool(payload.get("is_default_male", False)),
            "is_default_female": bool(payload.get("is_default_female", False)),
        }
        # Optional fields for imported voices
        for key in ("source", "source_voice_id", "source_public_user_id", "preview_url"):
            val = payload.get(key)
            if val:
                result[key] = str(val).strip()
        if payload.get("labels") and isinstance(payload["labels"], dict):
            result["labels"] = payload["labels"]
        return result

    def _read(self) -> Dict:
        if not self.path.exists():
            return {"voices": []}
        with open(self.path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _write(self, payload: Dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    def _enforce_defaults(self, voices: List[Dict]) -> List[Dict]:
        male_default_seen = False
        female_default_seen = False
        normalized = []
        for voice in voices:
            voice = dict(voice)
            if voice.get("is_default_male"):
                voice["is_default_male"] = voice.get("gender") == "male" and not male_default_seen
                male_default_seen = male_default_seen or voice["is_default_male"]
            else:
                voice["is_default_male"] = False
            if voice.get("is_default_female"):
                voice["is_default_female"] = voice.get("gender") == "female" and not female_default_seen
                female_default_seen = female_default_seen or voice["is_default_female"]
            else:
                voice["is_default_female"] = False
            normalized.append(voice)

        males = [voice for voice in normalized if voice.get("gender") == "male"]
        females = [voice for voice in normalized if voice.get("gender") == "female"]
        if males and not any(voice.get("is_default_male") for voice in normalized):
            males[0]["is_default_male"] = True
        if females and not any(voice.get("is_default_female") for voice in normalized):
            females[0]["is_default_female"] = True
        return normalized


def get_voice_library() -> VoiceLibrary:
    return VoiceLibrary()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "voice"
