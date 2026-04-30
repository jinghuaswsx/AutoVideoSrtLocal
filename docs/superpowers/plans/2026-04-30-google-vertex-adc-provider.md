# Google Vertex ADC Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `gemini_vertex_adc` provider that calls Gemini on Vertex AI through server-side Application Default Credentials.

**Architecture:** Add a focused adapter subclass/variant for ADC-only Vertex calls, register new text/image provider config rows, and leave existing AI Studio and Vertex API-key/project behavior untouched. Tests drive registry, credential mapping, error handling, and call routing.

**Tech Stack:** Python, `google-genai`, existing `appcore.llm_client`, `appcore.llm_provider_configs`, MySQL migrations, pytest.

---

### Task 1: Registry And Config Rows

**Files:**
- Modify: `appcore/llm_provider_configs.py`
- Create: `db/migrations/2026_04_30_google_vertex_adc_provider.sql`
- Modify: `tests/test_llm_provider_configs.py`

- [x] **Step 1: Write failing tests**

Add assertions that `gemini_vertex_adc_text` and `gemini_vertex_adc_image` are known providers and that `credential_provider_for_adapter("gemini_vertex_adc")` maps to the text row while image media maps to the image row.

- [x] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_llm_provider_configs.py -q`

Expected: failure mentioning unknown `gemini_vertex_adc`.

- [x] **Step 3: Implement registry and migration**

Add the provider rows to `_KNOWN_PROVIDERS`, add the adapter mapping to `_ADAPTER_CREDENTIAL_MAP`, and create an idempotent migration that inserts both DB rows and seeds `project-b95141b7-f9cb-4017-981` / `global` when the row is empty.

- [x] **Step 4: Verify**

Run: `pytest tests/test_llm_provider_configs.py -q`

Expected: pass.

### Task 2: ADC Adapter

**Files:**
- Modify: `appcore/llm_providers/gemini_vertex_adapter.py`
- Modify: `appcore/llm_providers/__init__.py`
- Modify: `appcore/llm_client.py`
- Modify: `tests/test_llm_providers_gemini_vertex.py`
- Modify: `tests/test_llm_client_invoke.py`

- [x] **Step 1: Write failing tests**

Add tests for ADC credential resolution requiring `extra_config.project`, `_get_client` receiving no API key, registry lookup for `gemini_vertex_adc`, and `network_route_intent == "proxy_required"`.

- [x] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_llm_providers_gemini_vertex.py tests/test_llm_client_invoke.py -q`

Expected: failures for missing adapter/provider handling.

- [x] **Step 3: Implement adapter**

Add a `GeminiVertexADCAdapter` that reuses Vertex generation logic but resolves credentials from `gemini_vertex_adc_*`, requires project, defaults location to `global`, and uses `genai.Client(vertexai=True, project=project, location=location)`.

- [x] **Step 4: Verify**

Run: `pytest tests/test_llm_provider_configs.py tests/test_llm_providers_gemini_vertex.py tests/test_llm_client_invoke.py -q`

Expected: pass, aside from any pre-existing unrelated registry count assertion if the full registry suite is included.

### Task 3: Server Configuration And Smoke Test

**Files:**
- No source files beyond the migration; server commands only.

- [x] **Step 1: Complete ADC auth**

Entered the Google verification code into the root tmux session, then ran:

```bash
/root/google-cloud-sdk/bin/gcloud auth application-default set-quota-project project-b95141b7-f9cb-4017-981
/root/google-cloud-sdk/bin/gcloud services enable aiplatform.googleapis.com
```

- [x] **Step 2: Verify direct API**

Ran root ADC token and Python SDK requests to `gemini-2.5-flash`; both returned `SUCCESS`.

- [x] **Step 3: Apply DB row on test/online as requested**

Use the server MySQL environment only; do not use Windows local MySQL. Insert or update `gemini_vertex_adc_text` and `gemini_vertex_adc_image` with `extra_config={"project":"project-b95141b7-f9cb-4017-981","location":"global"}`.
