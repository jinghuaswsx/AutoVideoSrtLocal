from typing import Dict, List


def build_timeline_manifest(script_segments: List[Dict], video_duration: float) -> Dict:
    timeline_segments = []
    video_cursor = 0.0
    timeline_cursor = 0.0

    for index, segment in enumerate(script_segments):
        duration = float(segment.get("tts_duration", 0.0) or 0.0)
        video_start = video_cursor
        video_end = min(video_duration, video_start + duration)
        actual_video_duration = max(video_end - video_start, 0.0)
        video_ranges = []
        if actual_video_duration > 0:
            video_ranges.append(
                {
                    "start": round(video_start, 3),
                    "end": round(video_end, 3),
                }
            )

        timeline_start = timeline_cursor
        timeline_end = timeline_cursor + duration

        timeline_segments.append(
            {
                "index": index,
                "text": segment.get("text", ""),
                "translated": segment.get("translated", segment.get("text", "")),
                "utterance_indices": segment.get("utterance_indices", []),
                "source_window": {
                    "start": round(float(segment.get("start_time", 0.0)), 3),
                    "end": round(float(segment.get("end_time", 0.0)), 3),
                },
                "tts_path": segment.get("tts_path"),
                "tts_duration": duration,
                "timeline_start": round(timeline_start, 3),
                "timeline_end": round(timeline_end, 3),
                "video_ranges": video_ranges,
                "video_truncated": actual_video_duration + 1e-6 < duration,
            }
        )

        video_cursor = video_end
        timeline_cursor = timeline_end

    return {
        "segments": timeline_segments,
        "total_tts_duration": round(timeline_cursor, 3),
        "video_consumed_duration": round(video_cursor, 3),
        "video_duration": round(float(video_duration), 3),
    }
