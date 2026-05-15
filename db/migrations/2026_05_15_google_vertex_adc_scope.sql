-- Restrict Google Vertex ADC use-case bindings to the Meta hot-post video analysis queue.
-- Docs-anchor: docs/superpowers/specs/2026-05-15-google-vertex-adc-scope-design.md

UPDATE llm_use_case_bindings
SET
  provider_code = 'gemini_aistudio',
  model_id = CASE
    WHEN model_id LIKE 'google/%' THEN SUBSTRING(model_id, 8)
    ELSE model_id
  END
WHERE provider_code = 'gemini_vertex_adc'
  AND use_case_code NOT IN (
    'meta_hot_posts.europe_fit',
    'meta_hot_posts.video_copyability'
  );

UPDATE system_settings
SET `value` = 'aistudio'
WHERE `key` = 'image_translate.channel'
  AND `value` = 'cloud_adc';
