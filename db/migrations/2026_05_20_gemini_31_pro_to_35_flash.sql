-- Replace active Gemini 3.1 Pro bindings and admin preferences with Gemini 3.5 Flash.
-- Docs-anchor: docs/superpowers/specs/2026-05-20-gemini-31-pro-to-35-flash-design.md

UPDATE llm_use_case_bindings
SET model_id = CASE
    WHEN model_id = 'google/gemini-3.1-pro-preview' THEN 'google/gemini-3.5-flash'
    WHEN model_id = 'gemini-3.1-pro-preview' THEN 'gemini-3.5-flash'
    ELSE model_id
  END
WHERE model_id IN ('gemini-3.1-pro-preview', 'google/gemini-3.1-pro-preview');

UPDATE api_keys
SET key_value = CASE
    WHEN key_value = 'vertex_gemini_31_pro' THEN 'vertex_gemini_35_flash'
    WHEN key_value = 'vertex_adc_gemini_31_pro' THEN 'vertex_adc_gemini_35_flash'
    WHEN key_value = 'gemini_31_pro' THEN 'gemini_35_flash'
    ELSE key_value
  END
WHERE service = 'translate_pref'
  AND key_value IN ('vertex_gemini_31_pro', 'vertex_adc_gemini_31_pro', 'gemini_31_pro');

INSERT INTO ai_model_prices (
  provider, model, units_type, unit_input_cny, unit_output_cny, note
) VALUES
  ('gemini_aistudio', 'gemini-3.5-flash', 'tokens', 0.00001020, 0.00006120, 'Gemini 3.5 Flash Standard: 1.5/9 USD/M ×6.8'),
  ('gemini_vertex', 'gemini-3.5-flash', 'tokens', 0.00001020, 0.00006120, 'Gemini 3.5 Flash Standard: 1.5/9 USD/M ×6.8'),
  ('gemini_vertex_adc', 'gemini-3.5-flash', 'tokens', 0.00001020, 0.00006120, 'Gemini 3.5 Flash Standard: 1.5/9 USD/M ×6.8'),
  ('openrouter', 'google/gemini-3.5-flash', 'tokens', 0.00001020, 0.00006120, 'OpenRouter Gemini 3.5 Flash fallback price: 1.5/9 USD/M ×6.8')
ON DUPLICATE KEY UPDATE
  unit_input_cny = VALUES(unit_input_cny),
  unit_output_cny = VALUES(unit_output_cny),
  note = VALUES(note),
  updated_at = CURRENT_TIMESTAMP;

DELETE FROM ai_model_prices
WHERE (provider = 'gemini_aistudio' AND model = 'gemini-3.1-pro-preview')
   OR (provider = 'gemini_vertex' AND model = 'gemini-3.1-pro-preview')
   OR (provider = 'gemini_vertex_adc' AND model = 'gemini-3.1-pro-preview')
   OR (provider = 'openrouter' AND model = 'google/gemini-3.1-pro-preview')
   OR (provider = 'openrouter' AND model = 'gemini-3.1-pro-preview');
