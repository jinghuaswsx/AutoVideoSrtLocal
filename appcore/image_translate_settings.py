"""图片翻译默认 prompt 管理，使用 system_settings 表。

每种目标语言 × 两种预设（cover / detail）= 12 条 prompt，
系统级默认硬编码，管理员可逐条覆盖。
"""
from __future__ import annotations

from appcore.db import execute, query_one
from appcore import medias


# 支持的目标语言（与 media_languages 表 enabled=1 的小语种保持一致；en 是源语言）
SUPPORTED_LANGS: tuple[str, ...] = ("de", "fr", "es", "it", "ja", "pt", "nl", "sv", "fi")
PRESETS: tuple[str, ...] = ("cover", "detail")

# 图片翻译 Gemini 通道（全局配置，存 system_settings）
CHANNELS: tuple[str, ...] = ("aistudio", "cloud", "openrouter", "doubao")
CHANNEL_LABELS: dict[str, str] = {
    "aistudio": "Google AI Studio",
    "cloud": "Google Cloud (Vertex AI)",
    "openrouter": "OpenRouter",
    "doubao": "豆包",
}
_CHANNEL_KEY = "image_translate.channel"
_DEFAULT_CHANNEL = "aistudio"
_DEFAULT_MODEL_KEY_PREFIX = "image_translate.default_model."


def _key(preset: str, lang: str) -> str:
    return f"image_translate.prompt_{preset}_{lang}"


def _default_model_key(channel: str) -> str:
    return f"{_DEFAULT_MODEL_KEY_PREFIX}{channel}"


def _normalize_language_code(code: str) -> str:
    return (code or "").strip().lower()


