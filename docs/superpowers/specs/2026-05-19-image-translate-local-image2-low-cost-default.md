# Image Translate Local Image 2 Low-Cost Default

Date: 2026-05-19

## Background

Image translation needs a fixed low-cost image generation path. OpenRouter `openai/gpt-5.4-image-2` is a Chat Completions multimodal model with token-based pricing and `modalities`/`image_config` request semantics, so it must not be treated as the fixed per-image low-cost default for product image translation.

The existing `local_image_2` channel already maps image translation to the OpenAI-compatible Image 2 edit API using `gpt-image-2`, `quality=low`, and a 1K output size selected from the source image ratio.

## Decision

- New image translation defaults use `local_image_2`.
- The default image translation model is `gpt-image-2`.
- Product/material image translation child tasks ignore unrelated global image model preferences and continue to use this fixed low-cost path.
- OpenRouter OpenAI Image 2 remains supported for explicit/historical tasks, but its image-translation model switch defaults to disabled.
- A follow-up migration updates persisted system settings from the previous OpenRouter Image 2 default to the local Image 2 low-cost default.
- Video cover generation defaults are out of scope for this change.

## Verification

- Settings tests must assert fallback/default image translation channel is `local_image_2`.
- Media and bulk child-task tests must assert created image translation tasks use `local_image_2` and `gpt-image-2`.
- Migration tests must assert the new migration writes only image-translation defaults and does not rewrite `video_cover_model_defaults`.
