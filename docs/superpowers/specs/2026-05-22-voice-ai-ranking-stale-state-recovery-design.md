# Voice AI Ranking Stale-State Recovery

Date: 2026-05-22

## Background

Production task `cef2d2be22e85053ad1920b8191f2894` entered the shared voice
selection step with `voice_ai_rank_status=running`, no persisted
`voice_ai_rank_usage_log_id`, and no rankings for the current candidate
signature. Read-only billing evidence showed only one `voice_selection.assess`
usage log for an older candidate signature, so the user-facing loop was caused
by stale polling state rather than repeated paid LLM calls.

The failure mode is a service interruption or worker shutdown after the
sidecar ranking has marked the task as running but before it writes a terminal
state. A page refresh then keeps seeing `running` and keeps polling the voice
library without giving the user a clear recovery action.

## Required Behavior

- Voice AI ranking is at-most-once per candidate signature unless the user
  explicitly clicks a rerun action. GET endpoints, page refreshes, automatic
  polling, and stale-state normalization must never call the LLM.
- When a ranking job is queued, state must persist a started timestamp together
  with the candidate signature, model, provider, candidates, empty rankings,
  and `voice_ai_rank_status=running`.
- A `running` or `queued` ranking with no started timestamp is legacy stale
  state and must be normalized to `interrupted` when the voice-library payload
  is built.
- A `running` or `queued` ranking whose started timestamp is older than the
  configured stale window must be normalized to `interrupted`.
- The interrupted state keeps the current candidate list and candidate
  signature, clears rankings and usage log id, and writes a debug payload that
  explains the interruption without fabricating an LLM result.
- A fresh `running` state inside the stale window must remain pollable.

## Frontend Contract

- `interrupted` is terminal for automatic polling. The selector must stop
  scheduling voice-library polls for it.
- The status pill must clearly say the AI voice selection was interrupted.
- The blocked-selection text must explain the available recovery choices:
  click `重新AI排名`, click `强制音色语速匹配排序`, or click
  `从音色选择重新跑`.
- `重新AI排名` remains cache-first. It may call the LLM only through the
  existing explicit POST rerun endpoint when no matching cache exists.
- `从音色选择重新跑` calls the existing resume endpoint with
  `start_step=voice_match`; it is an explicit user operation for recovering
  this step after service interruption.
- Auto-confirm must not run while the ranking status is `interrupted`.

## Out of Scope

- Do not change voice candidate generation, TTS synthesis, subtitle rendering,
  or the confirm-voice request contract.
- Do not add a scheduler or background retry loop for AI ranking. Recovery is
  user-triggered to keep paid LLM calls controlled.
