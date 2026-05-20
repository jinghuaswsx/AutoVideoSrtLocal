# Gemini 3.1 Pro to 3.5 Flash Design

## Context

The project routes LLM calls through `appcore.llm_client`, with provider
adapters for OpenRouter, Google AI Studio, Google Vertex AI, and Google Vertex
ADC. Google and OpenRouter now expose Gemini 3.5 Flash:

- Google AI Studio model ID: `gemini-3.5-flash`
- Google Vertex AI / Agent Platform model ID: `gemini-3.5-flash`
- OpenRouter model ID: `google/gemini-3.5-flash`

This change is anchored by:

- `AGENTS.md` section "Stack", which makes `appcore.llm_client` the unified LLM
  entry point.
- `docs/superpowers/specs/2026-05-01-llm-client-consolidation-design.md`,
  which keeps business code behind use cases and adapters.
- `docs/superpowers/specs/2026-04-30-google-vertex-adc-provider-design.md`,
  which defines the Vertex ADC channel shape.

## Goal

Replace current Gemini 3.1 Pro usage with Gemini 3.5 Flash across active
runtime configuration, UI model pickers, use-case defaults, API billing
pricebook rows, and tests.

## Scope

Replace these active identifiers:

- `gemini-3.1-pro-preview` -> `gemini-3.5-flash`
- `google/gemini-3.1-pro-preview` -> `google/gemini-3.5-flash`
- `Gemini 3.1 Pro` / `Gemini 3.1 Pro Preview` -> `Gemini 3.5 Flash`
- `gemini_31_pro` and `vertex*_gemini_31_pro` UI/admin preference aliases ->
  `gemini_35_flash` and matching Vertex aliases.

Do not change:

- Gemini 3.1 Flash-Lite models.
- Gemini 3 Flash models.
- Gemini image models.
- Historical benchmark result files whose purpose is to record past 3.1 Pro
  evaluation data.

## Runtime Design

The canonical Gemini 3.5 Flash IDs are native `gemini-3.5-flash` for Google
AI Studio / Vertex / Vertex ADC, and `google/gemini-3.5-flash` for OpenRouter.
The existing provider adapters already pass model IDs through; no SDK adapter
shape change is required.

Use-case defaults that currently select Gemini 3.1 Pro move to Gemini 3.5
Flash. Module-level override constants such as video scoring, video review,
CSK analysis, AI video review, material evaluation, and video-cover analysis
move to the same model ID.

Admin-facing model aliases use the new `*_gemini_35_flash` / `gemini_35_flash`
names. A migration updates saved translate preferences and custom LLM bindings
that still point at 3.1 Pro.

## API Billing Design

The API billing module reads locked request costs from `usage_logs` and prices
new requests through `ai_model_prices`. The migration seeds Gemini 3.5 Flash
token prices for all active call channels:

- `gemini_aistudio` / `gemini-3.5-flash`
- `gemini_vertex` / `gemini-3.5-flash`
- `gemini_vertex_adc` / `gemini-3.5-flash`
- `openrouter` / `google/gemini-3.5-flash`

Existing production databases may already contain old Gemini 3.1 Pro rows in
`ai_model_prices`. Those rows are removed after the 3.5 Flash rows are seeded
so the admin API pricing table no longer advertises a retired active model.
Historical `usage_logs.cost_cny` values stay unchanged because billing locks
costs at write time.

## Verification

1. Update focused tests to expect Gemini 3.5 Flash and run them before
   implementation to verify they fail against the current code.
2. Update code and migration data, including `ai_model_prices` entries for the
   API billing pricebook.
3. Run the focused pytest set covering model mapping, use-case defaults,
   settings routes, material/video analysis paths, OpenRouter adapter payloads,
   and video-cover selections.
4. Run a final search to confirm no active code/test path still uses Gemini 3.1
   Pro identifiers, allowing only historical docs and old migration history.

Real network smoke tests are not performed from this worktree because no Google
or OpenRouter credentials are present in the environment and local DB access
would target `127.0.0.1:3306`, which project rules forbid.
