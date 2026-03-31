"""
音频提取模块：从视频文件中提取音频，输出 WAV 格式供 ASR 使用
"""
import os
import subprocess


def extract_audio(video_path: str, output_dir: str) -> str:
    """
    从视频文件提取音频，输出 16kHz 单声道 WAV（豆包 ASR 推荐格式）

    Returns:
        str: 输出音频文件路径
    """
    os.makedirs(output_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(video_path))[0]
    audio_path = os.path.join(output_dir, f"{base_name}_audio.wav")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",                  # 不处理视频
        "-acodec", "pcm_s16le", # 16-bit PCM
        "-ar", "16000",         # 16kHz 采样率
        "-ac", "1",             # 单声道
        audio_path
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 音频提取失败: {result.stderr}")

    return audio_path


def get_video_duration(video_path: str) -> float:
    """获取视频时长（秒）"""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 获取时长失败: {result.stderr}")
    return float(result.stdout.strip())
