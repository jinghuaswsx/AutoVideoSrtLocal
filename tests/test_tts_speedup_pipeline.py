"""regenerate_full_audio_with_speed 单测：mock ElevenLabs SDK，验证：
- 每段 segment 都用同一个 speed 调用 generate_segment_audio
- segments 落盘到独立目录避免缓存命中干扰
- concat 出的 mp3 路径符合命名约定
- 网络异常透出（不吞）让上层走 fallback
"""
import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def no_db_tts_settings(monkeypatch):
    monkeypatch.setattr("appcore.settings.get_setting", lambda key: None)
    from pipeline import tts

    tts._TTS_POOL = None
    yield
    tts._TTS_POOL = None


def test_regenerate_full_audio_with_speed_calls_each_segment_with_speed(tmp_path):
    from pipeline import tts

    segments = [
        {"index": 0, "tts_text": "hello world", "translated": "ignored"},
        {"index": 1, "tts_text": "second segment"},
        {"index": 2, "tts_text": "third"},
    ]

    seg_calls = []

    def fake_generate_segment_audio(text, voice_id, output_path, **kwargs):
        seg_calls.append({"text": text, "voice_id": voice_id,
                          "output_path": output_path,
                          "speed": kwargs.get("speed")})
        # 真的写一个空文件让 concat 不爆
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"\xff\xfb\x10\x00")  # mp3 magic bytes 占位
        return output_path

    def fake_get_audio_duration(path):
        return 1.5

    with patch.object(tts, "generate_segment_audio", side_effect=fake_generate_segment_audio), \
         patch.object(tts, "_get_audio_duration", side_effect=fake_get_audio_duration), \
         patch("subprocess.run") as fake_run:
        fake_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

        result = tts.regenerate_full_audio_with_speed(
            segments=segments,
            voice_id="voice-xyz",
            output_dir=str(tmp_path),
            variant="round_2",
            speed=0.9772,
            elevenlabs_api_key="fake-key",
            model_id="eleven_turbo_v2_5",
            language_code="es",
        )

    # 全部 segment 都用 speed=0.9772
    assert len(seg_calls) == 3
    for c in seg_calls:
        assert c["speed"] == pytest.approx(0.9772, abs=1e-4)
        assert c["voice_id"] == "voice-xyz"

    # segments 落盘到独立目录 round_2_speedup
    expected_seg_dir = os.path.join(str(tmp_path), "tts_segments", "round_2_speedup")
    for i, c in enumerate(seg_calls):
        assert c["output_path"] == os.path.join(expected_seg_dir, f"seg_{i:04d}.mp3")

    # concat 输出路径 tts_full.round_2.speedup.mp3
    assert result["full_audio_path"] == os.path.join(
        str(tmp_path), "tts_full.round_2.speedup.mp3"
    )
    assert len(result["segments"]) == 3
    for s in result["segments"]:
        assert "tts_path" in s and "tts_duration" in s


def test_regenerate_full_audio_with_speed_propagates_elevenlabs_failure(tmp_path):
    """ElevenLabs SDK 抛错时函数应该让异常上抛，调用方会 fallback。"""
    from pipeline import tts

    segments = [{"index": 0, "tts_text": "x"}]

    def boom(*args, **kwargs):
        raise RuntimeError("simulated elevenlabs SSL EOF")

    with patch.object(tts, "generate_segment_audio", side_effect=boom):
        with pytest.raises(RuntimeError, match="simulated elevenlabs SSL EOF"):
            tts.regenerate_full_audio_with_speed(
                segments=segments,
                voice_id="v",
                output_dir=str(tmp_path),
                variant="round_3",
                speed=1.05,
            )


def test_regenerate_full_audio_with_speed_invokes_on_segment_done_callback(tmp_path):
    from pipeline import tts

    segments = [{"index": i, "tts_text": f"seg{i}"} for i in range(3)]
    progress = []

    def fake_gen(text, voice_id, output_path, **kw):
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"\xff\xfb\x10\x00")
        return output_path

    with patch.object(tts, "generate_segment_audio", side_effect=fake_gen), \
         patch.object(tts, "_get_audio_duration", return_value=1.0), \
         patch("subprocess.run") as fake_run:
        fake_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        tts.regenerate_full_audio_with_speed(
            segments=segments, voice_id="v", output_dir=str(tmp_path),
            variant="r1", speed=1.05,
            on_segment_done=lambda done, total, info: progress.append((done, total)),
        )

    assert progress == [(1, 3), (2, 3), (3, 3)]
