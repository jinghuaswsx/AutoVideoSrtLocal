import numpy as np
from unittest.mock import patch, MagicMock
from pipeline.voice_embedding import (
    embed_audio_file, cosine_similarity,
    serialize_embedding, deserialize_embedding,
)


def test_cosine_similarity_same_vector_is_one():
    v = np.array([1.0, 2.0, 3.0])
    assert cosine_similarity(v, v) == 1.0


def test_cosine_similarity_orthogonal_is_zero():
    a = np.array([1.0, 0.0])
    b = np.array([0.0, 1.0])
    assert cosine_similarity(a, b) == 0.0


def test_cosine_similarity_handles_zero_vector():
    a = np.zeros(3)
    b = np.array([1.0, 0.0, 0.0])
    assert cosine_similarity(a, b) == 0.0


def test_serialize_roundtrip_preserves_values():
    vec = np.array([0.1, -0.5, 1.2, 0.0], dtype=np.float32)
    blob = serialize_embedding(vec)
    restored = deserialize_embedding(blob)
    np.testing.assert_allclose(restored, vec)


def test_embed_audio_file_returns_256d_vector(tmp_path):
    dummy_audio = tmp_path / "test.wav"
    dummy_audio.write_bytes(b"fake")
    mock_encoder = MagicMock()
    mock_encoder.embed_utterance.return_value = np.zeros(256, dtype=np.float32)
    with patch("pipeline.voice_embedding.VoiceEncoder", return_value=mock_encoder), \
         patch("pipeline.voice_embedding.preprocess_wav", return_value=np.zeros(16000)):
        vec = embed_audio_file(str(dummy_audio))
    assert vec.shape == (256,)
