import pytest
from unittest.mock import MagicMock, patch
from pipeline import duration_reconcile_v2

@patch("appcore.omni_ffmpeg_tempo_config.is_enabled")
@patch("pipeline.speech_rate_model.get_effective_rate")
@patch("pipeline.tts.generate_segment_audio")
@patch("pipeline.tts.get_audio_duration")
@patch("pipeline.av_translate.rewrite_one")
def test_acoustic_sandbox_bounded_physical_verification(
    mock_rewrite,
    mock_get_audio_duration,
    mock_generate_tts,
    mock_get_rate,
    mock_tempo_enabled,
):
    """验证沙箱只做粗筛，真实 TTS 物理时长不准时会继续验证备选候选。"""
    
    # 1. 基础 Mock 配置
    mock_tempo_enabled.return_value = False
    mock_get_rate.return_value = 10.0  # 10 字符/秒
    
    # 模拟重写方法。第一轮沙箱预测完全收敛，但真实 TTS 仍偏长；第二轮作为备选最终收敛。
    mock_rewrite.side_effect = [
        {"text": "Ten charsx", "coverage_ok": True},          # 10 字符 -> 1.0s, 目标 1.0s -> ok
        {"text": "Shortened sentence.", "coverage_ok": True},  # 19 字符 -> 1.9s, 目标 1.0s -> needs_rewrite
    ]
    
    # 模拟真实 TTS 合成，第一条沙箱命中的候选真实时长仍偏长，第二条才物理收敛。
    mock_generate_tts.return_value = "/fake/path/best.mp3"
    mock_get_audio_duration.side_effect = [1.9, 1.0]
    
    # 2. 构造测试数据。
    # 目标时长 1.0 秒。初始文字极长 (70个字，发音估计 7.0s)。初始真实音频也是 7.0 秒。
    task = {"plugin_config": {"text_rewrite": "1"}}  # 开启重写
    av_output = {
        "sentences": [
            {
                "asr_index": 0,
                "start_time": 0.0,
                "end_time": 1.0,
                "target_duration": 1.0,
                "text": "This is a very very long sentence that will definitely overshoot the target duration of one second.",
                "target_chars_range": (5, 15),
            }
        ]
    }
    tts_output = {
        "segments": [
            {
                "asr_index": 0,
                "tts_path": "/fake/path/original.mp3",
                "tts_duration": 7.0,
            }
        ]
    }
    
    # 3. 运行 V2 reconcile_duration 对齐器
    results = duration_reconcile_v2.reconcile_duration(
        task=task,
        av_output=av_output,
        tts_output=tts_output,
        voice_id="voice1",
        target_language="en",
        av_inputs={},
        shot_notes={},
        script_segments=[],
        max_rewrite_rounds=2,
        max_tts_regenerate_attempts=2,
    )
    
    # 4. 断言验证
    assert len(results) == 1
    res = results[0]
    
    # 重写尝试 2 轮；真实 TTS 先验证沙箱最优，再在偏差时验证下一条备选。
    assert res["text_rewrite_attempts"] == 2
    assert res["tts_regenerate_attempts"] == 2
    assert mock_generate_tts.call_count == 2
    assert mock_get_audio_duration.call_count == 2
    assert res["status"] == "ok"
    assert res["best_effort"] is False
    
    # 验证 attempts 详情
    attempts = res["attempts"]
    assert len(attempts) == 2
    
    # 第一轮：先由沙箱入围，随后真实 TTS 校验发现仍偏长。
    assert attempts[0]["sandbox_predicted"] is False
    assert attempts[0]["status"] == "needs_rewrite"
    
    # 第二轮：真实配音合成后物理收敛，sandbox_predicted 被覆盖更新为 False
    assert attempts[1]["sandbox_predicted"] is False
    assert res["selected_attempt_round"] == 2
    assert res["duration_ratio"] == 1.0
