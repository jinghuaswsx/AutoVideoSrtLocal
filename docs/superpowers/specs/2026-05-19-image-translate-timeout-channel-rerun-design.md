# Image Translate Timeout And Channel Rerun Design

Date: 2026-05-19

## Context

Image translation detail tasks can hang when an upstream image generation request does not return. The detail page also has a recovery gap: users can retry unfinished images, but they cannot switch the processing mode, image channel, or model before rerunning the failed rows.

## Goals

- Cap each image generation request issued by an image translation item at 120 seconds.
- Treat that timeout as a terminal per-item failure for the current run, not as another retryable transient error.
- Add a detail-page channel-rerun action.
- The action must guide the user through image channel first, model second, and sequential/parallel mode last.
- Only OpenRouter and APIMART may use parallel mode. Other channels must run sequentially, with both UI and backend coercion enforcing that rule.
- It must keep successful rows untouched and reset only rows without a successful result.

## Backend Design

`appcore.image_translate_runtime` owns the image-translation item loop. It will pass `timeout_seconds=120` to `appcore.gemini_image.generate_image()` for every image generation call. Saved APIMART task polling uses the same timeout when an item resumes a provider task.

`appcore.gemini_image` will accept an optional `timeout_seconds` parameter and route it to provider clients:

- Google GenAI calls use `GenerateContentConfig(http_options=HttpOptions(timeout=120000))`.
- OpenRouter clients are created with a matching timeout.
- Seedream HTTP calls use the matching requests timeout.
- APIMART submit and poll use the same overall budget; poll timeout raises a non-retryable `GeminiImageTimeout`.

`web.routes.image_translate` adds a new POST endpoint for channel reruns. It validates `channel`, `model_id`, and `concurrency_mode`, coerces parallel mode back to sequential unless the channel is OpenRouter or APIMART, then resets every item whose status is not `done` or whose `dst_tos_key` is empty. Successful items remain unchanged. The task's channel, model, and mode are updated before restarting the runner.

## Frontend Design

The list-page new-task form applies the same channel rule before submission: the parallel pill is enabled only for OpenRouter or APIMART. Switching to any other channel disables the parallel pill and forces the hidden mode value back to sequential.

The detail task info card gets a channel-rerun button in the current-channel area. Clicking it opens a small modal with existing pill-style controls in this order:

- image channel;
- model list loaded from `/api/image-translate/models?channel=...`;
- processing mode: sequential or parallel.

When the selected channel is neither OpenRouter nor APIMART, the parallel pill is disabled and the hidden mode value is forced back to sequential.

Submitting the modal calls the new rerun endpoint. The existing polling/socket refresh flow then updates progress.

## Verification

Focused tests cover:

- runtime passes the 120-second timeout to image generation;
- image generation timeout marks an item failed after one attempt;
- APIMART resumed-task timeout fails without submitting a replacement request;
- channel rerun resets only unfinished rows, updates task channel/model/mode, and starts the runner;
- channel rerun rejects invalid model/channel/mode and running tasks, while coercing non-OpenRouter/non-APIMART parallel mode back to sequential;
- detail templates contain the new button, modal, model loading, and endpoint call.
- list-page new-task submission accepts OpenRouter/APIMART parallel mode and coerces other channels to sequential.
