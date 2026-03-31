import re


def _normalize_token(token: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", token.lower())


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

    for index, chunk in enumerate(subtitle_chunks):
        target_tokens = [_normalize_token(token) for token in chunk.get("text", "").split()]
        target_tokens = [token for token in target_tokens if token]
        matched = []

        for token in target_tokens:
            while cursor < len(words) and words[cursor]["normalized"] != token:
                cursor += 1
            if cursor < len(words):
                matched.append(words[cursor])
                cursor += 1

        if matched:
            start_time = matched[0]["start_time"]
            end_time = matched[-1]["end_time"]
        else:
            start_time = round((index / chunk_count) * total_duration, 3)
            end_time = round(((index + 1) / chunk_count) * total_duration, 3)

        aligned.append(
            {
                **chunk,
                "start_time": start_time,
                "end_time": end_time,
                "source_asr_text": " ".join(word["text"] for word in matched),
            }
        )

    return aligned
