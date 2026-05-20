# Google Vertex ADC Scope Design

## Context

Google Vertex ADC is now reserved for the Meta hot-post unified video analysis queue that runs:

- `meta_hot_posts.video_copyability`
- `meta_hot_posts.europe_fit`

Both use Gemini 3.5 Flash through `gemini_vertex_adc` because that queue has a controlled cadence and takeover singleton.

## Rule

Other business modules must not default to or persist new `gemini_vertex_adc` use-case bindings. If an older DB binding still points a non-allowlisted use case at `gemini_vertex_adc`, runtime resolution normalizes it to `gemini_aistudio` and strips an OpenRouter-style `google/` model prefix when needed.

Image translation and video-cover user-facing model options must not expose the ADC image channel. Existing low-level ADC provider support remains in the provider layer so the allowlisted Meta video analysis channel can keep working and old logs/config rows remain readable.

## Migration

A DB migration updates live `llm_use_case_bindings` rows from `gemini_vertex_adc` to `gemini_aistudio` unless the use case is one of the two allowlisted Meta video analysis cases.
