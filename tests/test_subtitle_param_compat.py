from pipeline.subtitle import (
    wrap_text, format_subtitle_chunk_text, build_srt_from_chunks,
    apply_french_punctuation, apply_punctuation_spacing,
)


def test_wrap_text_default_still_42():
    # 28 chars — well within the default max_chars=42, must not be wrapped
    text = "Hello world this fits easily"
    out = wrap_text(text)
    assert "\n" not in out


def test_wrap_text_with_custom_max_chars_de():
    text = "Das ist ein relativ langer deutscher Satz mit vielen Worten"
    out = wrap_text(text, max_chars=38)
    assert "\n" in out
    for line in out.split("\n"):
        assert len(line) <= 38


def test_format_subtitle_chunk_text_accepts_weak_starters():
    text = "Ein schöner Tag und ein neuer Anfang für alle"
    out = format_subtitle_chunk_text(text, weak_boundary_words={"und", "für"})
    assert "\n" in out
    lines = out.split("\n")
    for line in lines:
        first_word = line.split()[0].lower().strip(",")
        assert first_word not in {"und", "für"}


def test_apply_french_punctuation_backward_compat():
    srt = "1\n00:00:00,000 --> 00:00:01,000\nBonjour ?\n"
    out = apply_french_punctuation(srt)
    assert "Bonjour\u00A0?" in out


def test_apply_punctuation_spacing_generic():
    srt = "1\n00:00:00,000 --> 00:00:01,000\nHola : amigos !\n"
    rules = {"nbsp_before": ["?", "!", ":"], "guillemets": False}
    out = apply_punctuation_spacing(srt, rules)
    assert "Hola\u00A0:" in out
    assert "amigos\u00A0!" in out


def test_build_srt_from_chunks_still_works_with_default():
    chunks = [{"text": "Hello world this is a test", "start_time": 0.0, "end_time": 1.0}]
    out = build_srt_from_chunks(chunks)
    assert "00:00:00,000 --> 00:00:01,000" in out
