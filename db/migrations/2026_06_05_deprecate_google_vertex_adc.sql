-- 2026-06-05 Deprecate Google Vertex AI ADC provider and channel configurations.
--
-- This script migrates all existing use cases configured to use Vertex ADC ('gemini_vertex_adc')
-- to Google Vertex AI API client ('gemini_vertex'), and redirects the default image translation
-- channel from 'cloud_adc' to 'cloud'. It also cleans up the ADC-specific provider configurations.

-- 1. Migrate use case bindings
UPDATE llm_use_case_bindings
SET provider_code = 'gemini_vertex'
WHERE provider_code = 'gemini_vertex_adc';

-- 2. Migrate image translation channel settings
UPDATE system_settings
SET value = 'cloud'
WHERE `key` = 'image_translate.channel' AND value = 'cloud_adc';

DELETE FROM system_settings
WHERE `key` = 'image_translate.default_model.cloud_adc';

-- 3. Delete ADC-specific provider configs
DELETE FROM llm_provider_configs
WHERE provider_code IN ('gemini_vertex_adc_text', 'gemini_vertex_adc_image');
