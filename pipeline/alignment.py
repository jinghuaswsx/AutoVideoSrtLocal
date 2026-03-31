import re
from typing import Dict, List


try:
    from scenedetect import ContentDetector, SceneManager, open_video
except Exception:  # pragma: no cover - optional dependency
    ContentDetector = None
    SceneManager = None
    open_video = None


_BREAK_PUNCT = re.compile(r"[。！？!?；;.]$")


def detect_scene_cuts(video_path: str, threshold: float = 27.0) -> List[float]:
    if not (open_video and SceneManager and ContentDetector):
        return []

    try:
        video = open_video(video_path)
        manager = SceneManager()
        manager.add_detector(ContentDetector(threshold=threshold))
        manager.detect_scenes(video)
        return [round(scene[0].get_seconds(), 3) for scene in manager.get_scene_list()[1:]]
    except Exception:
        return []


def suggest_break_after(
    utterances: List[Dict],
    scene_cuts=None,
    min_pause_seconds: float = 0.9,
    scene_tolerance_seconds: float = 0.25,
) -> List[bool]:
    if not utterances:
        return []

    scene_cuts = scene_cuts or []
    suggested = []

    for index, utterance in enumerate(utterances):
        is_last = index == len(utterances) - 1
        current_end = float(utterance.get("end_time", 0.0))
        text = utterance.get("text", "").strip()

        break_here = is_last or bool(_BREAK_PUNCT.search(text))

        if not is_last:
            next_start = float(utterances[index + 1].get("start_time", current_end))
            if next_start - current_end >= min_pause_seconds:
                break_here = True

        if any(abs(cut - current_end) <= scene_tolerance_seconds for cut in scene_cuts):
            break_here = True

        suggested.append(break_here)

    suggested[-1] = True
    return suggested


def build_script_segments(utterances: List[Dict], break_after: List[bool]) -> List[Dict]:
    if not utterances:
        return []
    if len(utterances) != len(break_after):
        raise ValueError("break_after length must match utterances length")

    segments = []
    bucket = []
    segment_index = 0

    for idx, utterance in enumerate(utterances):
        bucket.append((idx, utterance))
        if not break_after[idx]:
            continue

        indices = [item[0] for item in bucket]
        items = [item[1] for item in bucket]
        words = []
        for item in items:
            words.extend(item.get("words", []))

        segments.append(
            {
                "index": segment_index,
                "text": _merge_texts([item.get("text", "") for item in items]),
                "start_time": float(items[0].get("start_time", 0.0)),
                "end_time": float(items[-1].get("end_time", 0.0)),
                "utterance_indices": indices,
                "words": words,
            }
        )
        bucket = []
        segment_index += 1

    if bucket:
        raise ValueError("break_after must terminate the final utterance")

    return segments


def compile_alignment(utterances: List[Dict], scene_cuts=None, break_after=None) -> Dict:
    scene_cuts = scene_cuts or []
    break_after = break_after or suggest_break_after(utterances, scene_cuts=scene_cuts)
    return {
        "scene_cuts": scene_cuts,
        "break_after": break_after,
        "script_segments": build_script_segments(utterances, break_after),
    }


def _merge_texts(parts: List[str]) -> str:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    if not cleaned:
        return ""
    if any(re.search(r"[A-Za-z0-9]", part) for part in cleaned):
        return " ".join(cleaned)
    return "".join(cleaned)
