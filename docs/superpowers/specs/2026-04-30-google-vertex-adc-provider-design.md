# Google Vertex ADC Provider Design

## Goal

Add a third Google LLM channel named `gemini_vertex_adc` so the application can call Gemini on Vertex AI through Application Default Credentials stored on the server user that runs the web service.

## Scope

- Keep existing `gemini_aistudio` and `gemini_vertex` behavior unchanged.
- Register a new adapter provider code: `gemini_vertex_adc`.
- Register separate credential rows for text and image flows:
  - `gemini_vertex_adc_text`
  - `gemini_vertex_adc_image`
- Store only non-secret runtime configuration in DB `extra_config`, primarily `project` and `location`.
- Do not read Google credentials from local Windows, local MySQL, or repository files.

## Architecture

`llm_client` continues to resolve a use case to an adapter provider. The new adapter reuses the existing Gemini Vertex request implementation style, but its credential resolution is stricter: it requires `extra_config.project`, uses `location` defaulting to `global`, and never accepts an API key. The Google Gen AI SDK obtains credentials through ADC, which is expected to be present at `/root/.config/gcloud/application_default_credentials.json` on the server because both web services run as root.

## Data Flow

1. Admin selects or binds a use case to `gemini_vertex_adc`.
2. `credential_provider_for_adapter("gemini_vertex_adc", media_kind)` maps to the text or image ADC config row.
3. The adapter creates `genai.Client(vertexai=True, project=project, location=location)`.
4. Text-only calls and media calls both use the Gen AI SDK Vertex path with ADC.

## Error Handling

Missing provider rows or missing `extra_config.project` raise `ProviderConfigError` with the exact provider code and `/settings` guidance. API keys are intentionally ignored for this provider so misconfigured secrets do not mask ADC failures.

## Verification

Unit tests cover provider registration, credential mapping, client creation without API key, missing-project errors, and `llm_client` network route metadata. Server verification uses root ADC with `project-b95141b7-f9cb-4017-981`, `location=global`, and `gemini-2.5-flash`.
