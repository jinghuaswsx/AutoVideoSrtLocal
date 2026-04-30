"""tts_generation_stats utils 测试。

聚焦 compute_summary 的口径正确性、format_log_line 的 ANSI 输出、
upsert 的 UPSERT 行为，以及 finalize 在异常路径下的容错。
"""
from __future__ import annotations

import pytest


def _round(rewrite_attempts: int = 0, audio_segments: int = 0) -> dict:
    """构造一个最简的 round_record。round 1 没有 rewrite_attempts。"""
    rec: dict = {"audio_segments_total": audio_segments}
    if rewrite_attempts > 0:
        rec["rewrite_attempts"] = [{"attempt": i + 1} for i in range(rewrite_attempts)]
    return rec


def test_compute_summary_round1_only_initial_translate():
    from appcore.tts_generation_stats import compute_summary
    rounds = [_round(rewrite_attempts=0, audio_segments=9)]
    summary = compute_summary(rounds)
    assert summary["translate_calls"] == 1
    assert summary["audio_calls"] == 9


def test_compute_summary_multi_round_aggregates_rewrite_and_segments():
    from appcore.tts_generation_stats import compute_summary
    rounds = [
        _round(rewrite_attempts=0, audio_segments=9),
        _round(rewrite_attempts=2, audio_segments=9),
        _round(rewrite_attempts=5, audio_segments=10),
    ]
    summary = compute_summary(rounds)
    assert summary["translate_calls"] == 8
    assert summary["audio_calls"] == 28


def test_compute_summary_handles_missing_audio_segments_total():
    from appcore.tts_generation_stats import compute_summary
    rounds = [{"rewrite_attempts": []}]
    summary = compute_summary(rounds)
    assert summary["translate_calls"] == 1
    assert summary["audio_calls"] == 0


def test_compute_summary_empty_rounds():
    from appcore.tts_generation_stats import compute_summary
    summary = compute_summary([])
    assert summary["translate_calls"] == 0
    assert summary["audio_calls"] == 0


def test_format_log_line_contains_counts_and_ansi():
    from appcore.tts_generation_stats import format_log_line
    line = format_log_line({"translate_calls": 4, "audio_calls": 27})
    assert "\033[1;34m" in line
    assert "\033[0m" in line
    assert "4 次翻译" in line
    assert "27 次语音生成" in line


def test_format_log_line_zero_counts():
    from appcore.tts_generation_stats import format_log_line
    line = format_log_line({"translate_calls": 0, "audio_calls": 0})
    assert "0 次翻译" in line
    assert "0 次语音生成" in line


def test_upsert_inserts_then_updates(monkeypatch):
    """同一个 task_id 上调两次 upsert，第二次应更新而非重复插入。"""
    from appcore import tts_generation_stats as stats_mod

    captured: list[tuple] = []

    def fake_execute(sql, args=None):
        captured.append((sql, args or ()))
        return 1

    monkeypatch.setattr(stats_mod, "execute", fake_execute)

    stats_mod.upsert(
        task_id="task-x",
        project_type="multi_translate",
        target_lang="it",
        user_id=42,
        summary={"translate_calls": 3, "audio_calls": 18},
        finished_at_iso="2026-04-30T10:00:00",
    )
    stats_mod.upsert(
        task_id="task-x",
        project_type="multi_translate",
        target_lang="it",
        user_id=42,
        summary={"translate_calls": 5, "audio_calls": 27},
        finished_at_iso="2026-04-30T10:05:00",
    )

    assert len(captured) == 2
    sql0, args0 = captured[0]
    sql_norm = " ".join(sql0.split()).upper()
    assert "INSERT INTO TTS_GENERATION_STATS" in sql_norm
    assert "ON DUPLICATE KEY UPDATE" in sql_norm
    assert args0[0] == "task-x"
    assert args0[1] == "multi_translate"
    assert args0[2] == "it"
    assert args0[3] == 42
    assert args0[4] == 3
    assert args0[5] == 18
    assert args0[6] == "2026-04-30T10:00:00"


def test_upsert_handles_null_user_id(monkeypatch):
    from appcore import tts_generation_stats as stats_mod
    captured: list[tuple] = []
    monkeypatch.setattr(stats_mod, "execute",
                        lambda sql, args=None: captured.append((sql, args or ())) or 1)

    stats_mod.upsert(
        task_id="task-y",
        project_type="ja_translate",
        target_lang="ja",
        user_id=None,
        summary={"translate_calls": 1, "audio_calls": 5},
        finished_at_iso="2026-04-30T11:00:00",
    )

    assert captured[0][1][3] is None
