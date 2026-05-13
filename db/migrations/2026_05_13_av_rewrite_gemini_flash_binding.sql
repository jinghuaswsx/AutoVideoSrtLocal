-- Move sentence convergence rewrite to OpenRouter Gemini 3 Flash.
-- Docs-anchor: docs/superpowers/specs/2026-05-13-omni-sentence-reconcile-parallel-ui-design.md
-- Only replace old defaults so manual non-default bindings remain untouched.

UPDATE llm_use_case_bindings
SET
  provider_code = 'openrouter',
  model_id = 'google/gemini-3-flash-preview',
  enabled = 1
WHERE use_case_code = 'video_translate.av_rewrite'
  AND provider_code = 'openrouter'
  AND model_id IN ('openai/gpt-5.5', 'anthropic/claude-sonnet-4.6');
