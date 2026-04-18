"""copywriting_translate 子任务 runtime。

把 media_copywritings.lang='en' 的英文文案翻译到目标语言。

不要与现有 appcore/copywriting_runtime.py(从视频生成文案)混淆——
后者是"创作"流程,本模块是"翻译"流程,完全独立。

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 2.2 节
"""
from __future__ import annotations

from pipeline.text_translate import translate_text


def _llm_translate(source_text: str, source_lang: str, target_lang: str) -> tuple[str, int]:
    """调用 LLM 翻译,返回 (译文, token 总数)。

    作为独立函数是为了让上层测试可以 monkeypatch 此处,
    无需 mock 到 pipeline 层。
    """
    r = translate_text(source_text, source_lang, target_lang)
    total_tokens = (r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
    return r["text"], total_tokens


def translate_copy_text(source_text: str, source_lang: str, target_lang: str) -> tuple[str, int]:
    """翻译单条文案。返回 (译文, 消耗 token 总数)。空输入直接短路。"""
    if not source_text or not source_text.strip():
        return "", 0
    return _llm_translate(source_text, source_lang, target_lang)
