# Manual Voice Selection After AI Ranking

## Context

Voice AI ranking should remain a recommendation layer only. After the ranking model sorts candidates, the task must wait for a human to choose the TTS voice instead of auto-confirming rank 1 and moving to the next pipeline step.

## Design

- Single-speaker voice selector keeps AI ranking, badges, sorting, and rerank controls, but never calls the voice confirmation/launch path automatically.
- Dialogue A/B voice matching still extracts per-speaker samples and runs the same ranking logic for each speaker. It stores ranked candidates on each speaker profile, clears any previous speaker voice selection, marks `voice_match_ab` as `waiting`, and requires the `/confirm-voices` route to continue.
- Bulk translation child sync no longer auto-confirms top-ranked child voices. Child tasks that reach voice selection stay in `awaiting_voice` until the user confirms the voice manually.

## Validation

Focused tests cover the frontend selector script, dialogue runtime voice matching, bulk child sync, and media product translation task sync behavior.
