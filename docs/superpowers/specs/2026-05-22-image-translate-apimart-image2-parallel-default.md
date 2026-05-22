# Image Translate APIMART Image 2 Parallel Default

Date: 2026-05-22

## Background

Image translation creation should now prefer the APIMART Image 2 path for new work. The existing channel-rerun design already allows parallel mode for APIMART, while non-OpenRouter/non-APIMART channels stay sequential.

## Decision

- New image translation fallback defaults use the `apimart` channel.
- The APIMART default image translation model is `gpt-image-2`.
- Material/product detail image translation and bulk translation child image tasks use the same APIMART Image 2 default.
- New creation defaults use parallel mode when the default channel is APIMART.
- Runtime fallback for historical tasks that do not store `concurrency_mode` remains `sequential`.
- Existing non-APIMART channel coercion remains unchanged: only OpenRouter and APIMART may run in parallel.

## Verification

- Settings tests assert default/fallback image translation channel is `apimart`.
- Media and bulk child-task tests assert created image translation tasks use `apimart`, `gpt-image-2`, and `parallel`.
- Template/static tests assert the creation UI defaults to parallel.
- Migration tests assert persisted system settings switch image translation defaults to APIMART without touching video cover defaults.
