"""标题翻译 prompt 设置模块。

只负责从 `appcore.medias.list_languages()` 读取启用语种，并根据语种代码返回
内置的标题翻译 prompt；不读取数据库，也不做持久化。
"""
from __future__ import annotations

from appcore import medias


_SPECIAL_PROMPT_HINTS: dict[str, dict[str, str]] = {
    "de": {
        "expert": "德语本土化专家",
        "audience": "德国用户",
        "locale": "Bundesdeutsch",
        "extra": "优先采用德国用户熟悉的自然表达，保持本土化、准确、克制。",
    },
    "fr": {
        "expert": "法语本土化专家",
        "audience": "法语用户",
        "locale": "français naturel",
        "extra": "优先采用地道、自然、适合法语用户阅读的表达。",
    },
    "es": {
        "expert": "西班牙语本土化专家",
        "audience": "西语用户",
        "locale": "español natural",
        "extra": "优先采用自然、口语化但不失准确的西语表达。",
    },
    "it": {
        "expert": "意大利语本土化专家",
        "audience": "意大利用户",
        "locale": "italiano naturale",
        "extra": "优先采用自然、顺口、适合意大利用户阅读的表达。",
    },
    "ja": {
        "expert": "日语本土化专家",
        "audience": "日本用户",
        "locale": "自然な日本語",
        "extra": "优先采用符合日语母语者阅读习惯的自然表达。",
    },
    "pt": {
        "expert": "葡萄牙语本土化专家",
        "audience": "葡语用户",
        "locale": "português natural",
        "extra": "优先采用自然、流畅、符合葡语用户习惯的表达。",
    },
}


def _normalize_code(code: str | None) -> str:
    return (code or "").strip().lower()


def list_title_translate_languages() -> list[dict]:
    """返回可用于标题翻译的启用语种，过滤掉 `en`，保持原顺序。"""
    langs: list[dict] = []
    for row in medias.list_languages():
        code = _normalize_code(row.get("code"))
        if code == "en":
            continue
        if not row.get("enabled"):
            continue
        langs.append(row)
    return langs


def get_title_translate_language(code: str) -> dict:
    """按代码获取标题翻译语种信息。

    大小写和首尾空格不敏感；拒绝 `en`、未启用或不存在的语种。
    """
    normalized = _normalize_code(code)
    if not normalized or normalized == "en":
        raise ValueError(f"unsupported language: {normalized or code!r}")

    for row in medias.list_languages():
        row_code = _normalize_code(row.get("code"))
        if row_code != normalized:
            continue
        if not row.get("enabled"):
            raise ValueError(f"unsupported language: {normalized}")
        return row

    raise ValueError(f"unsupported language: {normalized}")


def _build_prompt(lang_name: str, *, intro: str, locale: str | None = None, extra: str | None = None) -> str:
    lines = [
        intro,
        "",
        "输入格式",
        "请翻译并本土化 `{{SOURCE_TEXT}}` 中的固定三段内容：",
        "标题: ...",
        "文案: ...",
        "描述: ...",
        "",
        "输出格式",
        "必须严格输出三行，且只能使用下面格式：",
        "标题:[...]",
        "文案:[...]",
        "描述:[...]",
        "",
        "要求",
        "- 保留原意、关键信息和语气。",
        f"- 语言要自然、准确，符合{lang_name}用户的阅读习惯。",
    ]

    if locale:
        lines.append(f"- 优先采用符合 {locale} 的自然表达。")
    if extra:
        lines.append(f"- {extra}")

    lines.extend(
        [
            "- 保持三段对应关系，分别处理标题、文案、描述。",
            "- 不要解释，不要添加引号，不要输出多余内容。",
            "- 必须保留 `{{SOURCE_TEXT}}` 作为输入占位符。",
        ]
    )
    return "\n".join(lines) + "\n"


def _build_special_prompt(lang_name: str, expert: str, audience: str, locale: str, extra: str) -> str:
    intro = f"你是一位{expert}，擅长将面向{audience}的内容翻译并本土化为自然的三段式文案。"
    return _build_prompt(lang_name, intro=intro, locale=locale, extra=extra)


def _build_generic_prompt(lang_name: str) -> str:
    intro = f"你是一位专业的{lang_name}翻译助手，擅长将内容翻译并本土化为自然的三段式文案。"
    return _build_prompt(lang_name, intro=intro)


def get_prompt(code: str) -> str:
    """返回启用语种的标题翻译 prompt。"""
    lang = get_title_translate_language(code)
    normalized = _normalize_code(lang.get("code"))
    lang_name = (lang.get("name_zh") or normalized).strip() or normalized
    hint = _SPECIAL_PROMPT_HINTS.get(normalized)
    if hint:
        return _build_special_prompt(
            lang_name,
            hint["expert"],
            hint["audience"],
            hint["locale"],
            hint["extra"],
        )
    return _build_generic_prompt(lang_name)
