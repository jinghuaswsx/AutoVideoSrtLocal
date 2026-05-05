"""TTS 并发生成相关单测：线程池单例、配置解析、429 退避、并发提交、回调状态机。"""
from __future__ import annotations

import os
import threading
from unittest.mock import patch

import pytest

from pipeline import tts


def test_resolve_tts_max_concurrency_default(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: None)
    assert tts._resolve_tts_max_concurrency() == 12


def test_resolve_tts_max_concurrency_user_override(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "8")
    assert tts._resolve_tts_max_concurrency() == 8


def test_resolve_tts_max_concurrency_clamps_above_hard_cap(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "100")
    assert tts._resolve_tts_max_concurrency() == 15  # ElevenLabs Business 套餐硬上限


def test_resolve_tts_max_concurrency_clamps_below_one(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "0")
    assert tts._resolve_tts_max_concurrency() == 1


def test_resolve_tts_max_concurrency_invalid_string(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "not-a-number")
    assert tts._resolve_tts_max_concurrency() == 12  # 兜底默认


def test_resolve_tts_max_concurrency_falls_back_when_settings_unavailable(monkeypatch):
    def fail_get_setting(key):
        raise RuntimeError("settings db unavailable")

    monkeypatch.setattr("appcore.settings.get_setting", fail_get_setting)
    assert tts._resolve_tts_max_concurrency() == 12


def test_get_tts_pool_is_singleton(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: None)
    # 重置模块级单例
    tts._TTS_POOL = None
    pool1 = tts._get_tts_pool()
    pool2 = tts._get_tts_pool()
    assert pool1 is pool2
    assert pool1._max_workers == 12  # 默认值


def test_get_tts_pool_uses_resolved_max_workers(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "5")
    tts._TTS_POOL = None
    pool = tts._get_tts_pool()
    assert pool._max_workers == 5


# ===== Task 2: 429 / concurrent_limit_exceeded throttle retry =====


class _Fake429Error(Exception):
    """模拟 ElevenLabs SDK 抛出的 429 异常对象。"""
    def __init__(self, body: str = "concurrent_limit_exceeded"):
        super().__init__(body)
        self.status_code = 429
        self.body = body


class _Fake500Error(Exception):
    def __init__(self):
        super().__init__("server error")
        self.status_code = 500


def test_is_concurrent_limit_429_status_code():
    assert tts._is_concurrent_limit_429(_Fake429Error("concurrent_limit_exceeded")) is True
    assert tts._is_concurrent_limit_429(_Fake429Error("rate_limit_exceeded")) is True
    assert tts._is_concurrent_limit_429(_Fake500Error()) is False
    assert tts._is_concurrent_limit_429(ValueError("nope")) is False


def test_call_with_throttle_retry_succeeds_eventually(monkeypatch):
    """前两次 429，第三次成功。"""
    sleeps: list[float] = []
    monkeypatch.setattr(tts.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Fake429Error()
        return "ok"

    assert tts._call_with_throttle_retry(flaky) == "ok"
    assert calls["n"] == 3
    assert sleeps == [0.5, 1.0]  # 头两次失败的退避


def test_call_with_throttle_retry_exhausts(monkeypatch):
    """全部尝试都 429 → 最终抛 429 异常。"""
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)

    def always_429():
        raise _Fake429Error()

    with pytest.raises(_Fake429Error):
        tts._call_with_throttle_retry(always_429)


def test_call_with_throttle_retry_passes_non_429_through(monkeypatch):
    """非 429 错误立即透传，不重试。"""
    sleeps: list[float] = []
    monkeypatch.setattr(tts.time, "sleep", lambda s: sleeps.append(s))

    calls = {"n": 0}
    def boom():
        calls["n"] += 1
        raise _Fake500Error()

    with pytest.raises(_Fake500Error):
        tts._call_with_throttle_retry(boom)
    assert calls["n"] == 1
    assert sleeps == []


# ===== Task 3: throttle retry 接入 generate_segment_audio =====


def test_generate_segment_audio_retries_on_429(monkeypatch, tmp_path):
    """generate_segment_audio 收到 429 应该走 throttle retry。"""
    monkeypatch.setattr(tts.time, "sleep", lambda s: None)

    call_count = {"n": 0}
    def fake_convert(**kwargs):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise _Fake429Error()
        return iter([b"audio-bytes"])

    class FakeClient:
        class text_to_speech:
            convert = staticmethod(fake_convert)

    monkeypatch.setattr(tts, "_get_client", lambda api_key=None: FakeClient())
    out = tmp_path / "seg.mp3"
    tts.generate_segment_audio(text="hi", voice_id="v1", output_path=str(out),
                                elevenlabs_api_key="fake")
    assert out.exists()
    assert out.read_bytes() == b"audio-bytes"
    assert call_count["n"] == 2  # 第 1 次 429 + 第 2 次成功


# ===== Task 4: generate_full_audio 并发改造 =====


@pytest.fixture
def fake_segment_audio(monkeypatch):
    """让 generate_segment_audio 不真打 ElevenLabs，只 sleep 一小段写一个 mp3 文件。"""
    import time as _time

    def _fake(text, voice_id, output_path, **kwargs):
        # 模拟单段耗时（让并发性可观察）
        _time.sleep(0.05)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"fake-mp3")
        return output_path

    monkeypatch.setattr(tts, "generate_segment_audio", _fake)
    return _fake


@pytest.fixture
def fake_audio_duration(monkeypatch):
    monkeypatch.setattr(tts, "_get_audio_duration", lambda path: 1.5)


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """跳过真 ffmpeg concat。"""
    class _R:
        returncode = 0
        stderr = ""
    monkeypatch.setattr(tts.subprocess, "run", lambda *a, **kw: _R())


