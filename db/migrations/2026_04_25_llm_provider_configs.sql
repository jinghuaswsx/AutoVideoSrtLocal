-- 2026-04-25 LLM/API 供应商配置数据库化
--
-- 目标：
--   1) 新增 llm_provider_configs 表，作为所有模型/API 供应商 api_key、base_url、
--      model_id、extra_config 的唯一来源。
--   2) 不再从 .env 或 api_keys 表读取供应商凭据。
--   3) 每个功能入口一条独立 provider_code 行；即使底层真实 Key 一致也要分开存储，
--      方便后续某个功能单独换 key 不影响其他功能。
--
-- 现有 llm_use_case_bindings 表不受影响：仍然负责"业务 use_case → provider_code/model"
-- 路由；本表只负责"给某个 provider_code 提供凭据与默认连接信息"。

CREATE TABLE IF NOT EXISTS llm_provider_configs (
  provider_code VARCHAR(64)  NOT NULL,
  display_name  VARCHAR(128) NOT NULL,
  group_code    VARCHAR(32)  NOT NULL DEFAULT 'llm',
  api_key       TEXT         NULL,
  base_url      VARCHAR(512) NULL,
  model_id      VARCHAR(160) NULL,
  extra_config  JSON         NULL,
  enabled       TINYINT(1)   NOT NULL DEFAULT 1,
  updated_by    BIGINT       NULL,
  created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (provider_code),
  KEY idx_llm_provider_group_code (group_code),
  KEY idx_llm_provider_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ============================================================
-- 1) 种子 14 个固定 provider_code（幂等：已存在行不会被覆盖）
-- ============================================================
INSERT IGNORE INTO llm_provider_configs (provider_code, display_name, group_code) VALUES
  ('openrouter_text',        'OpenRouter 文本 / 本土化 LLM',      'text_llm'),
  ('openrouter_image',       'OpenRouter 图片模型',                'image'),
  ('gemini_aistudio_text',   'Google Gemini · AI Studio（文本）',  'text_llm'),
  ('gemini_aistudio_image',  'Google Gemini · AI Studio（图片）',  'image'),
  ('gemini_cloud_text',      'Google Cloud / Vertex AI（文本）',   'text_llm'),
  ('gemini_cloud_image',     'Google Cloud / Vertex AI（图片）',   'image'),
  ('doubao_llm',             '豆包 ARK 文本模型',                  'text_llm'),
  ('doubao_seedream',        '豆包 Seedream 图片生成',             'image'),
  ('doubao_asr',             '火山 ASR 语音识别',                  'asr'),
  ('seedance_video',         'Seedance 视频生成',                  'video'),
  ('apimart_image',          'APIMART / GPT Image 2',              'image'),
  ('elevenlabs_tts',         'ElevenLabs 配音',                    'tts'),
  ('subtitle_removal',       '字幕移除服务',                        'aux'),
  ('openapi_materials',      '素材 OpenAPI',                        'aux');

-- ============================================================
-- 2) 为新种子行补默认 base_url（只填空值，不覆盖管理员已填）
-- ============================================================
UPDATE llm_provider_configs
SET base_url = 'https://openrouter.ai/api/v1'
WHERE provider_code IN ('openrouter_text', 'openrouter_image')
  AND (base_url IS NULL OR base_url = '');

UPDATE llm_provider_configs
SET base_url = 'https://ark.cn-beijing.volces.com/api/v3'
WHERE provider_code IN ('doubao_llm', 'doubao_seedream')
  AND (base_url IS NULL OR base_url = '');

UPDATE llm_provider_configs
SET base_url = 'https://api.elevenlabs.io/v1'
WHERE provider_code = 'elevenlabs_tts'
  AND (base_url IS NULL OR base_url = '');

UPDATE llm_provider_configs
SET base_url = 'https://api.apimart.ai'
WHERE provider_code IN ('apimart_image', 'seedance_video')
  AND (base_url IS NULL OR base_url = '');

UPDATE llm_provider_configs
SET base_url = 'https://goodline.simplemokey.com/api/openAi'
WHERE provider_code = 'subtitle_removal'
  AND (base_url IS NULL OR base_url = '');

-- Seedance 默认走火山 ARK：复用 doubao_llm 的 base_url 做默认值（注意：key 仍独立）
UPDATE llm_provider_configs
SET base_url = 'https://ark.cn-beijing.volces.com/api/v3'
WHERE provider_code = 'seedance_video'
  AND (base_url IS NULL OR base_url = 'https://api.apimart.ai');

-- ============================================================
-- 3) 从 admin 的 api_keys 行一次性迁移已有凭据（idempotent）
--    只在 llm_provider_configs 该字段为空时填入；admin 已经手动填过的值不会被覆盖
-- ============================================================

-- openrouter → openrouter_text + openrouter_image（同一 key，独立行）
UPDATE llm_provider_configs lpc
INNER JOIN api_keys ak       ON ak.service = 'openrouter'
INNER JOIN users    u        ON u.id = ak.user_id AND u.username = 'admin' AND u.is_active = 1
SET
  lpc.api_key  = COALESCE(NULLIF(lpc.api_key, ''),  ak.key_value),
  lpc.base_url = COALESCE(NULLIF(lpc.base_url, ''), NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ak.extra_config, '$.base_url')), ''), 'null')),
  lpc.model_id = COALESCE(NULLIF(lpc.model_id, ''), NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ak.extra_config, '$.model_id')), ''), 'null'))
