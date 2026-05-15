# ES/IT Video Translation Adapters Design

- Date: 2026-05-15
- Module: multi-translate and omni-translate video pipelines
- Anchor docs:
  - `2026-05-13-multi-translate-language-adapters-design.md`
  - `2026-05-07-omni-translate-merge-design.md`
  - `2026-05-13-tts-deferred-adaptive-speedup-design.md`

## Goal

Spanish (`es`) and Italian (`it`) already have localized base prompts through
`llm_prompt_configs`, but the runtime still treats them like generic languages
for the adapter boundary. Promote both languages to dedicated adapter chains so
future TTS-duration failures are handled before falling back to best-pick,
tempo alignment, or hard truncation.

## Design

Add `pipeline.localization_es` and `pipeline.localization_it` next to the
existing German and French modules. Each module owns:

- TTS script message construction, while preserving admin prompt resolution.
- Rewrite message construction, with language-specific retry guardrails.
- TTS script validation and deterministic cleanup.
- TTS block-to-segment construction through the shared segment mapper.

`MultiTranslateRunner` resolves `es` and `it` through `_ModuleLocalizationAdapter`
instead of the generic prompt adapter.

`OmniTranslateRunner` keeps its source-anchored rewrite behavior, but wraps
`es` and `it` with module-backed omni adapters so the same validators and TTS
builders are used in the all-purpose video translation path.

## Language Rules

Spanish:

- Preserve opening inverted punctuation for question and exclamation TTS text.
- Keep subtitle chunks concise under the same mobile subtitle constraints.
- Rewrite prompts remind the model to preserve `?` / `!` pairing and avoid hype.

Italian:

- Keep apostrophe elisions attached, such as `l'amica`, `d'accordo`, and
  `un'idea`.
- Reject or normalize dangling elision fragments before subtitle chunking.
- Rewrite prompts remind the model to preserve natural informal `tu` style and
  articulated prepositions.

## Non-Goals

- No DB schema changes.
- No new target languages.
- No service restart or deployment.
- No changes to deprecated `de_translate`, `fr_translate`, or `ja_translate`
  standalone runners.

## Verification

Add focused tests that fail before implementation:

1. Multi-translate resolves `es` and `it` to dedicated localization modules.
2. ES/IT adapters keep admin prompt resolution for TTS script and rewrite hooks.
3. Spanish validator deterministically restores missing inverted punctuation.
4. Italian validator keeps apostrophe elisions attached.
5. Omni uses module-backed ES/IT adapters while retaining original ASR text in
   rewrite prompts.
