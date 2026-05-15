# Meta Hot Posts Video Copyability Analysis

Date: 2026-05-14

Superseded scheduling/model note: as of 2026-05-15, scheduled and manual US copyability analysis is run by the unified queue in `2026-05-15-meta-hot-posts-unified-video-analysis-queue-design.md`. The result table and US Top 50 ranking from this document remain active, but the runner uses task type `us_copyability`, Google Vertex ADC, `gemini-3.1-pro-preview`, a 30-second queue interval, and a shared 10-item round limit.

## Goal

Analyze localized Meta hot post videos and persist whether each material is worth copying for US Meta ad placement. The job uses the OpenRouter channel, the Gemini 3 Flash model, the associated product URL, and a compressed local video. Reviewers can open the US Top 50 shortlist from the Meta Hot Posts page.

## Rate Limit Guardrail

This task remains serial and bounded for normal scheduled runs: each 10-minute interval can analyze at most 20 videos, and it waits 20 seconds between two OpenRouter Gemini video analysis requests.

Guardrails:

- 20 requests maximum per interval run.
- 20 seconds minimum between analysis requests within one run.
- 1 compressed video per request.
- No parallel Gemini calls from this task.
- Normal interval runs skip while another run is active, so a longer 20-item batch will not overlap with the next tick.

## Video Input

Each analyzed video is transcoded before Gemini input:

- height 480p
- 15 fps
- video bitrate 600k
- stored under `output/meta_hot_posts/analysis_videos`

The original downloaded video remains unchanged under `output/meta_hot_posts/videos`.

## Persistence

Results are stored in `meta_hot_post_video_copyability_analyses`, keyed one-to-one by `meta_hot_posts.id`.

The table stores queue status, attempts, product URL, original local video path, compressed video path, provider/model, score fields, recommendation, summary, full JSON response, and error state.

Only downloaded local videos with a non-empty product URL are queued. Completed rows are not re-run unless reset manually.

## US Top 50

The "美国Top50" subtab shows the best completed rows ordered by overall score, copyability score, Meta US ad fit score, and newest analysis time.
