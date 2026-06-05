# Manual Voice Selection After AI Ranking

## Context

Voice AI ranking should remain a recommendation layer by default. After the
ranking model sorts candidates, tasks without an explicit task-level automatic
voice switch must wait for a human to choose the TTS voice instead of
auto-confirming rank 1 and moving to the next pipeline step.

This manual-selection rule does not override
`2026-06-04-auto-voice-selection-toggle-design.md`: Omni tasks whose
`plugin_config.auto_voice_selection` is enabled should auto-confirm AI rank 1
while still obeying the idempotency guards for existing selections and completed
`voice_match` steps.

## Design

- Single-speaker voice selector keeps AI ranking, badges, sorting, and rerank controls. It calls the voice confirmation/launch path automatically only when the task payload explicitly enables `voice_ai_auto_select_enabled`; otherwise it waits for manual selection.
- Dialogue A/B voice matching still extracts per-speaker samples and runs the same ranking logic for each speaker. It stores ranked candidates on each speaker profile, clears any previous speaker voice selection, marks `voice_match_ab` as `waiting`, and requires the `/confirm-voices` route to continue.
- Bulk translation child sync no longer auto-confirms top-ranked child voices. Child tasks that reach voice selection stay in `awaiting_voice` until the user confirms the voice manually.

## Validation

Focused tests cover the frontend selector script, dialogue runtime voice matching, bulk child sync, and media product translation task sync behavior.
