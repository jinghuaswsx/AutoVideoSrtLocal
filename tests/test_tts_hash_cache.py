import os
import shutil
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from pipeline import tts

def test_get_tts_cache_key_determinism():
    """验证相同参数生成相同哈希键，不同参数生成不同哈希键"""
    key1 = tts.get_tts_cache_key("hello", "voice1", "model1", "en", 1.0, 0.7, 0.9)
    key2 = tts.get_tts_cache_key("hello", "voice1", "model1", "en", 1.0, 0.7, 0.9)
    assert key1 == key2

    key3 = tts.get_tts_cache_key("world", "voice1", "model1", "en", 1.0, 0.7, 0.9)
    assert key1 != key3


def test_get_tts_cache_key_rounding():
    """验证 speed, stability, similarity_boost 被正确四舍五入以提高缓存碰撞率"""
    key1 = tts.get_tts_cache_key("hello", "voice1", "model1", "en", 1.00004, 0.70004, 0.90004)
    key2 = tts.get_tts_cache_key("hello", "voice1", "model1", "en", 1.0, 0.7, 0.9)
    assert key1 == key2


@patch("pipeline.tts._get_audio_duration")
@patch("pipeline.tts._get_client")
def test_generate_segment_audio_caching(mock_get_client, mock_get_duration):
    """测试 generate_segment_audio 缓存读取和写入逻辑"""
    mock_get_duration.return_value = 1.0
    # 模拟 ElevenLabs client 和 convert 返回生成器
    mock_client = MagicMock()
    mock_client.text_to_speech.convert.return_value = [b"mock_audio_bytes" * 100]  # 让它大于 1024 字节以通过大小校验
    mock_get_client.return_value = mock_client

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "out.mp3")
        cache_dir = os.path.join("instance", "tts_cache")

        # 确保全局缓存目录存在但没有我们当前参数的缓存
        os.makedirs(cache_dir, exist_ok=True)
        
        # 1. 第一次生成段落（物理缓存未击中）
        # 强制设置一个唯一的 text 以免碰撞历史物理缓存
        unique_text = f"unique_test_text_for_cache_{os.urandom(8).hex()}"
        cache_key = tts.get_tts_cache_key(unique_text, "voice1", "model1", "en", 1.0, None, None)
        expected_cache_file = os.path.join(cache_dir, f"{cache_key}.mp3")

        # 确保缓存文件先前不存在
        if os.path.exists(expected_cache_file):
            os.remove(expected_cache_file)

        res_path = tts.generate_segment_audio(
            text=unique_text,
            voice_id="voice1",
            output_path=output_path,
            model_id="model1",
            language_code="en",
        )
        assert res_path == output_path
        assert os.path.exists(output_path)
        assert mock_client.text_to_speech.convert.call_count == 1
        
        # 应该物理上写入了缓存文件
        assert os.path.exists(expected_cache_file)

        # 2. 第二次生成段落（全局缓存击中）
        mock_client.text_to_speech.convert.reset_mock()
        output_path_2 = os.path.join(tmpdir, "out_2.mp3")
        
        res_path_2 = tts.generate_segment_audio(
            text=unique_text,
            voice_id="voice1",
            output_path=output_path_2,
            model_id="model1",
            language_code="en",
        )
        assert res_path_2 == output_path_2
        assert os.path.exists(output_path_2)
        # 应无 ElevenLabs 接口调用
        assert mock_client.text_to_speech.convert.call_count == 0

        # 清理我们的测试缓存文件，以免污染本地开发环境
        if os.path.exists(expected_cache_file):
            os.remove(expected_cache_file)
