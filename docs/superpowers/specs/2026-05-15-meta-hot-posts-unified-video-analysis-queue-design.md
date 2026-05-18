# Meta Hot Posts Unified Video Analysis Queue

Date: 2026-05-15

## Background

Meta hot posts currently has two video analysis runners:

- US copyability analysis, documented in `2026-05-14-meta-hot-posts-video-copyability-analysis-design.md`.
- Europe direct-move fit assessment, documented in `2026-05-14-meta-hot-posts-europe-fit-design.md`.

Both analyze downloaded local videos with product links. They should now share one scheduled queue so operators do not run two independent video analysis loops against the same model channel.

## Goals

- Use one scheduled queue task for both analysis modes.
- Keep task type explicit: `us_copyability` for "美国直接抄分析" and `europe_fit` for "欧洲搬运分析".
- Process US copyability items before Europe fit items. Europe starts only when no US items are available in the same round.
- Process items continuously within a 560-second window per 10-minute round.
- Use Google Vertex ADC with Gemini 3 Flash for both analysis types.
- Run serially with no delay between LLM video calls and a 40-second hard per-item timeout.
- The 40-second timeout means the queue stops waiting for the current item's worker and moves on; it must not block on the underlying provider call after timeout.
- Stop the current round immediately on any 429 / quota / rate-limit response to avoid a quota storm.
- Analyze each downloaded local video at most three times; after the third failed attempt, set the analysis row to `suspended` so operators can inspect it later.
- Use takeover singleton behavior: every new 10-minute round marks any previous running queue run failed, resets both analysis types still marked running, starts a new run, and the old worker stops cooperatively before writing stale results.

## Non-goals

- Do not merge the existing result tables. US Top50 still reads `meta_hot_post_video_copyability_analyses`; Europe Top50 still reads `meta_hot_post_europe_assessments`.
- Do not change the Top50 ranking SQL or the page card rendering in this change.
- Do not connect to Windows local MySQL for verification.

## Queue Model

The queue is implemented at the scheduler/service layer and backed by the two existing status tables.

Each queue item has:

- `task_type`: `us_copyability` or `europe_fit`.
- `row`: the existing row selected by the task-specific store query.

Queue selection fills a round in this order:

1. Ensure and select pending US copyability rows up to the remaining round capacity.
2. If capacity remains and no more US rows were selected, select pending Europe fit rows up to the remaining capacity.
3. If neither type returns rows, the run succeeds with zero scanned items.

This preserves existing persistence while making scheduling and model throttling shared.

## LLM Channel

Both analyzers use:

- provider: `gemini_vertex_adc`
- model: `gemini-3-flash-preview`
- usage service: `gemini_vertex_adc`

The use case codes remain `meta_hot_posts.video_copyability` and `meta_hot_posts.europe_fit` so billing/reporting can distinguish analysis intent.

## Scheduler

Register one APScheduler job:

- task code: `meta_hot_posts_video_analysis_queue_tick`
- schedule: every 10 minutes
- max instances: 2, so a new tick can enter and take over a stuck previous tick
- batch model: time-driven, one item at a time within a 560-second window
- per-item delay: no delay
- per-item timeout: 40 seconds
- rate-limit circuit breaker: the first 429 / quota error stops the current round

The per-item timeout applies to the whole item worker, including video preparation and the LLM request. The queue must not call the row-specific finish method for the timed-out item, because the worker may still finish later and must not overwrite the persisted analysis row. A timed-out item therefore keeps its pre-run status and attempt count.

## Retry And Suspension

Both task types keep row status unchanged until the item succeeds or fails before the timeout.

- Before invoking an analyzer, the queue records the row's original status and attempt count.
- If the item succeeds before timeout, the queue writes the normal `done` result.
- If the item fails before timeout with a non-rate-limit error, the queue may write `failed` / `suspended` using the existing attempt policy.
- If the item times out, the queue restores the row to its original status and attempt count and records timeout only in the scheduled-task summary.
- If the item fails with 429 / quota / rate-limit, the queue restores the row to its original status and attempt count, records the rate-limit stop in the scheduled-task summary, and ends the current round immediately.

Queue summaries include `timed_out`, `rate_limited`, `suspended`, `rate_limit_circuit_break`, and `stop_reason` counters/flags, plus task-type-specific counters, so follow-up monitoring can tune the safety interval based on real timeout and 429 rates.

The previous separate scheduled jobs for Europe fit and US copyability are removed from APScheduler registration and from the scheduled task registry as controllable jobs. Manual page actions call the unified queue tick with the same limits instead of starting separate loops.

## Verification

Focused tests cover:

- queue ordering: US items first, Europe only after US capacity is exhausted or absent
- time-driven loop runs items one-at-a-time within a 560-second window
- stop the tick immediately when any item hits 429 / quota / rate-limit
- takeover reset of both running US and Europe analysis rows
- cooperative stop when a newer queue run supersedes the current run
- timed-out and rate-limited items restore their pre-run status and attempt count instead of writing a row finish
- third failed attempts are suspended for both task types
- both use cases and analyzer overrides use Vertex ADC Gemini 3 Flash
- scheduled task registry and APScheduler registration expose only the unified video analysis queue task
