-- 2026-04-30 Google Vertex AI ADC provider rows.
--
-- This adds a separate Google channel for server-side Application Default
-- Credentials. It intentionally stores no API key; credentials live in the
-- service user's ADC file, for example /root/.config/gcloud/application_default_credentials.json.

INSERT IGNORE INTO llm_provider_configs (provider_code, display_name, group_code) VALUES
  ('gemini_vertex_adc_text',  'Google Vertex AI · ADC（文本）', 'text_llm'),
  ('gemini_vertex_adc_image', 'Google Vertex AI · ADC（图片）', 'image');

UPDATE llm_provider_configs
SET
  model_id = COALESCE(NULLIF(model_id, ''), 'gemini-2.5-flash'),
  extra_config = COALESCE(
    extra_config,
    JSON_OBJECT(
      'project', 'project-b95141b7-f9cb-4017-981',
      'location', 'global'
    )
  )
WHERE provider_code IN ('gemini_vertex_adc_text', 'gemini_vertex_adc_image');

INSERT INTO llm_use_case_bindings (
  use_case_code,
  provider_code,
  model_id,
  extra_config,
  enabled,
  updated_by
) VALUES (
  'material_evaluation.evaluate',
  'gemini_vertex_adc',
  'gemini-3.1-pro-preview',
  NULL,
  1,
  NULL
) ON DUPLICATE KEY UPDATE
  provider_code = VALUES(provider_code),
  model_id = VALUES(model_id),
  enabled = VALUES(enabled);
