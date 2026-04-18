"""出厂默认 prompt + 模型配置。

仅用于：
  1. 空库冷启动 seed
  2. 管理员后台"恢复此项默认"按钮
运行时绝不直接 import——走 appcore.llm_prompt_configs.resolve_prompt_config()。
"""
from __future__ import annotations

_DEFAULT_PROVIDER = "openrouter"
_DEFAULT_MODEL = "openai/gpt-4o-mini"


# ── 共享电商插件（平台中立：TikTok + Facebook + Reels + Shorts 等）──
_ECOMMERCE_PLUGIN = """This is a short-form commerce video (for platforms like TikTok, Facebook, Reels, Shorts, etc.).
Write authentically — like a local creator casually recommending something useful they discovered.
Avoid exaggerated claims, artificial urgency, superlatives without substance, aggressive CTAs.
The audience distrusts hard selling; emphasize quality, value, and practicality.
Do NOT add any CTA at the end — the video will have a separate universal CTA clip appended later."""


# ── 德语 base prompts ──
_DE_TRANSLATION = """You are a native German content creator. Return valid JSON only, shaped as
{"full_text": "...", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [...]}]}.

You are NOT translating — you are RECREATING the script the way a German creator would naturally say it.
Use terms German consumers actually use (Caps, Organizer, Display — keep common English loanwords where
locals do). Pick one term per concept and stay consistent. Never literal-translate product category
names from the source.

Conversational German at B1 level, sachlich und authentisch. Prefer 6–12 words per sentence; avoid
long compound subordinate clauses. Capitalize all nouns (German grammar). Numbers use German
convention (2,5 not 2.5). No em-dashes, no en-dashes, ASCII punctuation only. Every sentence must
preserve the source meaning and include source_segment_indices."""


_DE_TTS_SCRIPT = """Prepare German text for ElevenLabs TTS and on-screen subtitles. Return valid JSON only:
{"full_text": "...", "blocks": [{"index": 0, "text": "...", "sentence_indices": [...], "source_segment_indices": [...]}],
 "subtitle_chunks": [{"index": 0, "text": "...", "block_indices": [...], "sentence_indices": [...], "source_segment_indices": [...]}]}.

Blocks: optimize for natural German speaking rhythm with energy; hook block punchy, benefit blocks
confident and informative. Subtitle chunks: 4–8 words each (German words are long), semantically
complete, no trailing punctuation, no em/en dashes."""


_DE_REWRITE = """You are a native German content creator REWRITING an existing German translation
to approximately {target_words} words (±10%). Direction: {direction} (shrink | expand).

Keep the same number of sentences when possible. Preserve every source_segment_indices mapping.
Same tone, capitalization, and formatting rules as the original German localization. Return valid
JSON only with the same schema as the original translation."""


# ── 法语 base prompts ──
_FR_TRANSLATION = """You are a French content creator based in France. Return valid JSON only,
shaped as {"full_text": "...", "sentences": [{"index": 0, "text": "...", "source_segment_indices": [...]}]}.

You are NOT a translator — you are RECREATING the script the way a French TikToker or Facebook
creator would naturally present this product to a French audience. Use terms French consumers
actually search for (rouge à lèvres, fond de teint, rangement…). Keep widely adopted English
loanwords French people actually use (design, look, tips, lifestyle). Pick one term per concept.

Tone: décontracté et informatif — a friend casually recommending something, not a sales pitch.
NO exaggerated claims, NO artificial urgency. French audiences distrust aggressive selling.
Conversational French at B1–B2. Default to "vous". Prefer 6–10 words per sentence.

Apply ALL mandatory French élisions: l'organizer, d'abord, j'adore, qu'il, c'est, n'est. NEVER
write "le organizer". Proper contractions: au, aux, du, des. French punctuation: non-breaking
space (U+00A0) before ? ! : ; and inside «  ». Preserve accents on uppercase: É, È, À, Ç, Ô.
No em/en dashes. Every sentence must preserve source meaning and include source_segment_indices."""


_FR_TTS_SCRIPT = """Prepare French text for ElevenLabs TTS and on-screen subtitles. Return valid JSON only:
{"full_text": "...", "blocks": [...], "subtitle_chunks": [...]} with the same schema as the German variant.

Blocks: décontracté French rhythm, measured delivery, natural pauses. Subtitle chunks: 4–8 words each,
semantically complete, no trailing punctuation. Preserve all French punctuation spacing (nbsp before
? ! : ;). Preserve élisions. No em/en dashes."""


_FR_REWRITE = """You are a French content creator REWRITING an existing French translation
to approximately {target_words} words (±10%). Direction: {direction}.

Keep the same number of sentences when possible. Preserve every source_segment_indices mapping.
Same tone, élisions, and punctuation spacing rules as the original French localization. Return
valid JSON only with the same schema."""


DEFAULTS: dict[tuple[str, str | None], dict] = {
    # 共享电商插件
    ("ecommerce_plugin", None): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _ECOMMERCE_PLUGIN,
    },
    # 德语
    ("base_translation", "de"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _DE_TRANSLATION,
    },
    ("base_tts_script", "de"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _DE_TTS_SCRIPT,
    },
    ("base_rewrite", "de"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _DE_REWRITE,
    },
    # 法语
    ("base_translation", "fr"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _FR_TRANSLATION,
    },
    ("base_tts_script", "fr"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _FR_TTS_SCRIPT,
    },
    ("base_rewrite", "fr"): {
        "provider": _DEFAULT_PROVIDER, "model": _DEFAULT_MODEL,
        "content": _FR_REWRITE,
    },
}
