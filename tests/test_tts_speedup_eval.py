"""tts_speedup_eval orchestrator 测试。
覆盖：
- run_evaluation 写 pending 行 → 调 invoke_generate → 写 ok 行
- LLM 抛异常时写 failed 行，不向上抛
- LLM 超过 timeout 时写 failed 行
- retry_evaluation 重跑只更新 score / model / status，不动 audio 路径
"""
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture(autouse=True)
def _stub_db(monkeypatch):
    """把 appcore.tts_speedup_eval 内部用的 db 调用 stub 掉。"""
    written = {"rows": []}

    def fake_execute(sql, params=None):
        written["rows"].append({"sql": sql, "params": params})
        return MagicMock(lastrowid=42)

    def fake_query_one(sql, params=None):
        # 重跑测试用：返回一行 fake 数据
        return {
            "id": 42, "task_id": "t1", "round_index": 2, "language": "es",
            "audio_pre_path": "tts_full.round_2.mp3",
            "audio_post_path": "tts_full.round_2.speedup.mp3",
            "video_duration": 60.0, "audio_pre_duration": 64.0,
            "audio_post_duration": 60.5, "speed_ratio": 1.0667,
            "hit_final_range": 1, "status": "failed",
        }

    monkeypatch.setattr("appcore.tts_speedup_eval.db_execute", fake_execute, raising=False)
    monkeypatch.setattr("appcore.tts_speedup_eval.db_query_one", fake_query_one, raising=False)
    return written


def _llm_ok():
    return {
        "json": {
            "score_naturalness": 4,
            "score_pacing": 3,
            "score_timbre": 5,
            "score_intelligibility": 5,
            "score_overall": 4,
            "summary": "整体可用，节奏轻微抖动",
            "flags": ["minor_pace_jitter"],
        },
        "usage": {"input_tokens": 1234, "output_tokens": 89, "cost_cny": 0.012},
    }


def test_run_evaluation_happy_path_writes_ok_row(tmp_path, _stub_db):
    from appcore import tts_speedup_eval

    # 创建 fake audio 文件
    pre = tmp_path / "pre.mp3"; pre.write_bytes(b"\xff\xfb\x10\x00")
    post = tmp_path / "post.mp3"; post.write_bytes(b"\xff\xfb\x10\x00")

    with patch("appcore.tts_speedup_eval.llm_client.invoke_generate", return_value=_llm_ok()):
        eval_id = tts_speedup_eval.run_evaluation(
            task_id="task-xyz", round_index=2, language="es",
            video_duration=60.0,
            audio_pre_path=str(pre), audio_pre_duration=64.0,
            audio_post_path=str(post), audio_post_duration=60.5,
            speed_ratio=1.0667, hit_final_range=True,
            user_id=1,
        )
    assert eval_id == 42
    sqls = [r["sql"] for r in _stub_db["rows"]]
    assert any("INSERT INTO tts_speedup_evaluations" in s for s in sqls)
    assert any("UPDATE tts_speedup_evaluations" in s and "status" in s for s in sqls)


def test_run_evaluation_llm_failure_writes_failed_row(tmp_path, _stub_db):
    from appcore import tts_speedup_eval

    pre = tmp_path / "pre.mp3"; pre.write_bytes(b"\xff\xfb\x10\x00")
    post = tmp_path / "post.mp3"; post.write_bytes(b"\xff\xfb\x10\x00")

    def boom(*args, **kwargs):
        raise RuntimeError("openrouter 502")

    with patch("appcore.tts_speedup_eval.llm_client.invoke_generate", side_effect=boom):
        eval_id = tts_speedup_eval.run_evaluation(
            task_id="task-xyz", round_index=2, language="es",
            video_duration=60.0,
            audio_pre_path=str(pre), audio_pre_duration=64.0,
            audio_post_path=str(post), audio_post_duration=60.5,
            speed_ratio=1.0667, hit_final_range=True,
            user_id=1,
        )
    assert eval_id == 42  # 仍然返回 ID，但 status=failed
    sqls = " ".join(r["sql"] for r in _stub_db["rows"])
    assert "INSERT INTO tts_speedup_evaluations" in sqls
    assert "UPDATE tts_speedup_evaluations" in sqls
    failed_update = [r for r in _stub_db["rows"]
                     if r["sql"].strip().startswith("UPDATE")]
    assert any("failed" in str(r["params"]) for r in failed_update)


def test_run_evaluation_timeout_writes_failed_row(tmp_path, _stub_db):
    """超过 EVAL_TIMEOUT_SECONDS 时写 failed 行，不向上抛。"""
    import time
    from appcore import tts_speedup_eval

    pre = tmp_path / "pre.mp3"; pre.write_bytes(b"\xff\xfb\x10\x00")
    post = tmp_path / "post.mp3"; post.write_bytes(b"\xff\xfb\x10\x00")

    def slow(*args, **kwargs):
        time.sleep(5)  # 超过测试用的小 timeout
        return _llm_ok()

    with patch("appcore.tts_speedup_eval.llm_client.invoke_generate", side_effect=slow), \
         patch("appcore.tts_speedup_eval.EVAL_TIMEOUT_SECONDS", 0.5):
        eval_id = tts_speedup_eval.run_evaluation(
            task_id="task-xyz", round_index=2, language="es",
            video_duration=60.0,
            audio_pre_path=str(pre), audio_pre_duration=64.0,
            audio_post_path=str(post), audio_post_duration=60.5,
            speed_ratio=1.0667, hit_final_range=True,
            user_id=1,
        )
    failed_rows = [r for r in _stub_db["rows"] if "UPDATE" in r["sql"]]
    assert any("failed" in str(r["params"]) for r in failed_rows)


def test_retry_evaluation_only_updates_scores_and_status(tmp_path, _stub_db):
    """retry 不重新写 audio 路径，只更新 score / model / status。"""
    from appcore import tts_speedup_eval

    with patch("appcore.tts_speedup_eval.llm_client.invoke_generate", return_value=_llm_ok()):
        ok = tts_speedup_eval.retry_evaluation(eval_id=42, user_id=1)
    assert ok is True
    update_rows = [r for r in _stub_db["rows"]
                   if r["sql"].strip().startswith("UPDATE")]
    assert update_rows
    # audio_pre_path / audio_post_path 不应在 UPDATE 字段里
    for r in update_rows:
        assert "audio_pre_path" not in r["sql"]
        assert "audio_post_path" not in r["sql"]
