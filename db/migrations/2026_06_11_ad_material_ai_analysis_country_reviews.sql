-- Add country-level review columns to product results table
ALTER TABLE ad_material_ai_analysis_product_results
  ADD COLUMN country_reviews_json JSON NULL AFTER ai_result_json,
  ADD COLUMN market_expansion_json JSON NULL AFTER country_reviews_json;

-- Register new LLM use case for country-level review
INSERT INTO llm_use_case_bindings (
  use_case_code, provider_code, model_id, extra_config, enabled, updated_by, updated_at
) VALUES
  ('medias.ad_material_ai_analysis_country_review', 'google_wj', 'gemini-3.5-flash', NULL, 1, NULL, CURRENT_TIMESTAMP)
ON DUPLICATE KEY UPDATE
  provider_code = VALUES(provider_code),
  model_id = VALUES(model_id),
  enabled = VALUES(enabled),
  updated_at = VALUES(updated_at);
