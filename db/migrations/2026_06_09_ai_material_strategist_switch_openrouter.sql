-- AI material strategist uses OpenRouter while GoogleWJ quota/rate limits are tuned.
-- Docs anchor: docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md

ALTER TABLE ai_material_strategist_projects
  MODIFY provider_code VARCHAR(64) NOT NULL DEFAULT 'openrouter',
  MODIFY model_id VARCHAR(128) NOT NULL DEFAULT 'google/gemini-3.5-flash';
