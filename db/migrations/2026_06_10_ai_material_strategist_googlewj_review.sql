-- Switch 投放素材AI分析 to GoogleWJ Gemini 3.5 Flash.
-- Docs anchor: docs/superpowers/specs/2026-06-09-ai-material-strategist-project-design.md#2026-06-10-投放素材-ai-分析评审契约

ALTER TABLE ai_material_strategist_projects
  MODIFY provider_code VARCHAR(64) NOT NULL DEFAULT 'google_wj',
  MODIFY model_id VARCHAR(128) NOT NULL DEFAULT 'gemini-3.5-flash';

UPDATE llm_use_case_bindings
SET provider_code = 'google_wj',
    model_id = 'gemini-3.5-flash',
    updated_at = CURRENT_TIMESTAMP
WHERE use_case_code IN (
  'medias.ai_material_strategist_rank_products',
  'medias.ai_material_strategist_product_analysis'
);
