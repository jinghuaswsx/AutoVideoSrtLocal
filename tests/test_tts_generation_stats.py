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


def test_finalize_writes_state_json_db_and_logger(monkeypatch, caplog):
    """finalize 应该：1) update task_state；2) upsert DB；3) logger.info 蓝色总结。"""
    from appcore import tts_generation_stats as stats_mod

    state_updates: dict = {}

    def fake_task_state_update(task_id, **fields):
        state_updates[task_id] = fields

    db_calls: list[tuple] = []

    def fake_execute(sql, args=None):
        db_calls.append((sql, args or ()))
        return 1

    monkeypatch.setattr(stats_mod, "task_state_update", fake_task_state_update)
    monkeypatch.setattr(stats_mod, "execute", fake_execute)

    rounds = [
        {"audio_segments_total": 9},
        {"rewrite_attempts": [1, 2, 3], "audio_segments_total": 9},
    ]
    task = {
        "type": "multi_translate",
        "target_lang": "it",
        "user_id": 77,
    }

    import logging
    caplog.set_level(logging.INFO, logger="appcore.tts_generation_stats")

    stats_mod.finalize(task_id="task-z", task=task, rounds=rounds)

    assert state_updates["task-z"]["tts_generation_summary"]["translate_calls"] == 4
    assert state_updates["task-z"]["tts_generation_summary"]["audio_calls"] == 18
    assert "finished_at" in state_updates["task-z"]["tts_generation_summary"]
    assert len(db_calls) == 1
    assert db_calls[0][1][0] == "task-z"
    assert db_calls[0][1][4] == 4
    assert db_calls[0][1][5] == 18
    log_record = next((r for r in caplog.records if "次翻译" in r.message), None)
    assert log_record is not None
    assert "4 次翻译" in log_record.message
    assert "18 次语音生成" in log_record.message


def test_finalize_swallows_db_error_but_keeps_state_json_and_log(monkeypatch, caplog):
    """DB 写失败不应让 _step_tts 整个崩溃。"""
    from appcore import tts_generation_stats as stats_mod

    state_updates: dict = {}
    monkeypatch.setattr(
        stats_mod, "task_state_update",
        lambda task_id, **f: state_updates.setdefault(task_id, f),
    )

    def boom_execute(sql, args=None):
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr(stats_mod, "execute", boom_execute)

    import logging
    caplog.set_level(logging.INFO, logger="appcore.tts_generation_stats")

    rounds = [{"audio_segments_total": 5}]
    task = {"type": "fr_translate", "target_lang": "fr", "user_id": None}

    stats_mod.finalize(task_id="task-err", task=task, rounds=rounds)

    assert "task-err" in state_updates
    assert any("次翻译" in r.message for r in caplog.records)
    assert any("DB unreachable" in r.message and r.levelname == "WARNING"
               for r in caplog.records)
