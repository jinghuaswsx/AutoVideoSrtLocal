"""
翻译模块：通过 OpenRouter 调用 Claude，将中文文案翻译为 TikTok 卖货广告英文文案
"""
import json
from typing import List, Dict
from openai import OpenAI
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, CLAUDE_MODEL

client = OpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL,
)

SYSTEM_PROMPT = """You are an expert copywriter specializing in TikTok e-commerce advertising for the US market.

Your task is to translate Chinese short video scripts into English copy that:
1. Sounds completely native — written by an American creator, NOT translated
2. Matches the energy and style of the original (enthusiastic, urgent, conversational)
3. Uses natural spoken American English — contractions, casual phrasing, direct address ("you", "your")
4. Adapts Chinese cultural references, idioms, and platform slang into US TikTok equivalents
5. Maintains persuasive selling power — hooks, social proof, calls-to-action must land hard
6. Keeps the same sentence count and rhythm as the original for audio/video sync
7. Each translated segment must be proportional in length to its original — don't make short segments long or long segments short

TikTok Ad Copy Rules:
- Open with a scroll-stopping hook (question, bold claim, or relatable pain point)
- Use simple vocabulary — 8th grade reading level max
- Active voice, present tense preferred
- Avoid formal or academic language
- "This thing is insane" beats "This product is remarkable"
- Price reveals, before/after, and urgency phrases should feel organic, not salesy

Output ONLY a valid JSON array. No explanations, no markdown, no extra text.
Format: [{"index": 0, "translated": "..."}, {"index": 1, "translated": "..."}, ...]"""


def translate_segments(segments: List[Dict]) -> List[Dict]:
    """
    翻译 ASR 分段结果

    Args:
        segments: [{"text": str, "start_time": float, "end_time": float}, ...]

    Returns:
        同结构，每个 dict 新增 "translated" 字段
    """
    if not segments:
        return segments

    # 构建待翻译列表
    items = [{"index": i, "text": seg["text"]} for i, seg in enumerate(segments)]

    user_prompt = f"""Translate these Chinese TikTok ad script segments to native American English.
Each segment is one spoken sentence/phrase. Keep the same count and order.

Segments:
{json.dumps(items, ensure_ascii=False, indent=2)}

Remember: output ONLY the JSON array."""

    response = client.chat.completions.create(
        model=CLAUDE_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.7,
        max_tokens=4096,
    )

    raw = response.choices[0].message.content.strip()

    # 防御性解析：去掉可能的 markdown 代码块
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    translations = json.loads(raw)
    translation_map = {item["index"]: item["translated"] for item in translations}

    result = []
    for i, seg in enumerate(segments):
        seg_copy = dict(seg)
        seg_copy["translated"] = translation_map.get(i, seg["text"])
        result.append(seg_copy)

    return result
