-- Update Gemini 3.1 Flash-Lite from preview to GA model.
-- This updates both direct model IDs and OpenRouter-prefixed model IDs.

-- Update llm_use_case_bindings: gemini-3.1-flash-lite-preview -> gemini-3.1-flash-lite
UPDATE llm_use_case_bindings
SET model_id = 'gemini-3.1-flash-lite'
WHERE model_id = 'gemini-3.1-flash-lite-preview';

-- Update llm_use_case_bindings: google/gemini-3.1-flash-lite-preview -> google/gemini-3.1-flash-lite
UPDATE llm_use_case_bindings
SET model_id = 'google/gemini-3.1-flash-lite'
WHERE model_id = 'google/gemini-3.1-flash-lite-preview';

-- Update llm_provider_configs where model_id might reference the preview version
UPDATE llm_provider_configs
SET model_id = 'gemini-3.1-flash-lite'
WHERE model_id = 'gemini-3.1-flash-lite-preview';

UPDATE llm_provider_configs
SET model_id = 'google/gemini-3.1-flash-lite'
WHERE model_id = 'google/gemini-3.1-flash-lite-preview';
