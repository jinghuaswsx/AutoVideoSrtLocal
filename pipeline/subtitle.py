"""
字幕生成模块：基于 TTS 音频时长重新计算时间戳，生成格式化 .srt 字幕
规则：
  - 首字母大写
  - 每行最多 42 字符，不截断单词
  - 最多 2 行，尽量均衡；较长行放第一行
"""
import os
from typing import List, Dict


def format_timestamp(seconds: float) -> str:
    """秒数转 SRT 时间戳格式 HH:MM:SS,mmm"""
    ms = int((seconds % 1) * 1000)
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def capitalize_sentence(text: str) -> str:
    """首字母大写，保留其余大小写"""
    text = text.strip()
    if not text:
        return text
    return text[0].upper() + text[1:]


def wrap_text(text: str, max_chars: int = 42, max_lines: int = 2) -> str:
    """
    智能断行：
    - 不截断单词
    - 最多 2 行
    - 较长段放第一行
    - 两行尽量均衡
    """
    words = text.split()
    if not words:
        return text

    # 如果单行放得下，直接返回
    if len(text) <= max_chars:
        return text

    # 尝试找最佳断点使两行尽量均衡
    best_split = None
    best_diff = float("inf")

    current = ""
    for i, word in enumerate(words[:-1]):
        if current:
            current += " " + word
        else:
            current = word

        rest = " ".join(words[i+1:])

        if len(current) <= max_chars and len(rest) <= max_chars:
            diff = abs(len(current) - len(rest))
            if diff < best_diff:
                best_diff = diff
                best_split = (current, rest)

    if best_split:
        line1, line2 = best_split
        # 较长行放第一行
        if len(line2) > len(line1):
            line1, line2 = line2, line1
        return f"{line1}\n{line2}"

    # 文案超出两行容量：顺序填词，第一行满了填第二行，超出丢弃
    lines = ["", ""]
    current_line = 0

    for word in words:
        if current_line >= 2:
            break
        line = lines[current_line]
        candidate = word if not line else line + " " + word
        if len(candidate) <= max_chars:
            lines[current_line] = candidate
        else:
            current_line += 1
            if current_line < 2:
                lines[current_line] = word

    line1, line2 = lines[0], lines[1]
    if line2:
        return f"{line1}\n{line2}"
    return line1


def build_srt_from_tts(segments: List[Dict]) -> str:
    """
    基于 TTS 实际音频时长重新分配时间戳，生成 SRT 字幕内容

    segments 必须包含字段: translated, tts_duration
    时间戳按 TTS 音频的实际累计时间排列
    """
    srt_lines = []
    current_time = 0.0

    for i, seg in enumerate(segments, 1):
        text = capitalize_sentence(seg.get("translated", seg.get("text", "")))
        duration = seg.get("tts_duration", 2.0)

        start = current_time
        end = current_time + duration
        current_time = end

        formatted_text = wrap_text(text)

        srt_lines.append(str(i))
        srt_lines.append(f"{format_timestamp(start)} --> {format_timestamp(end)}")
        srt_lines.append(formatted_text)
        srt_lines.append("")

    return "\n".join(srt_lines)


def build_srt_from_manifest(manifest: Dict) -> str:
    srt_lines = []
    for i, seg in enumerate(manifest.get("segments", []), 1):
        text = capitalize_sentence(seg.get("translated", seg.get("text", "")))
        start = float(seg.get("timeline_start", 0.0))
        end = float(seg.get("timeline_end", start))
        formatted_text = wrap_text(text)

        srt_lines.append(str(i))
        srt_lines.append(f"{format_timestamp(start)} --> {format_timestamp(end)}")
        srt_lines.append(formatted_text)
        srt_lines.append("")

    return "\n".join(srt_lines)


def save_srt(content: str, output_path: str) -> str:
    """保存 .srt 文件"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path
