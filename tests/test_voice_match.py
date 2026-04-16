import numpy as np
from unittest.mock import patch, MagicMock
from pipeline.voice_match import extract_sample_clip, match_candidates


def test_match_candidates_returns_top_k_by_cosine_similarity():
    query_vec = np.array([1.0, 0.0], dtype=np.float32)
    rows = [
        {"voice_id": "a", "name": "A", "language": "en",
         "gender": "female", "accent": None, "preview_url": None,
         "audio_embedding": np.array([1.0, 0.0], dtype=np.float32).tobytes()},
        {"voice_id": "b", "name": "B", "language": "en",
         "gender": "female", "accent": None, "preview_url": None,
         "audio_embedding": np.array([0.9, 0.1], dtype=np.float32).tobytes()},
        {"voice_id": "c", "name": "C", "language": "en",
         "gender": "female", "accent": None, "preview_url": None,
         "audio_embedding": np.array([0.0, 1.0], dtype=np.float32).tobytes()},
    ]
    with patch("pipeline.voice_match._query_voices_by_language",
               return_value=rows):
        top3 = match_candidates(query_vec, language="en", top_k=3)
    assert [c["voice_id"] for c in top3] == ["a", "b", "c"]
    assert top3[0]["similarity"] > 0.99
    assert top3[-1]["similarity"] < 0.01


def test_match_candidates_limits_top_k():
    query_vec = np.array([1.0, 0.0], dtype=np.float32)
    rows = [
        {"voice_id": chr(65 + i), "name": "x", "language": "en",
         "gender": None, "accent": None, "preview_url": None,
         "audio_embedding": np.array(
             [1.0 - i * 0.05, i * 0.05], dtype=np.float32).tobytes()}
        for i in range(6)
    ]
    with patch("pipeline.voice_match._query_voices_by_language",
               return_value=rows):
        top = match_candidates(query_vec, language="en", top_k=3)
    assert len(top) == 3


def test_match_candidates_skips_empty_embedding():
    query_vec = np.array([1.0, 0.0], dtype=np.float32)
    rows = [
        {"voice_id": "none", "name": "X", "language": "en",
         "gender": None, "accent": None, "preview_url": None,
         "audio_embedding": None},
        {"voice_id": "good", "name": "Y", "language": "en",
         "gender": None, "accent": None, "preview_url": None,
         "audio_embedding": np.array([1.0, 0.0], dtype=np.float32).tobytes()},
    ]
    with patch("pipeline.voice_match._query_voices_by_language",
               return_value=rows):
        top = match_candidates(query_vec, language="en", top_k=5)
    assert [c["voice_id"] for c in top] == ["good"]


def test_extract_sample_clip_picks_middle_voiced_segment(tmp_path):
    video_path = tmp_path / "v.mp4"
    video_path.write_bytes(b"fake")
    with patch("pipeline.voice_match._extract_audio_track",
               return_value=str(tmp_path / "full.wav")), \
         patch("pipeline.voice_match._cut_clip",
               return_value=str(tmp_path / "clip.wav")) as cut, \
         patch("pipeline.voice_match._get_duration", return_value=30.0):
        clip = extract_sample_clip(str(video_path), out_dir=str(tmp_path))
    assert clip.endswith("clip.wav")
    # 30s 视频中间 10s 段 = 10.0 ~ 20.0
    call_args = cut.call_args
    # 兼容位置参数或关键字调用
    start = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["start"]
    end = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs["end"]
    assert abs(start - 10.0) < 0.1
    assert abs(end - 20.0) < 0.1


def test_extract_sample_clip_handles_short_video(tmp_path):
    video_path = tmp_path / "v.mp4"
    video_path.write_bytes(b"fake")
    with patch("pipeline.voice_match._extract_audio_track",
               return_value=str(tmp_path / "full.wav")), \
         patch("pipeline.voice_match._cut_clip",
               return_value=str(tmp_path / "clip.wav")) as cut, \
         patch("pipeline.voice_match._get_duration", return_value=6.0):
        extract_sample_clip(str(video_path), out_dir=str(tmp_path))
    call_args = cut.call_args
    start = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs["start"]
    end = call_args.args[2] if len(call_args.args) > 2 else call_args.kwargs["end"]
    # 6s 视频：中点 3.0，10s 片段两侧对称超出则截取 0-6
    assert start >= 0.0
    assert end <= 6.0
    assert end > start
