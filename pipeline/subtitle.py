"""
字幕生成模块：基于 TTS 音频时长重新计算时间戳，生成格式化 .srt 字幕
规则：
  - 首字母大写
  - 每行最多 42 字符，不截断单词
  - 最多 2 行，尽量均衡；较长行放第一行
"""
import logging
import os
import re
from typing import List, Dict

log = logging.getLogger(__name__)


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


def _strip_terminal_punctuation(text: str) -> str:
    return re.sub(r"[,.!?;:]+$", "", text.strip()).strip()


def _choose_balanced_split(words: List[str], weak_boundary_words: set | None = None) -> int:
    if weak_boundary_words is None:
        weak_boundary_words = {"and", "or", "to", "of", "for", "with", "the", "a", "an"}
    best_index = max(1, len(words) // 2)
    best_score = None

    for index in range(2, len(words) - 1):
        left_count = index
        right_count = len(words) - index
        score = abs(left_count - right_count)

        if words[index - 1].strip(",").lower() in weak_boundary_words:
            score += 1.0
        if words[index].strip(",").lower() in weak_boundary_words:
            score += 1.0
        if words[index - 1].endswith(","):
            score -= 0.25

        if best_score is None or score < best_score:
            best_score = score
            best_index = index

    return best_index


def format_subtitle_chunk_text(
    text: str,
    weak_boundary_words: set | None = None,
    *,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
) -> str:
    cleaned = capitalize_sentence(_strip_terminal_punctuation(text))
    words = cleaned.split()
    if len(cleaned) <= max_chars_per_line:
        return cleaned
    if max_lines <= 1:
        return wrap_text(cleaned, max_chars=max_chars_per_line, max_lines=max_lines)
    if len(words) <= 5:
        return wrap_text(cleaned, max_chars=max_chars_per_line, max_lines=max_lines)

    split_index = _choose_balanced_split(words, weak_boundary_words=weak_boundary_words)
    line1 = " ".join(words[:split_index]).strip()
    line2 = " ".join(words[split_index:]).strip()
    if not line1 or not line2:
        return cleaned
    if len(line1) > max_chars_per_line or len(line2) > max_chars_per_line:
        return wrap_text(cleaned, max_chars=max_chars_per_line, max_lines=max_lines)
    return f"{line1}\n{line2}"


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
    max_lines = max(1, int(max_lines or 1))

    # 如果单行放得下，直接返回
    if len(text) <= max_chars:
        return text
    if len(words) == 1 and len(words[0]) > max_chars:
        return "\n".join(
            words[0][i:i + max_chars]
            for i in range(0, min(len(words[0]), max_chars * max_lines), max_chars)
        )
    if max_lines == 1:
        line = ""
        for word in words:
            candidate = word if not line else line + " " + word
            if len(candidate) > max_chars and line:
                break
            line = candidate
        return line or text[:max_chars]

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

    # 文案超出容量：顺序填词，超出丢弃
    lines = [""] * max_lines
    current_line = 0
    truncated = False

    for word in words:
        if current_line >= max_lines:
            truncated = True
            break
        line = lines[current_line]
        candidate = word if not line else line + " " + word
        if len(candidate) <= max_chars:
            lines[current_line] = candidate
        else:
            current_line += 1
            if current_line < max_lines:
                lines[current_line] = word

    if truncated:
        log.warning("字幕文本被截断（超出 %d 字符 × %d 行）: %s...", max_chars, max_lines, text[:80])

    return "\n".join(line for line in lines if line)


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


def build_srt_from_chunks(
    chunks: List[Dict],
    weak_boundary_words: set | None = None,
    *,
    max_chars_per_line: int = 42,
    max_lines: int = 2,
) -> str:
    srt_lines = []
    for i, chunk in enumerate(chunks, 1):
        srt_lines.append(str(i))
        srt_lines.append(
            f"{format_timestamp(float(chunk['start_time']))} --> {format_timestamp(float(chunk['end_time']))}"
        )
        srt_lines.append(
            format_subtitle_chunk_text(
                chunk["text"],
                weak_boundary_words=weak_boundary_words,
                max_chars_per_line=max_chars_per_line,
                max_lines=max_lines,
            )
        )
        srt_lines.append("")

    return "\n".join(srt_lines)


def save_srt(content: str, output_path: str) -> str:
    """保存 .srt 文件"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path


def apply_punctuation_spacing(srt_content: str, rules: dict) -> str:
    """按 rules 对 SRT 文本行做标点空格后处理（跳过时间戳行与序号行）。

    rules 字段：
      - nbsp_before: list[str]，这些标点前加 U+00A0
      - guillemets: bool，True 时 « » 内侧加 U+00A0
    """
    nbsp = "\u00A0"
    nbsp_before = set(rules.get("nbsp_before") or [])
    handle_guillemets = bool(rules.get("guillemets"))

    lines = srt_content.split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.isdigit() or "-->" in stripped:
            out.append(line)
            continue
        if nbsp_before:
            # 构造字符类；转义正则元字符
            escaped = "".join(re.escape(ch) for ch in nbsp_before)
            line = re.sub(rf"\s*([{escaped}])", rf"{nbsp}\1", line)
        if handle_guillemets:
            line = re.sub(r"«\s*", f"«{nbsp}", line)
            line = re.sub(r"\s*»", f"{nbsp}»", line)
        out.append(line)
    return "\n".join(out)


def apply_french_punctuation(text: str) -> str:
    """向后兼容薄包装：等价于 apply_punctuation_spacing(text, 法语规则)。

    Old callers that already use apply_french_punctuation(text) continue to
    work unchanged — signature and behaviour are preserved.
    """
    return apply_punctuation_spacing(text, {
        "nbsp_before": ["?", "!", ":", ";"],
        "guillemets": True,
    })
