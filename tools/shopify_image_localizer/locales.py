"""语言代码 → 浏览器界面显示名的映射。

EZ Product Translate 在选语言时下拉里显示的是英文语言名（French, German,
Italian...），**不是** ISO 639-1 代码。所以 GUI 清单里需要直接告诉用户
"在页面下拉里找这个英文名"。
"""
from __future__ import annotations


ISO_TO_ENGLISH_NAME: dict[str, str] = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "ja": "Japanese",
    "nl": "Dutch",
    "pt": "Portuguese",
    "sv": "Swedish",
    "fi": "Finnish",
    "da": "Danish",
    "no": "Norwegian",
    "pl": "Polish",
    "cs": "Czech",
    "zh": "Chinese",
    "zh-cn": "Chinese (Simplified)",
    "zh-tw": "Chinese (Traditional)",
    "ko": "Korean",
    "ru": "Russian",
    "ar": "Arabic",
    "tr": "Turkish",
    "he": "Hebrew",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "hu": "Hungarian",
    "ro": "Romanian",
    "uk": "Ukrainian",
    "el": "Greek",
    "hi": "Hindi",
}


def english_name_for(lang_code: str) -> str:
    """给出浏览器下拉里显示的英文名；未知代码回退到 ISO 大写形式。"""
    code = (lang_code or "").strip().lower()
    name = ISO_TO_ENGLISH_NAME.get(code)
    if name:
        return name
    # 未知语言码：回退到大写 + 原始代码，如 "KO-KP" 这种
    return code.upper() if code else ""
