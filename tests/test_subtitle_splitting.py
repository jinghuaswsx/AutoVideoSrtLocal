import re

from pipeline.languages import de as de_rules
from pipeline.subtitle import format_subtitle_chunk_text
from pipeline.subtitle_splitting import split_oversized_subtitle_chunks


GERMAN_LONG_TEXT = (
    "Deshalb sollte man öffentlichen Trinkwasserspendern wirklich nie "
    "vertrauen, besonders in der Öffentlichkeit."
)


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+(?:[-']\w+)*", text.lower(), flags=re.UNICODE)


def _timed_words(text: str, *, start: float, step: float) -> list[dict]:
    words = []
    cursor = start
    for token in text.split():
        end = round(cursor + step, 3)
        words.append({"text": token, "start_time": round(cursor, 3), "end_time": end})
        cursor = end
    return words


def _assert_fits_german_display(text: str) -> None:
    formatted = format_subtitle_chunk_text(
        text,
        weak_boundary_words=de_rules.WEAK_STARTERS,
        max_chars_per_line=de_rules.MAX_CHARS_PER_LINE,
        max_lines=de_rules.MAX_LINES,
    )
    lines = formatted.splitlines()
    assert len(lines) <= de_rules.MAX_LINES
    assert all(len(line) <= de_rules.MAX_CHARS_PER_LINE for line in lines)


def test_splitter_preserves_long_german_sentence_and_keeps_display_safe():
    chunks = [
        {
            "index": 0,
            "text": GERMAN_LONG_TEXT,
            "start_time": 0.1,
            "end_time": 8.5,
            "words": _timed_words(GERMAN_LONG_TEXT, start=0.1, step=0.7),
        }
    ]

    result = split_oversized_subtitle_chunks(
        chunks,
        weak_boundary_words=de_rules.WEAK_STARTERS,
        max_chars_per_line=de_rules.MAX_CHARS_PER_LINE,
        max_lines=de_rules.MAX_LINES,
        max_chars_per_second=de_rules.MAX_CHARS_PER_SECOND,
    )

    assert len(result) >= 2
    assert _tokens(" ".join(chunk["text"] for chunk in result)) == _tokens(GERMAN_LONG_TEXT)
    for chunk in result:
        _assert_fits_german_display(chunk["text"])
        duration = chunk["end_time"] - chunk["start_time"]
        assert duration > 0
        assert len(chunk["text"].rstrip(".!?;:,")) / duration <= de_rules.MAX_CHARS_PER_SECOND


def test_splitter_prefers_semantic_balance_over_filling_the_first_chunk():
    chunks = [
        {
            "index": 0,
            "text": GERMAN_LONG_TEXT,
            "start_time": 0.1,
            "end_time": 8.5,
            "words": _timed_words(GERMAN_LONG_TEXT, start=0.1, step=0.7),
        }
    ]

    result = split_oversized_subtitle_chunks(
        chunks,
        weak_boundary_words=de_rules.WEAK_STARTERS,
        max_chars_per_line=de_rules.MAX_CHARS_PER_LINE,
        max_lines=de_rules.MAX_LINES,
        max_chars_per_second=de_rules.MAX_CHARS_PER_SECOND,
    )

    assert result[0]["text"].endswith("Trinkwasserspendern")
    assert result[1]["text"].startswith("wirklich nie vertrauen")


def test_splitter_uses_word_timestamps_for_new_chunk_boundaries():
    chunks = [
        {
            "index": 0,
            "text": GERMAN_LONG_TEXT,
            "start_time": 0.1,
            "end_time": 8.5,
            "words": _timed_words(GERMAN_LONG_TEXT, start=0.1, step=0.7),
        }
    ]

    result = split_oversized_subtitle_chunks(
        chunks,
        weak_boundary_words=de_rules.WEAK_STARTERS,
        max_chars_per_line=de_rules.MAX_CHARS_PER_LINE,
        max_lines=de_rules.MAX_LINES,
        max_chars_per_second=de_rules.MAX_CHARS_PER_SECOND,
    )

    assert result[0]["start_time"] == chunks[0]["words"][0]["start_time"]
    assert result[-1]["end_time"] == chunks[0]["words"][-1]["end_time"]
    for previous, current in zip(result, result[1:]):
        assert previous["end_time"] <= current["start_time"]


def test_splitter_falls_back_to_proportional_timing_without_words():
    chunks = [
        {
            "index": 0,
            "text": GERMAN_LONG_TEXT,
            "start_time": 10.0,
            "end_time": 16.5,
        }
    ]

    result = split_oversized_subtitle_chunks(
        chunks,
        weak_boundary_words=de_rules.WEAK_STARTERS,
        max_chars_per_line=de_rules.MAX_CHARS_PER_LINE,
        max_lines=de_rules.MAX_LINES,
        max_chars_per_second=de_rules.MAX_CHARS_PER_SECOND,
    )

    assert len(result) >= 2
    assert result[0]["start_time"] == 10.0
    assert result[-1]["end_time"] == 16.5
    assert _tokens(" ".join(chunk["text"] for chunk in result)) == _tokens(GERMAN_LONG_TEXT)
    for previous, current in zip(result, result[1:]):
        assert previous["end_time"] <= current["start_time"]
        assert current["end_time"] > current["start_time"]
