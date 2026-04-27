"""ASR 输出语言污染清理。

豆包 ASR 在识别非中文视频时，静音/杂音/背景音乐段落经常被错误识别为中文，
污染下游 LLM 翻译。本模块在 ASR 输出之后，用 fast-langdetect 检测每个
utterance 的语言，把非主语言段落删除并把时间区间合并到相邻段，保持字幕
时间线连续。

策略：
  1. 太短不判断（< MIN_TEXT_LEN 字符 或 < MIN_DURATION_SEC）
  2. 置信度低不判断（fast-langdetect score < MIN_CONFIDENCE）
  3. 检测语言归一化（zh-Hans/zh-Hant → zh，en-US → en）
  4. 非主语言段标记删除
  5. 删除段的 (start, end) 合并到前一段；首段被删则合并到后一段；
     全段都被删 → 返回空列表

无 fallback 重转：当前 ASR 候选只有豆包 + Scribe，按语言路由不重叠，
fallback 没意义。
"""
from __future__ import annotations

import logging
from typing import List, Sequence

from appcore.asr_providers import Utterance

log = logging.getLogger(__name__)

MIN_TEXT_LEN = 8           # < 8 字符（含空格）一律保留
MIN_DURATION_SEC = 1.5     # < 1.5 秒一律保留
MIN_CONFIDENCE = 0.5       # fast-langdetect score 低于此值视为不可靠


def detect_language(text: str) -> tuple[str, float] | None:
    """返回 (lang, score) 或 None（无法判定 / 检测失败）。

    输出已归一化（zh-Hans/zh-Hant → zh，en-US/en-GB → en）。
    """
    if not text:
        return None
    cleaned = text.replace("\n", " ").strip()
    if len(cleaned) < 4:
        return None
    try:
        from fast_langdetect import detect

        result = detect(cleaned, low_memory=True)
    except Exception:
        log.exception("[ASR-Purify] fast-langdetect detect 失败 text=%r", cleaned[:80])
        return None
    raw_lang = (result.get("lang") or "").lower()
    score = float(result.get("score") or 0.0)
    if not raw_lang:
        return None
    return _normalize_lang_code(raw_lang), score


def _normalize_lang_code(code: str) -> str:
    if not code:
        return ""
    return code.split("-", 1)[0].split("_", 1)[0].lower()


def _too_short_to_judge(utt: Utterance) -> bool:
    text = (utt.get("text") or "").strip()
    if len(text) < MIN_TEXT_LEN:
        return True
    duration = float(utt.get("end_time") or 0.0) - float(utt.get("start_time") or 0.0)
    if duration < MIN_DURATION_SEC:
        return True
    return False


def purify_language(
    utterances: Sequence[Utterance],
    *,
    source_language: str | None,
) -> List[Utterance]:
    """删除非主语言 utterance；把时间区间合并到相邻段。

    Args:
        utterances: ASR 主路径输出。
        source_language: 主语言（ISO-639-1）。"auto" / "" / None 时跳过 purify。

    Returns:
        清理后的 utterance 列表（深拷贝输入元素）。
    """
    if not utterances:
        return []

    main_lang = _normalize_lang_code(source_language or "")
    if not main_lang or main_lang == "auto":
        log.debug("[ASR-Purify] source_language 为空/auto，跳过清理")
        return [_clone_utt(u) for u in utterances]

    cloned = [_clone_utt(u) for u in utterances]
    drop_mask: list[bool] = [False] * len(cloned)

    for i, utt in enumerate(cloned):
        if _too_short_to_judge(utt):
            continue
        detected = detect_language(utt["text"])
        if detected is None:
            continue
        lang, score = detected
        if score < MIN_CONFIDENCE:
            continue
        if lang == main_lang:
            continue
        log.warning(
            "[ASR-Purify] 删除非主语言段 idx=%d main=%s detected=%s score=%.2f text=%r",
            i,
            main_lang,
            lang,
            score,
            (utt.get("text") or "")[:80],
        )
        drop_mask[i] = True

    deleted = sum(drop_mask)
    if deleted == 0:
        return cloned

    # 多数派保护：如果"被判非主语言"的段超过 judged 段的 60%，说明
    # source_language 跟实际音频对不上（豆包对非中/英文音频会输出乱码，
    # fast-langdetect 又会把乱码归类成英语，整段全删），不是局部污染。
    # 这种情况退回原 utterances，让下游照常用，避免 ASR 输出被一刀切清空。
    judged = sum(1 for u in cloned if not _too_short_to_judge(u))
    if judged > 0 and deleted >= judged * 0.6:
        log.warning(
            "[ASR-Purify] 跳过清理：%d/%d 段被判非主语言（main=%s），"
            "源语言可能与实际音频不一致；保留原 ASR 输出",
            deleted, judged, main_lang,
        )
        return cloned

    log.info("[ASR-Purify] 清理 %d/%d 段非主语言", deleted, len(cloned))
    return _merge_adjacent(cloned, drop_mask)


def _merge_adjacent(items: Sequence[Utterance], drop_mask: Sequence[bool]) -> List[Utterance]:
    """把 drop_mask=True 的段时间区间合并到相邻 kept 段。

    规则：
      - 被删段优先合并到**前一个保留段**（扩展其 end_time）
      - 首段就被删 → 累积 pending，并入下一个保留段（提前其 start_time）
      - 连续多个被删段 → 累积合并
      - 全部被删 → 返回空列表
    """
    out: List[Utterance] = []
    pending_start: float | None = None
    pending_end: float | None = None
    last_kept_idx_in_out: int | None = None

    for i, item in enumerate(items):
        s = float(item.get("start_time") or 0.0)
        e = float(item.get("end_time") or 0.0)

        if drop_mask[i]:
            if last_kept_idx_in_out is not None:
                cur_end = float(out[last_kept_idx_in_out].get("end_time") or 0.0)
                out[last_kept_idx_in_out]["end_time"] = max(cur_end, e)
            else:
                pending_start = s if pending_start is None else min(pending_start, s)
                pending_end = e if pending_end is None else max(pending_end, e)
            continue

        kept = item  # 已经是 _clone_utt 的副本
        if pending_start is not None:
            kept_start = float(kept.get("start_time") or 0.0)
            kept["start_time"] = min(kept_start, pending_start)
            pending_start = None
            pending_end = None
        out.append(kept)
        last_kept_idx_in_out = len(out) - 1

    return out


def _clone_utt(utt: Utterance) -> Utterance:
    """浅拷贝 utterance（words 列表也浅拷贝，保持元素只读语义）。"""
    cloned: Utterance = {  # type: ignore[typeddict-item]
        "text": utt.get("text", ""),
        "start_time": float(utt.get("start_time") or 0.0),
        "end_time": float(utt.get("end_time") or 0.0),
        "words": list(utt.get("words") or []),
    }
    return cloned
