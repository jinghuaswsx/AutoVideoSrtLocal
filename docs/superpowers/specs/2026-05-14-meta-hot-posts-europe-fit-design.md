# Meta Hot Posts Europe Fit Design

Last updated: 2026-05-14

## Background

Meta hot posts already have four local workflows: wedev sync, product extraction/category analysis, message translation, and local video download. Operators now need an additional evaluation pass that decides whether a hot-post video and its product link can be used directly for Meta advertising in European markets such as Germany, France, Italy, and Spain.

This design follows the project rules: no Windows local MySQL verification, all timers are registered in `appcore/scheduled_tasks.py`, LLM calls go through `appcore.llm_client`, and code changes are made in an isolated worktree.

## Goals

- Evaluate hot-post materials that have both a product URL and a downloaded local video.
- Compress the video sent to the LLM to 480p, 15 fps, and 600k video bitrate before upload through OpenRouter.
- Use OpenRouter Gemini 3 Flash for the multimodal judgment.
- Process 30 materials per scheduled run.
- Run every 10 minutes.
- Use takeover singleton behavior: a later invocation supersedes any previous running Europe-fit run and owns the queue.
- Persist every result and expose the best 50 materials for European operation in a sub tab under the Meta hot posts page.

## Non-goals

- Do not create Meta campaigns or publish ads automatically.
- Do not translate, edit, or re-render the source videos in this change.
- Do not evaluate rows without a local video file; those remain blocked on the existing video-localization workflow.
- Do not connect to Windows local MySQL for verification.

## Data Model

Add `meta_hot_post_europe_assessments`, one row per `meta_hot_posts.id`.

Key fields:

- `post_id`: local hot-post ID, unique.
- `status`: `pending`, `running`, `done`, or `failed`.
- `attempts`, `last_error`, `assessed_at`.
- `suitability_score`: 0 to 100.
- `recommendation`: `direct_reuse`, `adapt_before_use`, or `not_recommended`.
- `direct_reuse`: boolean shortcut for high-confidence direct movement.
- JSON fields for `best_countries`, `country_scores`, `strengths`, `risks`, `required_changes`, raw LLM response, and video optimization metadata.
- `llm_provider` and `llm_model` for billing/audit traceability.

The queue query selects rows where:

- `product_url` is present.
- `local_video_status = 'downloaded'`.
- `local_video_path` resolves under `OUTPUT_DIR`.
- The assessment row is missing, pending, or failed, and `attempts < 3`.

## LLM Flow

Register use case `meta_hot_posts.europe_fit`:

- provider: `openrouter`
- model: `google/gemini-3-flash-preview`
- usage service: `openrouter`

For each material:

1. Resolve billing user with the existing admin-user resolver.
2. Resolve the protected local video path with `video_localization.resolve_local_video_path()`.
3. Prepare an LLM-only video using `appcore.llm_media_optimizer.REVIEW_480P_AUDIO`, which is 480p, 15 fps, 600k video bitrate, and preserves low-bitrate audio for language and compliance clues.
4. Build a prompt containing product URL, product title, price, category, Meta metrics, and the target European markets.
5. Call `llm_client.invoke_generate()` with the compressed video as media and a JSON schema.
6. Normalize model output into the stored fields.
7. Clean up the temporary compressed file after the call.

If compression fails, the optimizer records the error and falls back to the original local video path. The assessment still records the optimization metadata so operators can audit large uploads.

## Singleton Takeover

`meta_hot_posts_europe_fit_tick` uses a cooperative takeover singleton.

Every invocation checks for the latest running Europe-fit run. If one exists, the new invocation marks the old run failed, resets rows still in `status='running'` to `pending`, and starts its own run. The worker receives its current `run_id`; before committing each LLM result it checks that this run is still the latest running run. If a later run has taken over, the old worker stops without writing stale output.

APScheduler registers the interval job with enough `max_instances` to allow a newer invocation to enter and take over. The persisted run state still has only one active owner after takeover.

## UI

The existing `/xuanpin/meta-hot-posts` page gets an inner sub tab:

- `素材库`: the current hot-post card grid.
- `欧洲Top50`: the top 50 completed Europe-fit assessments, sorted by suitability score, then interaction change, then assessment time.

The Top50 card uses the existing media rendering order and adds Europe assessment badges: score, recommendation, best countries, strengths, risks, and required changes. Tool buttons include a manual “欧洲评估” trigger that uses the same scheduler entry point with a 30-item batch.

## Verification

Focused tests cover:

- DB migration contents.
- LLM use case registration.
- Queue selection and status transitions.
- Prompt/result normalization and video optimizer usage.
- Scheduler registration, takeover behavior, and scheduled batch defaults.
- Route delegation for manual trigger and Top50 API.
- Template sub-tab rendering and API wiring.

Do not run any check that connects to Windows local MySQL `127.0.0.1:3306`.