def test_generate_full_audio_emits_submitted_started_completed(
    monkeypatch, tmp_path, fake_segment_audio, fake_audio_duration, fake_ffmpeg,
):
    """on_progress 应按 submitted → started* → completed* 顺序触发。"""
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "4")
    tts._TTS_POOL = None  # 用新 max_workers=4 重建 pool

    snapshots = []
    def collect(snap):
        snapshots.append(dict(snap))

    segments = [{"tts_text": f"line {i}"} for i in range(6)]
    out = tts.generate_full_audio(
        segments, voice_id="v1", output_dir=str(tmp_path),
        on_progress=collect,
    )

    states = [s["state"] for s in snapshots]
    # 第 1 个事件必须是 submitted
    assert states[0] == "submitted"
    # 至少有 6 个 started 和 6 个 completed
    assert states.count("started") == 6
    assert states.count("completed") == 6
    # 完结后状态：done=6, active=0, queued=0
    last = snapshots[-1]
    assert last["done"] == 6
    assert last["active"] == 0
    assert last["queued"] == 0
    assert last["total"] == 6
    # 输出 segments 顺序按 i（concat 顺序）
    assert [s["tts_path"] for s in out["segments"]] == [
        os.path.join(str(tmp_path), "tts_segments", f"seg_{i:04d}.mp3")
        for i in range(6)
    ]


def test_generate_full_audio_active_never_exceeds_max_workers(
    monkeypatch, tmp_path, fake_segment_audio, fake_audio_duration, fake_ffmpeg,
):
    """同时活跃的段数不可能超过 pool max_workers。"""
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "3")
    tts._TTS_POOL = None

    peak_active = {"v": 0}
    def watch(snap):
        if snap["active"] > peak_active["v"]:
            peak_active["v"] = snap["active"]

    segments = [{"tts_text": f"line {i}"} for i in range(10)]
    tts.generate_full_audio(segments, voice_id="v1", output_dir=str(tmp_path),
                             on_progress=watch)
    assert peak_active["v"] <= 3


def test_generate_full_audio_concat_order_by_index(
    monkeypatch, tmp_path, fake_audio_duration, fake_ffmpeg,
):
    """乱序完成，但 concat.txt 必须按 i 升序写入（音轨时序）。"""
    import time as _time

    def slow_for_index_zero(text, voice_id, output_path, **kwargs):
        # 让 i=0 比其他段慢一倍，确保完成顺序乱
        if "0000" in output_path:
            _time.sleep(0.15)
        else:
            _time.sleep(0.02)
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"fake")
        return output_path

    monkeypatch.setattr(tts, "generate_segment_audio", slow_for_index_zero)
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "4")
    tts._TTS_POOL = None

    segments = [{"tts_text": f"line {i}"} for i in range(5)]
    tts.generate_full_audio(segments, voice_id="v1", output_dir=str(tmp_path),
                             on_progress=None)

    concat_path = tmp_path / "tts_segments" / "concat.txt"
    lines = concat_path.read_text().splitlines()
    expected_basenames = [f"seg_{i:04d}.mp3" for i in range(5)]
    actual_basenames = [os.path.basename(line.split("'")[1]) for line in lines]
    assert actual_basenames == expected_basenames


def test_generate_full_audio_first_failure_propagates(
    monkeypatch, tmp_path, fake_audio_duration, fake_ffmpeg,
):
    """任一段抛错 → cancel 其余 + 抛 RuntimeError。"""
    def fail_index_2(text, voice_id, output_path, **kwargs):
        if "0002" in output_path:
            raise ValueError("synthetic failure")
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"fake")
        return output_path

    monkeypatch.setattr(tts, "generate_segment_audio", fail_index_2)
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "4")
    tts._TTS_POOL = None

    segments = [{"tts_text": f"line {i}"} for i in range(5)]
    with pytest.raises(RuntimeError, match="TTS segment generation failed at index 2"):
        tts.generate_full_audio(segments, voice_id="v1", output_dir=str(tmp_path))


def test_generate_full_audio_legacy_on_segment_done_still_called(
    monkeypatch, tmp_path, fake_segment_audio, fake_audio_duration, fake_ffmpeg,
):
    """旧 on_segment_done 接口要继续兼容（向下不破）。"""
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "4")
    tts._TTS_POOL = None

    legacy_calls = []
    def legacy_cb(done, total, info):
        legacy_calls.append((done, total, info.get("segment_index")))

    segments = [{"tts_text": f"line {i}"} for i in range(4)]
    tts.generate_full_audio(segments, voice_id="v1", output_dir=str(tmp_path),
                             on_segment_done=legacy_cb)
    assert len(legacy_calls) == 4
    # done 单调递增，最后一次 done == total
    dones = [c[0] for c in legacy_calls]
    assert dones == sorted(dones)
    assert dones[-1] == 4
    assert all(c[1] == 4 for c in legacy_calls)


def test_generate_full_audio_progress_callback_exception_does_not_break(
    monkeypatch, tmp_path, fake_segment_audio, fake_audio_duration, fake_ffmpeg,
):
    """on_progress 回调抛错应被吞掉，不影响主流程。"""
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: "2")
    tts._TTS_POOL = None

    def angry(snap):
        raise RuntimeError("callback explodes")

    segments = [{"tts_text": f"line {i}"} for i in range(3)]
    out = tts.generate_full_audio(segments, voice_id="v1", output_dir=str(tmp_path),
                                    on_progress=angry)
    assert len(out["segments"]) == 3
