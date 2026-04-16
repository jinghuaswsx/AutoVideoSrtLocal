"""
字幕生成 V2：分块、统一字号、SRT 输出
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# 中英文常见断句标点
BREAK_PUNCT = r"[.!?,;:\u3002\uFF01\uFF1F\uFF0C\uFF1B\uFF1A]"


def _split_one_line(text: str, max_chars: int) -> List[str]:
    """将 text 按中点附近最近的断句标点或空格拆成两行。"""
    if len(text) <= max_chars:
        return [text]
    mid = len(text) // 2
    # 优先找最接近中点的断句标点
    best = None
    best_dist = float("inf")
    for m in re.finditer(BREAK_PUNCT + r"\s*", text):
        dist = abs(m.end() - mid)
        if dist < best_dist:
            best_dist = dist
            best = m
    if best and 0 < best.end() < len(text):
        head = text[: best.end()].rstrip()
        tail = text[best.end():].strip()
        if head and tail:
            return [head, tail]
    # 退化：按空格拆
    space = text.rfind(" ", 0, max_chars)
    if space <= 0:
        space = max_chars
    head = text[:space].strip()
    tail = text[space:].strip()
    if not head:
        return [tail]
    if not tail:
        return [head]
    return [head, tail]


def split_into_blocks(
    text: str,
    *,
    max_chars_per_line: int,
    max_lines_per_block: int = 2,
) -> List[List[str]]:
    """把一段文本切成多个字幕块，每块至多 max_lines_per_block 行。"""
    text = (text or "").strip()
    if not text:
        return []

    # 递归拆分直到每行 <= max_chars_per_line
    lines = [text]
    progressed = True
    safety = 64
    while any(len(line) > max_chars_per_line for line in lines) and progressed and safety > 0:
        new_lines: List[str] = []
        progressed = False
        for line in lines:
            if len(line) > max_chars_per_line:
                parts = _split_one_line(line, max_chars_per_line)
                # 若拆不动（仅得到同样长度的一条）则保留原样，避免死循环
                if len(parts) == 1 and parts[0] == line:
                    new_lines.append(line)
                else:
                    new_lines.extend(parts)
                    progressed = True
            else:
                new_lines.append(line)
        lines = new_lines
        safety -= 1

    # 分组为块
    blocks: List[List[str]] = []
    for i in range(0, len(lines), max_lines_per_block):
        blocks.append(lines[i: i + max_lines_per_block])
    return blocks


def _max_chars_for_font(video_width: int, font_size: int,
                         safe_ratio: float = 0.8) -> int:
    """按字号粗略估算单行可容纳字符数（平均字宽 ≈ 0.55 × 字号）。"""
    avg_char_width = font_size * 0.55
    return max(1, int((video_width * safe_ratio) / avg_char_width))


def compute_unified_font_size(
    shots: List[Dict[str, Any]],
    *,
    video_width: int,
    video_height: int,
    min_size: int = 16,
    max_size: int = 42,
) -> int:
    """找出能让「最长字幕正好容纳在 2 行以内」的最大字号。"""
    longest = ""
    for shot in shots:
        text = (shot.get("final_text") or "").strip()
        if len(text) > len(longest):
            longest = text
    if not longest:
        return max_size
    for size in range(max_size, min_size - 1, -1):
        mcpl = _max_chars_for_font(video_width, size)
        blocks = split_into_blocks(longest, max_chars_per_line=mcpl)
        if len(blocks) == 1 and len(blocks[0]) <= 2:
            return size
    return min_size


def _fmt_time(t: float) -> str:
    """将秒转为 SRT 时间戳格式 HH:MM:SS,mmm。"""
    if t < 0:
        t = 0.0
    total_ms = int(round(t * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_srt(
    shots: List[Dict[str, Any]],
    *,
    font_size: int,
    max_chars_per_line: int,
) -> str:
    """基于分镜生成 SRT 字幕内容。"""
    entries: List[str] = []
    counter = 1
    for shot in shots:
        text = (shot.get("final_text") or "").strip()
        if not text:
            continue
        start = float(shot.get("start") or 0.0)
        duration = float(shot.get("final_duration") or shot.get("duration") or 0.0)
        end = start + duration
        blocks = split_into_blocks(text, max_chars_per_line=max_chars_per_line)
        if not blocks:
            continue
        total = len(blocks)
        if total > 0:
            block_span = (end - start) / total
        else:
            block_span = 0.0
        for i, block in enumerate(blocks):
            b_start = start + i * block_span
            b_end = end if i == total - 1 else start + (i + 1) * block_span
            text_block = "\n".join(block)
            entries.append(
                f"{counter}\n"
                f"{_fmt_time(b_start)} --> {_fmt_time(b_end)}\n"
                f"{text_block}\n"
            )
            counter += 1
    return "\n".join(entries)
