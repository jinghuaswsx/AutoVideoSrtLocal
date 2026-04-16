"""
Speaker embedding 提取（resemblyzer 封装）+ 余弦相似度 + 序列化
"""
from __future__ import annotations

from typing import Any

import numpy as np

# 模块级占位符：真正的 resemblyzer import 发生在 _ensure_imports()。
# 测试通过 patch("pipeline.voice_embedding.VoiceEncoder" / "preprocess_wav") 替换这两个符号。
VoiceEncoder: Any = None
preprocess_wav: Any = None

_ENCODER_CACHE: dict = {}


def _ensure_imports() -> None:
    """延迟 import resemblyzer，避免模块级导入失败影响没有该依赖的测试环境。"""
    global VoiceEncoder, preprocess_wav
    if VoiceEncoder is None:
        from resemblyzer import VoiceEncoder as _Enc, preprocess_wav as _pre
        VoiceEncoder = _Enc
        preprocess_wav = _pre


def _get_encoder():
    if "enc" not in _ENCODER_CACHE:
        _ensure_imports()
        _ENCODER_CACHE["enc"] = VoiceEncoder(verbose=False)
    return _ENCODER_CACHE["enc"]


def embed_audio_file(audio_path: str) -> np.ndarray:
    """从音频文件提取 256 维 speaker embedding 向量。"""
    _ensure_imports()
    wav = preprocess_wav(audio_path)
    encoder = _get_encoder()
    return np.asarray(encoder.embed_utterance(wav), dtype=np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """两向量的余弦相似度，范围 [-1, 1]。任一零向量返回 0。"""
    # 使用 float64 做计算以保证数学精度（同向量严格返回 1.0）。
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def serialize_embedding(vec: np.ndarray) -> bytes:
    """将向量序列化为二进制（float32），用于写入 DB BLOB。"""
    return np.asarray(vec, dtype=np.float32).tobytes()


def deserialize_embedding(blob: bytes) -> np.ndarray:
    """从 DB BLOB 还原向量。"""
    return np.frombuffer(blob, dtype=np.float32).copy()
