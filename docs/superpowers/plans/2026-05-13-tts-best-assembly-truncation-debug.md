# TTS Best Assembly Truncation Debug Plan

- Date: 2026-05-13
- Docs-anchor: `docs/superpowers/specs/2026-05-13-tts-segment-candidate-assembly-design.md`

## Scope

Implement the follow-up behavior requested after the ElevenLabs speed variant
work: when three native speed attempts cannot hit `[video - 1s, video]` but can
produce a shorter closest-over assembly inside `[video - 1s, video + 2s]`, use
that best assembly as the source, truncate it to the video duration immediately,
and pass the truncated file and fitted segment metadata to composition.

## Tasks

1. Add regression coverage for closest-over segment assembly truncation in the
   stage-1 postprocess path and the best-pick overrun path.
2. Preserve existing exact-hit assembly behavior.
3. Record diagnostics for candidate attempts, selected segments, untrimmed
   assembly duration/path, truncation output duration/path, removed count, and
   removed duration.
4. Update task detail rendering so debugging shows speed candidates, selected
   segment sources, and the trim from best assembly to final composition audio.
5. Verify targeted backend and template tests, then publish through the local
   server worktree because this host is the deployment server.
