from pathlib import Path


SCRIPT = Path("web/static/js/video_ai_review.js").read_text(encoding="utf-8")


def test_video_ai_review_escapes_model_score_fields_before_rendering_html():
    assert "function _scoreText(value)" in SCRIPT
    assert "function _verdictTier(verdict)" in SCRIPT

    assert "const tier = _verdictTier(r.verdict);" in SCRIPT
    assert '<b>${_esc(_scoreText(r.overall_score))}</b>' in SCRIPT
    assert '<span class="vr-dim-score">${_esc(_scoreText(v))}</span>' in SCRIPT
    assert '<div class="vr-score-big">${_esc(_scoreText(r.overall_score))}</div>' in SCRIPT
    assert (
        "const display = v == null ? \"<span class='vr-meta'>跳过 / 无数据</span>\" "
        ": `<b>${_esc(_scoreText(v))}</b>`;"
    ) in SCRIPT

    assert 'const tier = VERDICT_TIER[r.verdict] || "";' not in SCRIPT
    assert '<b>${r.overall_score ?? "—"}</b>' not in SCRIPT
    assert '<span class="vr-dim-score">${v}</span>' not in SCRIPT
    assert '<div class="vr-score-big">${r.overall_score ?? "—"}</div>' not in SCRIPT
    assert "`<b>${v}</b>`" not in SCRIPT
