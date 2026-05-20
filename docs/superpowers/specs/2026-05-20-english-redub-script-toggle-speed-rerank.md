# English Redub Script Toggle and Voice Speed Rerank

Date: 2026-05-20

## Behavior

- New English redub projects expose a "rewrite script" toggle.
- The toggle is off by default. Off means the original English copy is preserved through translation and TTS duration reconciliation; voice, speed, and audio placement must adapt to the script.
- When the toggle is on, `script_mode=rewrite` is submitted and English redub may rewrite copy during translation and duration reconciliation.
- Omni and other non-English-redub pipelines keep their existing duration text rewrite behavior.

## Voice Match

- Speed-aware voice matching first asks the timbre matcher for the top 20 English voices by similarity.
- Only that 20-voice similarity pool is reranked by speech-rate closeness.
- Voices with a preview speech rate closest to the source speech rate rank first, with timbre similarity used as the tie-breaker.
