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
- Process at most 10 items per 10-minute round.
- Use Google Vertex ADC with Gemini 3.1 Pro Preview for both analysis types.
- Run serially with a conservative 30-second delay between LLM video calls.
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
- model: `gemini-3.1-pro-preview`
- usage service: `gemini_vertex_adc`

The use case codes remain `meta_hot_posts.video_copyability` and `meta_hot_posts.europe_fit` so billing/reporting can distinguish analysis intent.

## Scheduler

Register one APScheduler job:

- task code: `meta_hot_posts_video_analysis_queue_tick`
- schedule: every 10 minutes
- max instances: 2, so a new tick can enter and take over a stuck previous tick
- batch size: 10
- per-item delay: 30 seconds

The previous separate scheduled jobs for Europe fit and US copyability are removed from APScheduler registration and from the scheduled task registry as controllable jobs. Manual page actions call the unified queue tick with the same limits instead of starting separate loops.

## Verification

Focused tests cover:

- queue ordering: US items first, Europe only after US capacity is exhausted or absent
- max 10 items per tick and 30-second delay between item executions
- takeover reset of both running US and Europe analysis rows
- cooperative stop when a newer queue run supersedes the current run
- both use cases and analyzer overrides use Vertex ADC Gemini 3.1 Pro Preview
- scheduled task registry and APScheduler registration expose only the unified video analysis queue task
