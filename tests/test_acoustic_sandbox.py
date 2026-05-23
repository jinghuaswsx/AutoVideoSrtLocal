import pytest
from unittest.mock import MagicMock, patch
from pipeline import duration_reconcile_v2

@patch("appcore.omni_ffmpeg_tempo_config.is_enabled")
@patch("pipeline.speech_rate_model.get_effective_rate")
@patch("pipeline.tts.generate_segment_audio")
@patch("pipeline.av_translate.rewrite_one")
def test_acoustic_sandbox_zero_api_waste(
    mock_rewrite,
    mock_generate_tts,
    mock_get_rate,
    mock_tempo_enabled,
):
    """验证在重写对齐收敛循环内，真实 ElevenLabs TTS API 仅在锁定最优候选时触发唯一一次"""
    
    # 1. 基础 Mock 配置
    mock_tempo_enabled.return_value = False
    mock_get_rate.return_value = 10.0  # 10 字符/秒
    
    # 模拟重写方法。第一轮重写虽然缩短了但还没收敛，第二轮完全收敛
    # 我们第一轮重写后让它仍然 needs_rewrite，第二轮后收敛为 ok。
    mock_rewrite.side_effect = [
        {"text": "Shortened sentence.", "coverage_ok": True},  # 19 字符 -> 1.9s, 目标 1.0s -> needs_rewrite
        {"text": "Ten charsx", "coverage_ok": True},          # 10 字符 -> 1.0s, 目标 1.0s -> ok
    ]
    
    # 模拟真实 TTS 合成，记录调用次数和返回的时长
    mock_generate_tts.return_value = ("/fake/path/best.mp3", 0.75)
    
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
        max_rewrite_rounds=5,
        max_tts_regenerate_attempts=5,
    )
    
    # 4. 断言验证
    assert len(results) == 1
    res = results[0]
    
    # 重写尝试了 2 轮，但真实云端 ElevenLabs 调用仅触发了 1 次！
    assert res["text_rewrite_attempts"] == 2
    assert res["tts_regenerate_attempts"] == 1
    assert mock_generate_tts.call_count == 1
    
    # 验证 attempts 详情
    attempts = res["attempts"]
    assert len(attempts) == 2
    
    # 第一轮：使用沙盒估计时长，且标记为沙盒预测
    assert attempts[0]["sandbox_predicted"] is True
    assert attempts[0]["status"] == "needs_rewrite"
    
    # 第二轮：因为收敛（或最优），所以进行了真实配音合成，sandbox_predicted 被覆盖更新为 False
    assert attempts[1]["sandbox_predicted"] is False
    assert res["selected_attempt_round"] == 2
