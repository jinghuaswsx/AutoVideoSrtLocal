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
- Process at most 22 items per 10-minute round.
- Use Google Vertex ADC with Gemini 3 Flash for both analysis types.
- Run serially with a 10-second delay between LLM video calls.
- Requeue 429 / rate-limit failures for the next scheduled round once there are remaining attempts.
- Stop the current round early after 2 rate-limit requeues to avoid a quota storm.
- Analyze each downloaded local video at most three times; after the third failed attempt, set the analysis row to `suspended` so operators can inspect it later.
- Use takeover singleton behavior: every new 10-minute round marks any previous running queue run failed, resets both analysis types still marked running, starts a new run, and the old worker stops cooperatively before writing stale results.
- At the start of every 10-minute queue tick, rebuild the analysis queues before selecting work: add every downloaded hot post that still has a product link and a local video path into the US copyability queue and the Europe fit queue when its task row is missing.

## Non-goals

- Do not merge the existing result tables. US Top50 still reads `meta_hot_post_video_copyability_analyses`; Europe Top50 still reads `meta_hot_post_europe_assessments`.
- Do not change the Top50 ranking SQL or the page card rendering in this change.
- Do not connect to Windows local MySQL for verification.

## Queue Model

The queue is implemented at the scheduler/service layer and backed by the two existing status tables.

Each queue item has:

- `task_type`: `us_copyability` or `europe_fit`.
- `row`: the existing row selected by the task-specific store query.

Every scheduled tick begins with queue reconciliation:

1. Ensure downloaded hot posts with product links exist in `meta_hot_post_video_copyability_analyses`.
2. Ensure downloaded hot posts with product links exist in `meta_hot_post_europe_assessments`.
3. Only after both queue tables are reconciled, select the current round of work.

Queue selection fills a round in this order:

1. Select pending US copyability rows up to the remaining round capacity.
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
- batch size: 22
- per-item delay: 10 seconds
- rate-limit circuit breaker: 2 requeued 429 / quota errors stop the current round

## Retry And Suspension

Both task types count attempts when an item is marked `running`.

- Attempts 1-2: any 429 / quota / rate-limit error is written back as `pending` with the error text, so the item is retried by a later queue round instead of immediately hammering the same quota window.
- Attempts 1-2: non-rate-limit failures remain `failed` and are eligible for another later queue round.
- Attempt 3: any failure is written as `suspended`. The pending selectors exclude `suspended`, so the video is no longer retried automatically until an operator decides how to handle it.

Queue summaries include `rate_limited_requeued`, `suspended`, `rate_limit_circuit_break`, and `stop_reason` counters/flags, plus task-type-specific counters, so follow-up monitoring can tune the safety interval based on real 429 rate and throughput.

The previous separate scheduled jobs for Europe fit and US copyability are removed from APScheduler registration and from the scheduled task registry as controllable jobs. Manual page actions call the unified queue tick with the same limits instead of starting separate loops.

## Verification

Focused tests cover:

- queue ordering: US items first, Europe only after US capacity is exhausted or absent
- max 22 items per tick and 10-second delay between item executions
- stop the tick once 2 items in the round hit 429 / quota requeue
- takeover reset of both running US and Europe analysis rows
- cooperative stop when a newer queue run supersedes the current run
- 429 failures are requeued for a later round
- third failed attempts are suspended for both task types
- both use cases and analyzer overrides use Vertex ADC Gemini 3 Flash
- scheduled task registry and APScheduler registration expose only the unified video analysis queue task
