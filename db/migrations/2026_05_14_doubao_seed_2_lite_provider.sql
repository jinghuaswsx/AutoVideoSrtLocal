-- 2026-05-14 Dedicated provider row for Doubao Seed 2.0 Lite.
-- The secret value is written by operations through DB/settings, not by migration.

INSERT IGNORE INTO llm_provider_configs
  (provider_code, display_name, group_code, base_url, model_id)
VALUES
  (
    'doubao_seed_2_lite',
    '豆包 Seed 2.0 Lite 专用模型',
    'text_llm',
    'https://ark.cn-beijing.volces.com/api/v3',
    'doubao-seed-2-0-lite-260215'
  );

UPDATE llm_provider_configs
SET base_url = 'https://ark.cn-beijing.volces.com/api/v3'
WHERE provider_code = 'doubao_seed_2_lite'
  AND (base_url IS NULL OR base_url = '');

UPDATE llm_provider_configs
SET model_id = 'doubao-seed-2-0-lite-260215'
WHERE provider_code = 'doubao_seed_2_lite'
  AND (model_id IS NULL OR model_id = '');
