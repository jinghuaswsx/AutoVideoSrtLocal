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
    "sv": {
        "expert": "瑞典语本土化专家",
        "audience": "瑞典用户",
        "locale": "naturlig svenska",
        "extra": "优先采用自然、贴近瑞典用户日常阅读的表达。",
    },
}


# Few-shot 共用的英文输入：钥匙扣文案，刻意包含一个英文疑问句标题，
# 用于演示「即使是疑问句/品牌口号也必须翻译成目标语言」的力度。
_FEW_SHOT_SOURCE = (
    "标题: What's on Your Keychain?\n"
    "文案: Mine opens bottles, starts fires, and looks cool. "
    "Upgrade your everyday carry with this 3-in-1 survival tool.\n"
    "描述: Discover the 3-in-1"
)

# 各特化语种的正确示例输出。生成 prompt 时按 code 取出，附在 prompt 末尾。
# 注意：行首必须以中文「标题:」「文案:」「描述:」开头，绝不能本地化为目标语言。
_FEW_SHOT_OUTPUTS: dict[str, str] = {
    "de": (
        "标题: Was hängt an deinem Schlüsselbund?\n"
        "文案: Meiner öffnet Flaschen, entfacht Feuer und sieht obendrein cool aus. "
        "Bring deine Alltagsausrüstung mit diesem 3-in-1-Survival-Tool aufs nächste Level.\n"
        "描述: Entdecke das 3-in-1"
    ),
    "fr": (
        "标题: Qu'y a-t-il sur ton porte-clés ?\n"
        "文案: Le mien ouvre des bouteilles, allume des feux et a la classe. "
        "Améliore ton EDC avec cet outil de survie 3-en-1.\n"
        "描述: Découvrez le 3-en-1"
    ),
    "es": (
        "标题: ¿Qué hay en tu llavero?\n"
        "文案: El mío abre botellas, enciende fuegos y luce genial. "
        "Mejora tu EDC con esta herramienta de supervivencia 3 en 1.\n"
        "描述: Descubre el 3 en 1"
    ),
    "it": (
        "标题: Cosa c'è sul tuo portachiavi?\n"
        "文案: Il mio apre bottiglie, accende fuochi e fa colpo. "
        "Aggiorna il tuo kit quotidiano con questo strumento di sopravvivenza 3 in 1.\n"
        "描述: Scopri il 3 in 1"
    ),
    "ja": (
        "标题: あなたのキーホルダーには何が？\n"
        "文案: 僕のはボトルを開け、火を起こし、しかもかっこいい。"
        "この3-in-1サバイバルツールで、毎日のキャリーをアップグレードしよう。\n"
        "描述: 3-in-1を発見"
    ),
    "pt": (
        "标题: O que tem no seu chaveiro?\n"
        "文案: O meu abre garrafas, acende fogos e impressiona. "
        "Atualize seu kit do dia a dia com esta ferramenta de sobrevivência 3 em 1.\n"
        "描述: Descubra o 3 em 1"
    ),
    "sv": (
        "标题: Vad sitter på din nyckelring?\n"
        "文案: Min öppnar flaskor, tänder eldar och ser cool ut. "
        "Uppgradera din vardagsutrustning med detta 3-i-1-överlevnadsverktyg.\n"
        "描述: Upptäck 3-i-1"
    ),
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


def _build_prompt(
    lang_name: str,
    *,
    intro: str,
    locale: str | None = None,
    extra: str | None = None,
    example_output: str | None = None,
) -> str:
    lines = [
        intro,
        "",
        "输入格式",
        "请翻译并本土化 `{{SOURCE_TEXT}}` 中的固定三段内容，原文格式为：",
        "标题: <英文标题>",
        "文案: <英文文案>",
        "描述: <英文描述>",
        "",
        "输出格式",
        "严格输出三行，结构为：固定中文字段名 + 半角冒号 + 半角空格 + 译文。",
        f"标题: <{lang_name}译文>",
        f"文案: <{lang_name}译文>",
        f"描述: <{lang_name}译文>",
        "",
        "要求",
        (
            "- 行首的「标题」「文案」「描述」是固定的中文结构标签，必须**逐字保留为这三个中文词**，"
            f"绝对不允许翻译为{lang_name}或其他任何语言"
            "（例如不要改写为「Title」「Titel」「Titolo」「Título」「Titre」「タイトル」「標題」"
            "「Rubrik」「Otsikko」「Onderwerp」等）。"
        ),
        (
            f"- 冒号后只能直接跟你的{lang_name}译文，**不允许在译文之前再加任何形式的字段名前缀**"
            "（例如「タイトル: 」「標題: 」「Titel: 」「Title: 」「Titolo: 」等都属于禁止）。"
        ),
        (
            f"- 必须将标题、文案、描述三段全部译为{lang_name}；"
            f"即使原文看起来像品牌名、口号、疑问句或单个英文短语，也必须给出{lang_name}译文，"
            "不允许在输出中保留任何英文单词或短语。"
        ),
        "- 保留原意、关键信息和语气。",
        f"- 语言要自然、准确，符合{lang_name}用户的阅读习惯。",
    ]

    if locale:
        lines.append(f"- 优先采用符合 {locale} 的自然表达。")
    if extra:
        lines.append(f"- {extra}")
    lines.extend(
        [
            "- 输出中不得出现任何方括号 []、尖括号 <>、花括号 {}，也不要给译文添加引号。",
            "- 标题最多 100 个字符。",
            "- 文案最多 200 个字符。",
            "- 描述最多 50 个字符。",
            "- 保持三段对应关系，分别处理标题、文案、描述。",
            "- 不要解释，不要输出多余内容。",
        ]
    )

    if example_output:
        lines.extend(
            [
                "",
                "示例（仅演示格式与翻译力度，请勿照搬内容）",
                "英文输入：",
                _FEW_SHOT_SOURCE,
                "",
                f"正确的{lang_name}输出：",
                example_output,
            ]
        )

    return "\n".join(lines) + "\n"


def _build_special_prompt(
    code: str,
    lang_name: str,
    expert: str,
    audience: str,
    locale: str,
    extra: str,
) -> str:
    intro = f"你是一位{expert}，擅长将面向{audience}的内容翻译并本土化为自然的三段式文案。"
    return _build_prompt(
        lang_name,
        intro=intro,
        locale=locale,
        extra=extra,
        example_output=_FEW_SHOT_OUTPUTS.get(code),
    )


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
            normalized,
            lang_name,
            hint["expert"],
            hint["audience"],
            hint["locale"],
            hint["extra"],
        )
    return _build_generic_prompt(lang_name)
