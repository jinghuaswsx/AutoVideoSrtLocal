import os
import subprocess
from typing import List, Dict

from elevenlabs.client import ElevenLabs
from config import ELEVENLABS_API_KEY
from pipeline.voice_library import get_voice_library

_client: ElevenLabs = None


def _get_client() -> ElevenLabs:
    global _client
    if _client is None:
        _client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
    return _client


def load_voices() -> List[Dict]:
    return get_voice_library().list_voices()


def get_default_voice(gender: str = "male") -> Dict:
    return get_voice_library().get_default_voice(gender)


def get_voice_by_id(voice_id: str) -> Dict | None:
    return get_voice_library().get_voice(voice_id)


def generate_segment_audio(text: str, voice_id: str, output_path: str) -> str:
    """生成单段音频，返回文件路径（mp3）"""
    client = _get_client()
    audio = client.text_to_speech.convert(
        text=text,
        voice_id=voice_id,
        model_id="eleven_turbo_v2_5",
        output_format="mp3_44100_128",
    )
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)
    return output_path


def generate_full_audio(segments: List[Dict], voice_id: str, output_dir: str) -> Dict:
    """
    为所有翻译段落生成音频并拼接成完整音轨

    Returns:
        {"full_audio_path": str, "segments": [...]}  # 每段新增 tts_path, tts_duration
    """
    seg_dir = os.path.join(output_dir, "tts_segments")
    os.makedirs(seg_dir, exist_ok=True)

    updated_segments = []
    concat_list_path = os.path.join(seg_dir, "concat.txt")

    with open(concat_list_path, "w", encoding="utf-8") as concat_f:
        for i, seg in enumerate(segments):
            text = seg.get("translated", seg.get("text", ""))
            seg_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")

            generate_segment_audio(text, voice_id, seg_path)
            duration = _get_audio_duration(seg_path)

            seg_copy = dict(seg)
            seg_copy["tts_path"] = seg_path
            seg_copy["tts_duration"] = duration
            updated_segments.append(seg_copy)

            concat_f.write(f"file '{os.path.abspath(seg_path)}'\n")

    full_audio_path = os.path.join(output_dir, "tts_full.mp3")
    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list_path, "-c", "copy", full_audio_path],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频拼接失败: {result.stderr}")

    return {"full_audio_path": full_audio_path, "segments": updated_segments}


def _get_audio_duration(audio_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0
