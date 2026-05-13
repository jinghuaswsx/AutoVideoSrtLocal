# Multi-Translate Language Adapters Design

- Date: 2026-05-13
- Module: multi-translate video pipeline
- Anchor docs:
  - `2026-04-18-multi-translate-design.md`
  - `2026-04-06-german-translation-module-design.md`
  - `2026-04-06-french-translation-module-design.md`
  - `2026-04-24-ja-translate-shared-shell-design.md`
  - `2026-04-16-en-de-fr-tts-duration-control-design.md`
  - `2026-05-04-tts-speedup-shortcut-design.md`

## Goal

Make the multi-language video translation module use language-specific logic where the project already has it, instead of forcing every target language through the generic prompt adapter.

The first integration scope is intentionally limited to existing independent logic:

- German (`de`)
- French (`fr`)
- Japanese (`ja`)

Other supported languages (`es`, `it`, `pt`, `nl`, `sv`, `fi`, `en`) keep the current generic multi-translate path unless a future spec adds language-specific behavior for them.

## Current State

`MultiTranslateRunner` already resolves per-language TTS model/language code and subtitle SRT parameters through `pipeline.languages.<lang>`.

However, several language-specific implementations are not fully used by multi-translate:

- `pipeline.localization_de` and `pipeline.localization_fr` contain dedicated translation, TTS-script, rewrite prompts, weak starters, and message builders.
- `pipeline.ja_translate` and `appcore.runtime_ja` contain a Japanese character-budget flow, Japanese subtitle chunk splitting, Japanese TTS segment construction, and timed subtitle generation.
- `MultiTranslateRunner` currently uses `_PromptLocalizationAdapter` for all languages and routes TTS duration convergence through the shared word-count loop.

## Design

Add a language adapter layer for `MultiTranslateRunner`.

The runner should resolve an adapter by `target_lang`:

- Default adapter: current behavior.
- German adapter: reuse German-specific localization builders and rules where compatible.
- French adapter: reuse French-specific localization builders and rules where compatible.
- Japanese adapter: use the Japanese character-budget flow instead of the generic word-count TTS loop.

The adapter boundary should be narrow:

- Build initial translation messages or system prompt.
- Build rewrite messages.
- Build or validate TTS script.
- Build TTS segments.
- Optionally override TTS execution.
- Optionally override subtitle generation.

## German And French

German and French should continue to use the shared five-round TTS duration loop, but the loop must receive language-specific message builders and validation constraints.

Behavior:

- `de/fr` translation should use the same prompt content available through `llm_prompt_configs`, preserving admin prompt overrides.
- `de/fr` TTS script and rewrite prompts should use their language-specific builder shape instead of the generic adapter.
- Subtitle generation should keep the current ASR realign path but continue using `pipeline.languages.<lang>` weak starters, max chars per line, max lines, and post-processing.
- German keeps the hotfix behavior from 2026-05-13: stricter subtitle chunk validation and language-specific speedup window.

## Japanese

Japanese should not use the generic word-count TTS duration loop.

Behavior:

- Initial localization uses `pipeline.ja_translate.generate_ja_localized_translation`.
- Rewrite uses `pipeline.ja_translate.rewrite_ja_localized_translation`.
- TTS script uses `pipeline.ja_translate.build_ja_tts_script`.
- TTS segments use `pipeline.ja_translate.build_ja_tts_segments`.
- Subtitle generation uses `pipeline.ja_translate.build_timed_subtitle_chunks` and `pipeline.languages.ja` SRT post-processing.
- Multi-translate artifacts and state fields should stay compatible with existing multi task detail UI: `localized_translation`, `tts_script`, `tts_duration_rounds`, `tts_audio_path`, `corrected_subtitle`, `srt_path`, and variant state.

## Non-Goals

- No new language support.
- No DB schema migration.
- No rewrite of the prompt management UI.
- No changes to languages that only have generic prompt defaults today.
- No service restart or deployment as part of implementation unless explicitly requested later.

## Error Handling

- If a language adapter raises a provider or JSON validation error, the existing pipeline failure path should report the failing step.
- If a language has no adapter, the runner must fall back to the generic adapter.
- Japanese adapter failures should include step context in the error message because its path bypasses the generic duration loop.

## Verification

Add focused tests:

1. Multi-translate resolves `de` and `fr` through language-specific adapters.
2. `de/fr` still preserve admin prompt resolver usage for base translation, TTS script, and rewrite prompt content.
3. `ja` multi-translate uses Japanese character-budget localization and TTS script builders.
4. `ja` multi-translate subtitle generation uses timed Japanese chunks instead of subtitle ASR realign.
5. Existing generic language tests continue to pass for `es/it/pt/nl/sv/fi/en`.

