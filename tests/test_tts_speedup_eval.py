"""tts_speedup_eval orchestrator 测试。
覆盖：
- run_evaluation 写 pending 行 → 调 invoke_generate → 写 ok 行
- LLM 抛异常时写 failed 行，不向上抛
- LLM 超过 timeout 时写 failed 行
- retry_evaluation 重跑只更新 score / model / status，不动 audio 路径
"""
from pathlib import Path
import shutil
import subprocess
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


def _fake_ffmpeg_concat(cmd, *args, **kwargs):
    out_path = Path(cmd[-1])
    out_path.write_bytes(b"combined-mp3")
    return subprocess.CompletedProcess(cmd, 0, "", "")


def _gen_tone(path: Path, *, freq: int) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency={freq}:duration=0.2",
            "-c:a", "libmp3lame",
            "-b:a", "64k",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_prepare_comparison_audio_builds_single_mp3_with_real_ffmpeg(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg required")

    from appcore import tts_speedup_eval

    pre = tmp_path / "pre.mp3"
    post = tmp_path / "post.mp3"
    _gen_tone(pre, freq=440)
    _gen_tone(post, freq=660)

    out = Path(tts_speedup_eval._prepare_comparison_audio(
        pre,
        post,
        round_index=3,
    ))

    assert out == tmp_path / "tts_speedup_eval.round_3.comparison.mp3"
    assert out.is_file()
    assert out.stat().st_size > 0


def test_run_evaluation_happy_path_writes_ok_row(tmp_path, _stub_db):
    from appcore import tts_speedup_eval

    # 创建 fake audio 文件
    pre = tmp_path / "pre.mp3"; pre.write_bytes(b"\xff\xfb\x10\x00")
    post = tmp_path / "post.mp3"; post.write_bytes(b"\xff\xfb\x10\x00")

    with patch("subprocess.run", side_effect=_fake_ffmpeg_concat), \
         patch("appcore.tts_speedup_eval.llm_client.invoke_generate",
               return_value=_llm_ok()) as m_invoke:
        eval_id = tts_speedup_eval.run_evaluation(
            task_id="task-xyz", round_index=2, language="es",
            video_duration=60.0,
            audio_pre_path=str(pre), audio_pre_duration=64.0,
            audio_post_path=str(post), audio_post_duration=60.5,
            speed_ratio=1.0667, hit_final_range=True,
            user_id=1,
        )
    assert eval_id == 42
    expected_media = str(pre.with_name("tts_speedup_eval.round_2.comparison.mp3"))
    invoke_kwargs = m_invoke.call_args.kwargs
    assert invoke_kwargs["provider_override"] == tts_speedup_eval.EVAL_PROVIDER
    assert invoke_kwargs["model_override"] == tts_speedup_eval.EVAL_MODEL
    assert invoke_kwargs["media"] == [expected_media]
    assert Path(expected_media).is_file()
    sqls = [r["sql"] for r in _stub_db["rows"]]
    assert any("INSERT INTO tts_speedup_evaluations" in s for s in sqls)
    assert any("UPDATE tts_speedup_evaluations" in s and "status" in s for s in sqls)


def test_run_evaluation_llm_failure_writes_failed_row(tmp_path, _stub_db):
    from appcore import tts_speedup_eval

    pre = tmp_path / "pre.mp3"; pre.write_bytes(b"\xff\xfb\x10\x00")
    post = tmp_path / "post.mp3"; post.write_bytes(b"\xff\xfb\x10\x00")

    def boom(*args, **kwargs):
        raise RuntimeError("openrouter 502")

    with patch("subprocess.run", side_effect=_fake_ffmpeg_concat), \
         patch("appcore.tts_speedup_eval.llm_client.invoke_generate", side_effect=boom):
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
        time.sleep(0.8)  # 超过测试用的小 timeout（0.5s），但调用方应在 ~0.5s 后立即返回
        return _llm_ok()

    with patch("subprocess.run", side_effect=_fake_ffmpeg_concat), \
         patch("appcore.tts_speedup_eval.llm_client.invoke_generate", side_effect=slow), \
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


def test_retry_evaluation_only_updates_scores_and_status(tmp_path, _stub_db, monkeypatch):
    """retry 不重新写 audio 路径，只更新 score / model / status。"""
    from appcore import tts_speedup_eval

    pre = tmp_path / "tts_full.round_2.mp3"
    post = tmp_path / "tts_full.round_2.speedup.mp3"
    pre.write_bytes(b"\xff\xfb\x10\x00")
    post.write_bytes(b"\xff\xfb\x10\x00")

    def fake_query_one(sql, params=None):
        return {
            "id": 42, "task_id": "t1", "round_index": 2, "language": "es",
            "audio_pre_path": str(pre),
            "audio_post_path": str(post),
            "video_duration": 60.0, "audio_pre_duration": 64.0,
            "audio_post_duration": 60.5, "speed_ratio": 1.0667,
            "hit_final_range": 1, "status": "failed",
        }

    monkeypatch.setattr("appcore.tts_speedup_eval.db_query_one", fake_query_one, raising=False)

    with patch("subprocess.run", side_effect=_fake_ffmpeg_concat), \
         patch("appcore.tts_speedup_eval.llm_client.invoke_generate",
               return_value=_llm_ok()) as m_invoke:
        ok = tts_speedup_eval.retry_evaluation(eval_id=42, user_id=1)
    assert ok is True
    invoke_kwargs = m_invoke.call_args.kwargs
    assert invoke_kwargs["provider_override"] == tts_speedup_eval.EVAL_PROVIDER
    assert invoke_kwargs["model_override"] == tts_speedup_eval.EVAL_MODEL
    assert invoke_kwargs["media"] == [
        str(pre.with_name("tts_speedup_eval.round_2.comparison.mp3"))
    ]
    update_rows = [r for r in _stub_db["rows"]
                   if r["sql"].strip().startswith("UPDATE")]
    assert update_rows
    # audio_pre_path / audio_post_path 不应在 UPDATE 字段里
    for r in update_rows:
        assert "audio_pre_path" not in r["sql"]
        assert "audio_post_path" not in r["sql"]
