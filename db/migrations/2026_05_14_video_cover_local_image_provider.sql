-- Register local OpenAI-compatible image endpoint for 文案封面生成.
-- API key stays in llm_provider_configs and is not seeded in code.

INSERT IGNORE INTO llm_provider_configs
  (provider_code, display_name, group_code, base_url, model_id)
VALUES
  ('video_cover_local_image', '文案封面生成 · 本地图片接口', 'image', 'http://172.16.254.106:82/v1', 'gpt-image-2');

UPDATE llm_provider_configs
SET base_url = COALESCE(NULLIF(base_url, ''), 'http://172.16.254.106:82/v1'),
    model_id = COALESCE(NULLIF(model_id, ''), 'gpt-image-2')
WHERE provider_code = 'video_cover_local_image';
