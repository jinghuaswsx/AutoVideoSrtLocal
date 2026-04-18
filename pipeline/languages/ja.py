"""日语（ja）字幕/TTS 规则。

V1 策略：不依赖外部分词器。
- LLM 在 tts_script 阶段产出短 chunk（≤15 全角字符），直接单行渲染
- 不做字幕折行（Japanese 惯例：短句为主，能一行放下就不折）
- WEAK_STARTERS 仍定义（助词集合），留作后续接入 janome/fugashi 时使用

后续精度不足时再引入 janome（pure Python）或 fugashi（MeCab 绑定）。
"""
from __future__ import annotations

TTS_MODEL_ID = "eleven_multilingual_v2"
TTS_LANGUAGE_CODE = "ja"

# 日语字幕按全角字符计：21 列是业界常用（Netflix / NHK 偏保守 16–18）
MAX_CHARS_PER_LINE = 21
# 日语阅读速度较慢（密度高）：Netflix 规范 13 字/秒
MAX_CHARS_PER_SECOND = 13
MAX_LINES = 2

# 行首不应出现的附着助词（Japanese "後置助詞"）
# 它们在语义上附着在前一个词上，如果出现在行首，说明折行点切错了
WEAK_STARTERS = {
    # 格助詞 / 副助詞 / 終助詞（附着性最强）
    "は", "が", "を", "に", "で", "と", "の", "も", "へ", "や", "か",
    "から", "まで", "ので", "のに", "ため", "など", "こそ", "さえ",
    "でも", "ても", "だけ", "しか", "ばかり", "ほど", "くらい", "ぐらい",
    # 接続助詞
    "けど", "けれど", "けれども", "し", "ば", "ながら",
    # 形式名詞 / 副詞性助詞
    "こと", "もの", "はず", "つもり", "ようだ", "そうだ",
}
WEAK_STARTER_PHRASES: list[str] = []


def pre_process(text: str) -> str:
    """日语无翻译层前处理。未来接入 janome 后可在此做分词 + 插入零宽空格。"""
    return text


def post_process_srt(srt_content: str) -> str:
    """日语 SRT 无需后处理。

    不去掉行首助词——那需要完整分词才能判断；靠 LLM 端在 tts_script prompt
    里遵守"不把助词放在 chunk 开头"的指示。
    """
    return srt_content