def _normalize_channel(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in CHANNELS else _DEFAULT_CHANNEL


def list_image_translate_languages() -> list[dict]:
    """返回图片翻译可用语种，过滤掉源语言 en。"""
    return [
        row
        for row in medias.list_languages()
        if _normalize_language_code(row.get("code")) != "en"
    ]


def is_image_translate_language_supported(code: str) -> bool:
    normalized = _normalize_language_code(code)
    return any(
        _normalize_language_code(lang.get("code")) == normalized
        for lang in list_image_translate_languages()
    )


def _get_language_info(code: str) -> dict:
    normalized = _normalize_language_code(code)
    for lang in list_image_translate_languages():
        if _normalize_language_code(lang.get("code")) == normalized:
            return lang
    raise ValueError(f"unsupported lang: {normalized}")


def _build_generic_prompt(preset: str, lang_info: dict) -> str:
    lang_name = lang_info.get("name_zh") or lang_info.get("code") or ""
    if preset == "cover":
        return f"""Task: Localize this English video cover image into {lang_name}.

Core Rules:
1. Keep all visual elements (background, images, graphics) completely unchanged - only replace the English text with {lang_name}
2. Text position, size, layout style, font weight, color, shadow/stroke effects must stay consistent with the original
3. 只替换文字，保留布局；如果目标语言文本变长，可以轻微缩小字号以适配原文本区域，但绝不能溢出或破坏布局
4. Output size must be strictly the same as the input image

Translation Requirements:
- Use natural {lang_name} that fits short-video cover copy
- Do not translate word-for-word from English; rewrite it so it reads naturally in {lang_name}
- Keep the hierarchy intact: headline stays prominent, supporting text stays secondary
"""

    return f"""你是一位专业的{lang_name}产品详情图本地化专家。

## 任务
将此产品详情图中的所有英文说明翻译成{lang_name}。

## 规则
- 只替换文字，保留背景、图片、图标、颜色和版式不变
- 保持原始文本的位置、对齐方式、字号层级、字体粗细和视觉结构
- 只替换文字，保留布局；如果{lang_name}文本比英文更长，可以轻微缩小字号以适配原文本区域，但不可溢出或破坏布局
- 输出分辨率和图片尺寸必须与输入完全一致

## 质量要求
- 使用自然、地道的{lang_name}表达，避免生硬的逐字直译
- 保留所有非文本视觉元素，不做任何修改
"""


# ============================================================
# 详情图（产品详情图翻译）— 6 种语言
# ============================================================

_DETAIL_DE = """你是一位专业的产品列表图片本地化专家，专门从事电商平台的英语到德语翻译。

## 任务
将此产品详情图中的所有描述性/说明性文本从英语翻译成德语。

## 规则

### 需要翻译的内容
- 详情图上的所有营销文案、功能描述、规格说明、标注、徽章以及任何其他说明性文本。

### 不得翻译的内容
- 任何物理印刷、压印、雕刻或显示在产品本身上的英文单词/文本（品牌名称、型号名称、产品主体上的标签）。这些必须保持原样。

### 翻译质量
- 产出自然、流畅、母语级别的德语——如同由德语文案撰写人撰写，而非机器翻译。
- 使用地道的德语措辞、正确的语法（包括性别冠词、复合名词以及符合德语正字法规则的名词大写）。
- 调整营销语气以引起德语消费者（DACH 市场）的共鸣。避免过于字面的逐字翻译。

### 视觉布局
- 保持与原始图片完全相同的文本位置、对齐方式、字体样式、字体粗细和视觉层次结构。
- 按比例匹配原始文本大小。如果德语文本比英语长，请略微调整字体大小以适应相同的边界区域，而不是溢出或破坏布局。

### 输出规格
- 输出图片的分辨率和尺寸必须与输入图片完全相同（像素级精确匹配）。
- 保留所有非文本视觉元素（产品照片、背景、图标、配色方案），不做任何修改。

### 文件命名
- 导出文件名：[原始文件名]-German.[原始扩展名]
  示例：product-detail-01.jpg → product-detail-01-German.jpg
"""

_DETAIL_FR = """你是一位专业的产品列表图片本地化专家，专门从事电商平台的英语到法语翻译。

## 任务
将此产品详情图中的所有描述性/说明性文本从英语翻译成法语。

## 规则

### 需要翻译的内容
- 详情图上的所有营销文案、功能描述、规格说明、标注、徽章以及任何其他说明性文本。

### 不得翻译的内容
- 任何物理印刷、压印、雕刻或显示在产品本身上的英文单词/文本（品牌名称、型号名称、产品主体上的标签）。这些必须保持原样。

### 翻译质量
- 产出自然、流畅、母语级别的法语——如同由法语文案撰写人撰写，而非机器翻译。
- 使用地道的法语措辞、正确的语法（阴阳性冠词 le/la、性数配合、重音符号 é è ê à ç 以及正确的动词变位）。
- 调整营销语气以引起法语消费者（法国及法语区欧洲市场）的共鸣。避免过于字面的逐字翻译。

### 视觉布局
- 保持与原始图片完全相同的文本位置、对齐方式、字体样式、字体粗细和视觉层次结构。
- 按比例匹配原始文本大小。如果法语文本比英语长，请略微调整字体大小以适应相同的边界区域，而不是溢出或破坏布局。

### 输出规格
- 输出图片的分辨率和尺寸必须与输入图片完全相同（像素级精确匹配）。
- 保留所有非文本视觉元素（产品照片、背景、图标、配色方案），不做任何修改。

### 文件命名
- 导出文件名：[原始文件名]-French.[原始扩展名]
  示例：product-detail-01.jpg → product-detail-01-French.jpg
"""

_DETAIL_ES = """你是一位专业的产品列表图片本地化专家，专门从事电商平台的英语到西班牙语翻译。

## 任务
将此产品详情图中的所有描述性/说明性文本从英语翻译成西班牙语。

## 规则

### 需要翻译的内容
- 详情图上的所有营销文案、功能描述、规格说明、标注、徽章以及任何其他说明性文本。

### 不得翻译的内容
- 任何物理印刷、压印、雕刻或显示在产品本身上的英文单词/文本（品牌名称、型号名称、产品主体上的标签）。这些必须保持原样。

### 翻译质量
- 产出自然、流畅、母语级别的西班牙语——如同由西班牙语文案撰写人撰写，而非机器翻译。
- 使用地道的西班牙语措辞、正确的语法（阴阳性冠词 el/la、性数配合、重音符号 á é í ó ú ñ、倒置标点 ¿ ¡）。
- 调整营销语气以引起西班牙语消费者（西班牙及拉丁美洲市场）的共鸣。避免过于字面的逐字翻译。

### 视觉布局
- 保持与原始图片完全相同的文本位置、对齐方式、字体样式、字体粗细和视觉层次结构。
- 按比例匹配原始文本大小。如果西班牙语文本比英语长，请略微调整字体大小以适应相同的边界区域，而不是溢出或破坏布局。

### 输出规格
- 输出图片的分辨率和尺寸必须与输入图片完全相同（像素级精确匹配）。
- 保留所有非文本视觉元素（产品照片、背景、图标、配色方案），不做任何修改。

### 文件命名
- 导出文件名：[原始文件名]-Spanish.[原始扩展名]
  示例：product-detail-01.jpg → product-detail-01-Spanish.jpg
"""

_DETAIL_IT = """你是一位专业的产品列表图片本地化专家，专门从事电商平台的英语到意大利语翻译。

## 任务
将此产品详情图中的所有描述性/说明性文本从英语翻译成意大利语。

## 规则

### 需要翻译的内容
- 详情图上的所有营销文案、功能描述、规格说明、标注、徽章以及任何其他说明性文本。

### 不得翻译的内容
- 任何物理印刷、压印、雕刻或显示在产品本身上的英文单词/文本（品牌名称、型号名称、产品主体上的标签）。这些必须保持原样。

### 翻译质量
- 产出自然、流畅、母语级别的意大利语——如同由意大利语文案撰写人撰写，而非机器翻译。
- 使用地道的意大利语措辞、正确的语法（阴阳性冠词 il/la/lo、介词冠词组合 del/della/al 等、重音符号 à è é ì ò ù）。
- 调整营销语气以引起意大利语消费者（意大利市场）的共鸣。避免过于字面的逐字翻译。

### 视觉布局
- 保持与原始图片完全相同的文本位置、对齐方式、字体样式、字体粗细和视觉层次结构。
- 按比例匹配原始文本大小。如果意大利语文本比英语长，请略微调整字体大小以适应相同的边界区域，而不是溢出或破坏布局。

### 输出规格
- 输出图片的分辨率和尺寸必须与输入图片完全相同（像素级精确匹配）。
- 保留所有非文本视觉元素（产品照片、背景、图标、配色方案），不做任何修改。

### 文件命名
- 导出文件名：[原始文件名]-Italian.[原始扩展名]
  示例：product-detail-01.jpg → product-detail-01-Italian.jpg
"""

_DETAIL_JA = """你是一位专业的产品列表图片本地化专家，专门从事电商平台的英语到日语翻译。

## 任务
将此产品详情图中的所有描述性/说明性文本从英语翻译成日语。

## 规则

### 需要翻译的内容
- 详情图上的所有营销文案、功能描述、规格说明、标注、徽章以及任何其他说明性文本。

### 不得翻译的内容
- 任何物理印刷、压印、雕刻或显示在产品本身上的英文单词/文本（品牌名称、型号名称、产品主体上的标签）。这些必须保持原样。

### 翻译质量
- 产出自然、流畅、母语级别的日语——如同由日语文案撰写人撰写，而非机器翻译。
- 合理运用汉字、平假名、片假名（外来语片假名化）及助词结构；营销类文案可使用简洁有力的体言止、感叹号等电商常见手法。
- 调整营销语气以引起日本消费者（日本市场）的共鸣。避免过于字面的逐字翻译。

### 视觉布局
- 保持与原始图片完全相同的文本位置、对齐方式、字体样式、字体粗细和视觉层次结构。
- 按比例匹配原始文本大小。如果日语文本比英语长（或更紧凑），请略微调整字体大小以适应相同的边界区域，而不是溢出或破坏布局。

### 输出规格
- 输出图片的分辨率和尺寸必须与输入图片完全相同（像素级精确匹配）。
- 保留所有非文本视觉元素（产品照片、背景、图标、配色方案），不做任何修改。

### 文件命名
- 导出文件名：[原始文件名]-Japanese.[原始扩展名]
  示例：product-detail-01.jpg → product-detail-01-Japanese.jpg
"""

_DETAIL_PT = """你是一位专业的产品列表图片本地化专家，专门从事电商平台的英语到葡萄牙语翻译。

## 任务
将此产品详情图中的所有描述性/说明性文本从英语翻译成葡萄牙语（以巴西葡语为主，如面向欧洲葡语请相应调整）。

## 规则

### 需要翻译的内容
- 详情图上的所有营销文案、功能描述、规格说明、标注、徽章以及任何其他说明性文本。

### 不得翻译的内容
- 任何物理印刷、压印、雕刻或显示在产品本身上的英文单词/文本（品牌名称、型号名称、产品主体上的标签）。这些必须保持原样。

### 翻译质量
- 产出自然、流畅、母语级别的葡萄牙语——如同由葡语文案撰写人撰写，而非机器翻译。
- 使用地道的葡语措辞、正确的语法（阴阳性冠词 o/a、动词变位、重音符号 á à â ã ç ê 等）。
- 调整营销语气以引起葡语消费者（巴西及葡萄牙市场）的共鸣。避免过于字面的逐字翻译。

### 视觉布局
- 保持与原始图片完全相同的文本位置、对齐方式、字体样式、字体粗细和视觉层次结构。
- 按比例匹配原始文本大小。如果葡语文本比英语长，请略微调整字体大小以适应相同的边界区域，而不是溢出或破坏布局。

### 输出规格
- 输出图片的分辨率和尺寸必须与输入图片完全相同（像素级精确匹配）。
- 保留所有非文本视觉元素（产品照片、背景、图标、配色方案），不做任何修改。

### 文件命名
- 导出文件名：[原始文件名]-Portuguese.[原始扩展名]
  示例：product-detail-01.jpg → product-detail-01-Portuguese.jpg
"""

_DETAIL_NL = """你是一位专业的产品列表图片本地化专家，专门从事电商平台的英语到荷兰语翻译。

## 任务
将此产品详情图中的所有描述性/说明性文本从英语翻译成荷兰语。

## 规则

### 需要翻译的内容
- 详情图上的所有营销文案、功能描述、规格说明、标注、徽章以及任何其他说明性文本。

### 不得翻译的内容
- 任何物理印刷、压印、雕刻或显示在产品本身上的英文单词/文本（品牌名称、型号名称、产品主体上的标签）。这些必须保持原样。

### 翻译质量
- 产出自然、流畅、母语级别的荷兰语——如同由荷兰语文案撰写人撰写，而非机器翻译。
- 使用地道的荷兰语措辞、正确的语法（包括 de/het 冠词、词序、复合词以及符合荷兰语正字法的重音符号与分音符）。
- 调整营销语气以引起荷兰语消费者（荷兰及比利时弗拉芒区市场）的共鸣。避免过于字面的逐字翻译。

### 视觉布局
- 保持与原始图片完全相同的文本位置、对齐方式、字体样式、字体粗细和视觉层次结构。
- 按比例匹配原始文本大小。如果荷兰语文本比英语长，请略微调整字体大小以适应相同的边界区域，而不是溢出或破坏布局。

### 输出规格
- 输出图片的分辨率和尺寸必须与输入图片完全相同（像素级精确匹配）。
- 保留所有非文本视觉元素（产品照片、背景、图标、配色方案），不做任何修改。

### 文件命名
- 导出文件名：[原始文件名]-Dutch.[原始扩展名]
  示例：product-detail-01.jpg → product-detail-01-Dutch.jpg
"""

_DETAIL_SV = """你是一位专业的产品列表图片本地化专家，专门从事电商平台的英语到瑞典语翻译。

## 任务
将此产品详情图中的所有描述性/说明性文本从英语翻译成瑞典语。

## 规则

### 需要翻译的内容
- 详情图上的所有营销文案、功能描述、规格说明、标注、徽章以及任何其他说明性文本。

### 不得翻译的内容
- 任何物理印刷、压印、雕刻或显示在产品本身上的英文单词/文本（品牌名称、型号名称、产品主体上的标签）。这些必须保持原样。

### 翻译质量
- 产出自然、流畅、母语级别的瑞典语——如同由瑞典语文案撰写人撰写，而非机器翻译。
- 使用地道的瑞典语措辞、正确的语法（包括 en/ett 冠词、定冠后缀、复合词以及 å/ä/ö 等瑞典语字母的正确使用）。
- 调整营销语气以引起瑞典语消费者（瑞典市场）的共鸣。避免过于字面的逐字翻译。

### 视觉布局
- 保持与原始图片完全相同的文本位置、对齐方式、字体样式、字体粗细和视觉层次结构。
- 按比例匹配原始文本大小。如果瑞典语文本比英语长，请略微调整字体大小以适应相同的边界区域，而不是溢出或破坏布局。

### 输出规格
- 输出图片的分辨率和尺寸必须与输入图片完全相同（像素级精确匹配）。
- 保留所有非文本视觉元素（产品照片、背景、图标、配色方案），不做任何修改。

### 文件命名
- 导出文件名：[原始文件名]-Swedish.[原始扩展名]
  示例：product-detail-01.jpg → product-detail-01-Swedish.jpg
"""

_DETAIL_FI = """你是一位专业的产品列表图片本地化专家，专门从事电商平台的英语到芬兰语翻译。

## 任务
将此产品详情图中的所有描述性/说明性文本从英语翻译成芬兰语。

## 规则

### 需要翻译的内容
- 详情图上的所有营销文案、功能描述、规格说明、标注、徽章以及任何其他说明性文本。

### 不得翻译的内容
- 任何物理印刷、压印、雕刻或显示在产品本身上的英文单词/文本（品牌名称、型号名称、产品主体上的标签）。这些必须保持原样。

### 翻译质量
- 产出自然、流畅、母语级别的芬兰语——如同由芬兰语文案撰写人撰写，而非机器翻译。
- 使用地道的芬兰语措辞、正确的语法（包括格变化、元音和谐、复合词以及 ä/ö 等芬兰语字母的正确使用）。
- 调整营销语气以引起芬兰语消费者（芬兰市场）的共鸣。避免过于字面的逐字翻译。

### 视觉布局
- 保持与原始图片完全相同的文本位置、对齐方式、字体样式、字体粗细和视觉层次结构。
- 按比例匹配原始文本大小。如果芬兰语文本比英语长，请略微调整字体大小以适应相同的边界区域，而不是溢出或破坏布局。

### 输出规格
- 输出图片的分辨率和尺寸必须与输入图片完全相同（像素级精确匹配）。
- 保留所有非文本视觉元素（产品照片、背景、图标、配色方案），不做任何修改。

### 文件命名
- 导出文件名：[原始文件名]-Finnish.[原始扩展名]
  示例：product-detail-01.jpg → product-detail-01-Finnish.jpg
"""


# ============================================================
# 封面图（视频封面图翻译）— 6 种语言
# ============================================================

_COVER_DE = """Task: Localize this English video cover image into German.

【Core Rules】
1. Keep all visual elements (background, images, graphics) completely unchanged — only replace the English text with German
2. Text position, size, layout style, font weight, color, shadow/stroke effects must stay consistent with the original
3. Output size must be strictly 1080×1920 pixels (vertical 9:16)

【German Translation Requirements】
- You are a native-level German short-video content creator; the translation must feel natural to a German audience
- Use the casual, attention-grabbing style commonly seen on German social media / short-video platforms
- Do NOT translate word-for-word from English — restructure the language to sound native and idiomatic in German
- Headlines / large text should be punchy and compelling, like a viral German YouTube/TikTok thumbnail
- Subtitles or secondary text must also be fully localized

【Layout Constraints】
- Each text element must appear in the same position as in the original image
- If the German text is longer than the English, slightly reduce the font size to fit within the original text area — no overflow allowed
- Maintain the original visual hierarchy (main title prominent, subtitle secondary)
"""

_COVER_FR = """Task: Localize this English video cover image into French.

【Core Rules】
1. Keep all visual elements (background, images, graphics) completely unchanged — only replace the English text with French
2. Text position, size, layout style, font weight, color, shadow/stroke effects must stay consistent with the original
3. Output size must be strictly 1080×1920 pixels (vertical 9:16)

【French Translation Requirements】
- You are a native-level French short-video content creator; the translation must feel natural to a French-speaking audience
- Use the casual, attention-grabbing style commonly seen on French social media / short-video platforms
- Do NOT translate word-for-word from English — restructure the language to sound native and idiomatic in French
- Headlines / large text should be punchy and compelling, like a viral French YouTube/TikTok thumbnail
- Subtitles or secondary text must also be fully localized

【Layout Constraints】
- Each text element must appear in the same position as in the original image
- If the French text is longer than the English, slightly reduce the font size to fit within the original text area — no overflow allowed
- Maintain the original visual hierarchy (main title prominent, subtitle secondary)
"""

_COVER_ES = """Task: Localize this English video cover image into Spanish.

【Core Rules】
1. Keep all visual elements (background, images, graphics) completely unchanged — only replace the English text with Spanish
2. Text position, size, layout style, font weight, color, shadow/stroke effects must stay consistent with the original
3. Output size must be strictly 1080×1920 pixels (vertical 9:16)

【Spanish Translation Requirements】
- You are a native-level Spanish short-video content creator; the translation must feel natural to a Spanish-speaking audience (Spain / LATAM)
- Use the casual, attention-grabbing style commonly seen on Spanish-speaking social media / short-video platforms
- Do NOT translate word-for-word from English — restructure the language to sound native and idiomatic in Spanish
- Headlines / large text should be punchy and compelling, like a viral Spanish YouTube/TikTok thumbnail
- Subtitles or secondary text must also be fully localized

【Layout Constraints】
- Each text element must appear in the same position as in the original image
- If the Spanish text is longer than the English, slightly reduce the font size to fit within the original text area — no overflow allowed
- Maintain the original visual hierarchy (main title prominent, subtitle secondary)
"""

_COVER_IT = """Task: Localize this English video cover image into Italian.

【Core Rules】
1. Keep all visual elements (background, images, graphics) completely unchanged — only replace the English text with Italian
2. Text position, size, layout style, font weight, color, shadow/stroke effects must stay consistent with the original
3. Output size must be strictly 1080×1920 pixels (vertical 9:16)

【Italian Translation Requirements】
- You are a native-level Italian short-video content creator; the translation must feel natural to an Italian audience
- Use the casual, attention-grabbing style commonly seen on Italian social media / short-video platforms
- Do NOT translate word-for-word from English — restructure the language to sound native and idiomatic in Italian
- Headlines / large text should be punchy and compelling, like a viral Italian YouTube/TikTok thumbnail
- Subtitles or secondary text must also be fully localized

【Layout Constraints】
- Each text element must appear in the same position as in the original image
- If the Italian text is longer than the English, slightly reduce the font size to fit within the original text area — no overflow allowed
- Maintain the original visual hierarchy (main title prominent, subtitle secondary)
"""

_COVER_JA = """Task: Localize this English video cover image into Japanese.

【Core Rules】
1. Keep all visual elements (background, images, graphics) completely unchanged — only replace the English text with Japanese
2. Text position, size, layout style, font weight, color, shadow/stroke effects must stay consistent with the original
3. Output size must be strictly 1080×1920 pixels (vertical 9:16)

【Japanese Translation Requirements】
- You are a native-level Japanese short-video content creator; the translation must feel natural to a Japanese audience
- Use the casual, attention-grabbing style commonly seen on Japanese social media / short-video platforms (e.g. TikTok Japan, YouTube Shorts)
- Make appropriate use of kanji, hiragana and katakana (especially for loanwords); punchy headlines may use noun-ending phrases or exclamation marks typical for Japanese thumbnails
- Do NOT translate word-for-word from English — restructure the language to sound native and idiomatic in Japanese
- Subtitles or secondary text must also be fully localized

【Layout Constraints】
- Each text element must appear in the same position as in the original image
- If the Japanese text is longer or more compact than the English, slightly adjust the font size to fit within the original text area — no overflow allowed
- Maintain the original visual hierarchy (main title prominent, subtitle secondary)
"""

_COVER_PT = """Task: Localize this English video cover image into Portuguese (Brazilian Portuguese by default; adjust for European Portuguese if the context implies it).

【Core Rules】
1. Keep all visual elements (background, images, graphics) completely unchanged — only replace the English text with Portuguese
2. Text position, size, layout style, font weight, color, shadow/stroke effects must stay consistent with the original
3. Output size must be strictly 1080×1920 pixels (vertical 9:16)

【Portuguese Translation Requirements】
- You are a native-level Portuguese short-video content creator; the translation must feel natural to a Portuguese-speaking audience
- Use the casual, attention-grabbing style commonly seen on Portuguese-speaking social media / short-video platforms
- Do NOT translate word-for-word from English — restructure the language to sound native and idiomatic in Portuguese
- Headlines / large text should be punchy and compelling, like a viral Portuguese YouTube/TikTok thumbnail
- Subtitles or secondary text must also be fully localized

【Layout Constraints】
- Each text element must appear in the same position as in the original image
- If the Portuguese text is longer than the English, slightly reduce the font size to fit within the original text area — no overflow allowed
- Maintain the original visual hierarchy (main title prominent, subtitle secondary)
"""

_COVER_NL = """Task: Localize this English video cover image into Dutch.

【Core Rules】
1. Keep all visual elements (background, images, graphics) completely unchanged — only replace the English text with Dutch
2. Text position, size, layout style, font weight, color, shadow/stroke effects must stay consistent with the original
3. Output size must be strictly 1080×1920 pixels (vertical 9:16)

【Dutch Translation Requirements】
- You are a native-level Dutch short-video content creator; the translation must feel natural to a Dutch audience
- Use the casual, attention-grabbing style commonly seen on Dutch social media / short-video platforms
- Do NOT translate word-for-word from English — restructure the language to sound native and idiomatic in Dutch
- Headlines / large text should be punchy and compelling, like a viral Dutch YouTube/TikTok thumbnail
- Subtitles or secondary text must also be fully localized

【Layout Constraints】
- Each text element must appear in the same position as in the original image
- If the Dutch text is longer than the English, slightly reduce the font size to fit within the original text area — no overflow allowed
- Maintain the original visual hierarchy (main title prominent, subtitle secondary)
"""

_COVER_SV = """Task: Localize this English video cover image into Swedish.

【Core Rules】
1. Keep all visual elements (background, images, graphics) completely unchanged — only replace the English text with Swedish
2. Text position, size, layout style, font weight, color, shadow/stroke effects must stay consistent with the original
3. Output size must be strictly 1080×1920 pixels (vertical 9:16)

【Swedish Translation Requirements】
- You are a native-level Swedish short-video content creator; the translation must feel natural to a Swedish audience
- Use the casual, attention-grabbing style commonly seen on Swedish social media / short-video platforms
- Do NOT translate word-for-word from English — restructure the language to sound native and idiomatic in Swedish
- Headlines / large text should be punchy and compelling, like a viral Swedish YouTube/TikTok thumbnail
- Subtitles or secondary text must also be fully localized

【Layout Constraints】
- Each text element must appear in the same position as in the original image
- If the Swedish text is longer than the English, slightly reduce the font size to fit within the original text area — no overflow allowed
- Maintain the original visual hierarchy (main title prominent, subtitle secondary)
"""

_COVER_FI = """Task: Localize this English video cover image into Finnish.

【Core Rules】
1. Keep all visual elements (background, images, graphics) completely unchanged — only replace the English text with Finnish
2. Text position, size, layout style, font weight, color, shadow/stroke effects must stay consistent with the original
3. Output size must be strictly 1080×1920 pixels (vertical 9:16)

【Finnish Translation Requirements】
- You are a native-level Finnish short-video content creator; the translation must feel natural to a Finnish audience
- Use the casual, attention-grabbing style commonly seen on Finnish social media / short-video platforms
- Do NOT translate word-for-word from English — restructure the language to sound native and idiomatic in Finnish
- Headlines / large text should be punchy and compelling, like a viral Finnish YouTube/TikTok thumbnail
- Subtitles or secondary text must also be fully localized

【Layout Constraints】
- Each text element must appear in the same position as in the original image
- If the Finnish text is longer than the English, slightly reduce the font size to fit within the original text area — no overflow allowed
- Maintain the original visual hierarchy (main title prominent, subtitle secondary)
"""


_DEFAULTS: dict[tuple[str, str], str] = {
    ("cover", "de"): _COVER_DE,
    ("cover", "fr"): _COVER_FR,
    ("cover", "es"): _COVER_ES,
    ("cover", "it"): _COVER_IT,
    ("cover", "ja"): _COVER_JA,
    ("cover", "pt"): _COVER_PT,
    ("cover", "nl"): _COVER_NL,
    ("cover", "sv"): _COVER_SV,
    ("cover", "fi"): _COVER_FI,
    ("detail", "de"): _DETAIL_DE,
    ("detail", "fr"): _DETAIL_FR,
    ("detail", "es"): _DETAIL_ES,
    ("detail", "it"): _DETAIL_IT,
    ("detail", "ja"): _DETAIL_JA,
    ("detail", "pt"): _DETAIL_PT,
    ("detail", "nl"): _DETAIL_NL,
    ("detail", "sv"): _DETAIL_SV,
    ("detail", "fi"): _DETAIL_FI,
}
_REPLACE_STALE_GENERIC_LANGS: set[str] = {"nl", "sv", "fi"}


def _read(key: str) -> str | None:
    row = query_one("SELECT `value` FROM system_settings WHERE `key`=%s", (key,))
    return (row.get("value") or "") if row else None


def _write(key: str, value: str) -> None:
    execute(
        "INSERT INTO system_settings (`key`, `value`) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE `value`=VALUES(`value`)",
        (key, value),
    )


def _is_stale_generic_prompt(preset: str, lang: str, lang_info: dict, value: str) -> bool:
    if lang not in _REPLACE_STALE_GENERIC_LANGS:
        return False
    if (preset, lang) not in _DEFAULTS:
        return False
    return value.strip() == _build_generic_prompt(preset, lang_info).strip()


def get_prompt(preset: str, lang: str) -> str:
    """返回某语言 + 某预设下的 prompt；不存在则写入内置默认后返回。"""
    if preset not in PRESETS:
        raise ValueError("preset must be cover or detail")
    normalized_lang = _normalize_language_code(lang)
    lang_info = _get_language_info(normalized_lang)
    key = _key(preset, normalized_lang)
    value = _read(key)
    default = _DEFAULTS.get((preset, normalized_lang))
    if value is None or value == "":
        if default is None:
            default = _build_generic_prompt(preset, lang_info)
        _write(key, default)
        return default
    if default is not None and _is_stale_generic_prompt(preset, normalized_lang, lang_info, value):
        _write(key, default)
        return default
    return value


def get_prompts_for_lang(lang: str) -> dict[str, str]:
    """返回该语言下的 {cover, detail} 两条 prompt。"""
    return {preset: get_prompt(preset, lang) for preset in PRESETS}


def update_prompt(preset: str, lang: str, value: str) -> None:
    if preset not in PRESETS:
        raise ValueError("preset must be cover or detail")
    normalized_lang = _normalize_language_code(lang)
    _get_language_info(normalized_lang)
    _write(_key(preset, normalized_lang), value)


def get_channel() -> str:
    """返回当前图片翻译通道。未配置或不合法时回退到默认 aistudio。"""
    return _normalize_channel(_read(_CHANNEL_KEY))


def set_channel(value: str) -> None:
    normalized = (value or "").strip().lower()
    if normalized not in CHANNELS:
        raise ValueError(f"unsupported channel: {value}")
    _write(_CHANNEL_KEY, normalized)


def get_default_model(channel: str | None = None) -> str:
    """返回指定通道的全局默认图片翻译模型；未配置时回到该通道内置默认。"""
    normalized_channel = _normalize_channel(channel or get_channel())
    value = (_read(_default_model_key(normalized_channel)) or "").strip()
    from appcore.gemini_image import coerce_image_model

    return coerce_image_model(value, channel=normalized_channel)


def set_default_model(channel: str, model_id: str) -> None:
    normalized_channel = (channel or "").strip().lower()
    if normalized_channel not in CHANNELS:
        raise ValueError(f"unsupported channel: {channel}")
    normalized_model = (model_id or "").strip()
    from appcore.gemini_image import is_valid_image_model

    if not is_valid_image_model(normalized_model, channel=normalized_channel):
        raise ValueError(f"unsupported image model for {normalized_channel}: {model_id}")
    _write(_default_model_key(normalized_channel), normalized_model)


def list_all_prompts() -> dict[str, dict[str, str]]:
    """管理员页面用：返回 {lang: {cover, detail}}（12 条）。"""
    return {
        lang["code"]: get_prompts_for_lang(lang["code"])
        for lang in list_image_translate_languages()
    }