WHERE lpc.provider_code IN ('openrouter_text', 'openrouter_image');

-- gemini (AI Studio) → gemini_aistudio_text + gemini_aistudio_image
UPDATE llm_provider_configs lpc
INNER JOIN api_keys ak       ON ak.service = 'gemini'
INNER JOIN users    u        ON u.id = ak.user_id AND u.username = 'admin' AND u.is_active = 1
SET
  lpc.api_key  = COALESCE(NULLIF(lpc.api_key, ''),  ak.key_value),
  lpc.model_id = COALESCE(NULLIF(lpc.model_id, ''), NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ak.extra_config, '$.model_id')), ''), 'null'))
WHERE lpc.provider_code IN ('gemini_aistudio_text', 'gemini_aistudio_image');

-- gemini_video_analysis（原服务名）→ 归并到 gemini_aistudio_text（仅当当前 key 为空）
UPDATE llm_provider_configs lpc
INNER JOIN api_keys ak       ON ak.service = 'gemini_video_analysis'
INNER JOIN users    u        ON u.id = ak.user_id AND u.username = 'admin' AND u.is_active = 1
SET lpc.api_key = COALESCE(NULLIF(lpc.api_key, ''), ak.key_value)
WHERE lpc.provider_code = 'gemini_aistudio_text'
  AND (lpc.api_key IS NULL OR lpc.api_key = '')
  AND ak.key_value IS NOT NULL AND ak.key_value <> '';

-- gemini_cloud → gemini_cloud_text + gemini_cloud_image
UPDATE llm_provider_configs lpc
INNER JOIN api_keys ak       ON ak.service = 'gemini_cloud'
INNER JOIN users    u        ON u.id = ak.user_id AND u.username = 'admin' AND u.is_active = 1
SET lpc.api_key = COALESCE(NULLIF(lpc.api_key, ''), ak.key_value)
WHERE lpc.provider_code IN ('gemini_cloud_text', 'gemini_cloud_image');

-- doubao_llm → doubao_llm（明确不回落到 doubao_seedream / doubao_asr）
UPDATE llm_provider_configs lpc
INNER JOIN api_keys ak       ON ak.service = 'doubao_llm'
INNER JOIN users    u        ON u.id = ak.user_id AND u.username = 'admin' AND u.is_active = 1
SET
  lpc.api_key  = COALESCE(NULLIF(lpc.api_key, ''),  ak.key_value),
  lpc.base_url = COALESCE(NULLIF(lpc.base_url, ''), NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ak.extra_config, '$.base_url')), ''), 'null')),
  lpc.model_id = COALESCE(NULLIF(lpc.model_id, ''), NULLIF(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ak.extra_config, '$.model_id')), ''), 'null'))
WHERE lpc.provider_code = 'doubao_llm';

-- doubao_asr → doubao_asr（保留原 extra_config 中的 app_id / cluster / resource_id）
UPDATE llm_provider_configs lpc
INNER JOIN api_keys ak       ON ak.service = 'doubao_asr'
INNER JOIN users    u        ON u.id = ak.user_id AND u.username = 'admin' AND u.is_active = 1
SET
  lpc.api_key      = COALESCE(NULLIF(lpc.api_key, ''), ak.key_value),
  lpc.extra_config = COALESCE(lpc.extra_config, ak.extra_config)
WHERE lpc.provider_code = 'doubao_asr';

-- volc（runtime.py 历史用过的 service 名）→ 归并到 doubao_asr
UPDATE llm_provider_configs lpc
INNER JOIN api_keys ak       ON ak.service = 'volc'
INNER JOIN users    u        ON u.id = ak.user_id AND u.username = 'admin' AND u.is_active = 1
SET lpc.api_key = COALESCE(NULLIF(lpc.api_key, ''), ak.key_value)
WHERE lpc.provider_code = 'doubao_asr'
  AND (lpc.api_key IS NULL OR lpc.api_key = '')
  AND ak.key_value IS NOT NULL AND ak.key_value <> '';

-- elevenlabs → elevenlabs_tts
UPDATE llm_provider_configs lpc
INNER JOIN api_keys ak       ON ak.service = 'elevenlabs'
INNER JOIN users    u        ON u.id = ak.user_id AND u.username = 'admin' AND u.is_active = 1
SET lpc.api_key = COALESCE(NULLIF(lpc.api_key, ''), ak.key_value)
WHERE lpc.provider_code = 'elevenlabs_tts';

-- seedance → seedance_video
UPDATE llm_provider_configs lpc
INNER JOIN api_keys ak       ON ak.service = 'seedance'
INNER JOIN users    u        ON u.id = ak.user_id AND u.username = 'admin' AND u.is_active = 1
SET lpc.api_key = COALESCE(NULLIF(lpc.api_key, ''), ak.key_value)
WHERE lpc.provider_code = 'seedance_video';

-- 注：
-- doubao_seedream、apimart_image、subtitle_removal、openapi_materials 在旧 api_keys 表里没有
-- 对应 service 行，无法从 DB 迁移 key。首次上线后，请 admin 在 /settings 里填写这几条。
-- 历史 .env 变量（APIMART_IMAGE_API_KEY、SUBTITLE_REMOVAL_PROVIDER_TOKEN、OPENAPI_MEDIA_API_KEY
-- 等）不再作为运行时 fallback；需要时由 admin 粘贴到 /settings。
