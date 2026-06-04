import re


def _normalize_token(token: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", token.lower())


def _find_token(words: list[dict], start: int, token: str) -> int | None:
    for position in range(start, len(words)):
        if words[position]["normalized"] == token:
            return position
    return None


def _float_time(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def align_subtitle_chunks_to_asr(
    subtitle_chunks: list[dict],
    asr_result: dict,
    total_duration: float,
) -> list[dict]:
    words = []
    for utterance in asr_result.get("utterances", []):
        for word in utterance.get("words", []):
            normalized = _normalize_token(word.get("text", ""))
            if normalized:
                words.append({**word, "normalized": normalized})

    aligned = []
    cursor = 0
    chunk_count = max(len(subtitle_chunks), 1)
    previous_end = 0.0

    for index, chunk in enumerate(subtitle_chunks):
        target_tokens = [_normalize_token(token) for token in chunk.get("text", "").split()]
        target_tokens = [token for token in target_tokens if token]
        matched = []
        local_cursor = cursor

        for token in target_tokens:
            match_position = _find_token(words, local_cursor, token)
            if match_position is None:
                continue
            matched.append(words[match_position])
            local_cursor = match_position + 1

        if matched:
            start_time = _float_time(matched[0].get("start_time"), previous_end)
            end_time = _float_time(matched[-1].get("end_time"), start_time)
            cursor = max(cursor, local_cursor)
        else:
            proportional_start = (index / chunk_count) * total_duration
            proportional_end = ((index + 1) / chunk_count) * total_duration
            start_time = max(previous_end, proportional_start)
            end_time = max(start_time + 0.001, proportional_end)
            if cursor < len(words):
                next_start = _float_time(words[cursor].get("start_time"), 0.0)
                if next_start > start_time:
                    end_time = min(end_time, next_start)

        if start_time < previous_end:
            start_time = previous_end
        if end_time <= start_time:
            end_time = start_time + 0.001
        start_time = round(start_time, 3)
        end_time = round(end_time, 3)
        previous_end = end_time

        aligned.append(
            {
                **chunk,
                "start_time": start_time,
                "end_time": end_time,
                "words": matched,
                "source_asr_text": " ".join(word["text"] for word in matched),
            }
        )

    return aligned
