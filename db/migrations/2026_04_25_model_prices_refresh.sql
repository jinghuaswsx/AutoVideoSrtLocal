-- 价格表完善：补齐目前系统实际调用的全部 provider × model 定价。
-- 单位：unit_input_cny / unit_output_cny 为 CNY/每token；unit_flat_cny 为平铺单位价
-- （images=CNY/张，seconds=CNY/秒，chars=CNY/字符）。
-- 所有行用 INSERT ... ON DUPLICATE KEY UPDATE，可重复执行。

-- ========== APIMART（新增 channel） ==========
-- GPT-Image-2 1K 分辨率约 $0.04/image ×6.8 ≈ ¥0.27；留一点 markup 空间走 ¥0.30
INSERT INTO ai_model_prices (provider, model, units_type, unit_flat_cny, note)
VALUES ('apimart', 'gpt-image-2', 'images', 0.30000000, '待复核：APIMART GPT-Image-2 1K 分辨率，官方价 ~$0.04/image')
ON DUPLICATE KEY UPDATE unit_flat_cny=VALUES(unit_flat_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;

-- APIMART 代理的 Gemini 图像模型（参考官方价，小幅 markup）
INSERT INTO ai_model_prices (provider, model, units_type, unit_flat_cny, note) VALUES
  ('apimart', 'gemini-3-pro-image-preview',      'images', 1.00000000, '待复核：APIMART 代理 Nano Banana Pro（官方 $0.134/img ×6.8 ≈ ¥0.91 + markup）'),
  ('apimart', 'gemini-3.1-flash-image-preview',  'images', 0.50000000, '待复核：APIMART 代理 Nano Banana 2（官方 $0.067/img ×6.8 ≈ ¥0.46 + markup）'),
  ('apimart', 'gemini-2.5-flash-image-preview',  'images', 0.30000000, '待复核：APIMART 代理 Nano Banana 1（官方 $0.039/img ×6.8 ≈ ¥0.27 + markup）')
ON DUPLICATE KEY UPDATE unit_flat_cny=VALUES(unit_flat_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;

-- APIMART 通配兜底
INSERT INTO ai_model_prices (provider, model, units_type, unit_flat_cny, note)
VALUES ('apimart', '*', 'images', 0.50000000, '待复核：APIMART 默认图片单价，未精确入表的走此行')
ON DUPLICATE KEY UPDATE unit_flat_cny=VALUES(unit_flat_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;

-- ========== Doubao 新增模型 ==========
-- Seedream 5.0 图像生成：官方 ~¥0.25/image（1024×1024）
INSERT INTO ai_model_prices (provider, model, units_type, unit_flat_cny, note)
VALUES ('doubao', 'doubao-seedream-5-0-260128', 'images', 0.25000000, '待复核：豆包 Seedream 5.0 图像生成 1024×1024 参考价')
ON DUPLICATE KEY UPDATE unit_flat_cny=VALUES(unit_flat_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;

-- Seedance 视频生成：按秒计费，参考 1.5-pro 和 2.0 pro 近似价 ¥0.50/秒（5s 720p ≈ ¥2.5）
INSERT INTO ai_model_prices (provider, model, units_type, unit_flat_cny, note) VALUES
  ('doubao', 'doubao-seedance-1-5-pro-251215', 'seconds', 0.50000000, '待复核：Seedance 1.5 Pro，按秒计费；实际按 resolution+duration 档位'),
  ('doubao', 'doubao-seedance-2-0-260128',     'seconds', 0.50000000, '待复核：Seedance 2.0 Pro（默认模型），按秒计费')
ON DUPLICATE KEY UPDATE unit_flat_cny=VALUES(unit_flat_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;

-- ========== Gemini AI Studio 补齐 ==========
-- 2.5-flash-image-preview（Nano Banana 1，初代）
INSERT INTO ai_model_prices (provider, model, units_type, unit_flat_cny, note)
VALUES ('gemini_aistudio', 'gemini-2.5-flash-image-preview', 'images', 0.27000000, '待复核：Nano Banana 1（初代），$0.039/img ×6.8')
ON DUPLICATE KEY UPDATE unit_flat_cny=VALUES(unit_flat_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;

-- gemini-3-flash-preview（debug_vertex.py 中出现）
INSERT INTO ai_model_prices (provider, model, units_type, unit_input_cny, unit_output_cny, note)
VALUES ('gemini_aistudio', 'gemini-3-flash-preview', 'tokens', 0.00000816, 0.00003264, '待复核：参考 3.1-flash-lite 价格')
ON DUPLICATE KEY UPDATE unit_input_cny=VALUES(unit_input_cny), unit_output_cny=VALUES(unit_output_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;

-- ========== Gemini Vertex（Cloud）补齐 ==========
-- Cloud 定价和 AI Studio 基本一致（同模型同价）
INSERT INTO ai_model_prices (provider, model, units_type, unit_input_cny, unit_output_cny, note) VALUES
  ('gemini_vertex', 'gemini-2.5-flash',           'tokens', 0.00000204, 0.00000816, '待复核：同 AI Studio'),
  ('gemini_vertex', 'gemini-3.1-pro-preview',     'tokens', 0.00005780, 0.00023120, '待复核：同 AI Studio'),
  ('gemini_vertex', 'gemini-3-flash-preview',     'tokens', 0.00000816, 0.00003264, '待复核：参考 3.1-flash-lite 价格')
ON DUPLICATE KEY UPDATE unit_input_cny=VALUES(unit_input_cny), unit_output_cny=VALUES(unit_output_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;

INSERT INTO ai_model_prices (provider, model, units_type, unit_flat_cny, note) VALUES
  ('gemini_vertex', 'gemini-3-pro-image-preview',     'images', 0.91120000, '待复核：Cloud 同 AI Studio 价'),
  ('gemini_vertex', 'gemini-3.1-flash-image-preview', 'images', 0.45560000, '待复核：Cloud 同 AI Studio 价'),
  ('gemini_vertex', 'gemini-2.5-flash-image-preview', 'images', 0.27000000, '待复核：Cloud 同 AI Studio 价')
ON DUPLICATE KEY UPDATE unit_flat_cny=VALUES(unit_flat_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;

-- ========== OpenRouter 图像模型补齐 ==========
-- OpenRouter 会在响应里带 cost 字段；此处是响应缺失时的兜底。markup 约 5-10%。
INSERT INTO ai_model_prices (provider, model, units_type, unit_flat_cny, note) VALUES
  ('openrouter', 'gemini-3-pro-image-preview',          'images', 0.95000000, '待复核：OpenRouter 兜底，响应 cost 优先'),
  ('openrouter', 'gemini-3.1-flash-image-preview',      'images', 0.48000000, '待复核：OpenRouter 兜底'),
  ('openrouter', 'gemini-2.5-flash-image-preview',      'images', 0.28000000, '待复核：OpenRouter 兜底'),
  ('openrouter', 'google/gemini-3-pro-image-preview',        'images', 0.95000000, '待复核：OpenRouter 带前缀形式，兜底'),
  ('openrouter', 'google/gemini-3.1-flash-image-preview',    'images', 0.48000000, '待复核：OpenRouter 带前缀形式，兜底'),
  ('openrouter', 'google/gemini-2.5-flash-image-preview',    'images', 0.28000000, '待复核：OpenRouter 带前缀形式，兜底')
ON DUPLICATE KEY UPDATE unit_flat_cny=VALUES(unit_flat_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;

-- OpenAI Image 2（经由 OpenRouter 的虚拟 model_id）：low / mid / high 三档
-- 参考 OpenAI 官方价：low ~$0.02/img、medium ~$0.042/img、high ~$0.167/img
INSERT INTO ai_model_prices (provider, model, units_type, unit_flat_cny, note) VALUES
  ('openrouter', 'openai/gpt-5.4-image-2:low',  'images', 0.15000000, '待复核：OpenAI Image 2 low（~$0.02/img ×6.8 + markup）'),
  ('openrouter', 'openai/gpt-5.4-image-2:mid',  'images', 0.32000000, '待复核：OpenAI Image 2 medium（~$0.042/img ×6.8 + markup）'),
  ('openrouter', 'openai/gpt-5.4-image-2:high', 'images', 1.20000000, '待复核：OpenAI Image 2 high（~$0.167/img ×6.8 + markup）')
ON DUPLICATE KEY UPDATE unit_flat_cny=VALUES(unit_flat_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;

-- ========== OpenRouter LLM 补齐（常用 Gemini 系列） ==========
-- 响应 cost 存在时 ai_billing 会优先使用，这里是兜底
INSERT INTO ai_model_prices (provider, model, units_type, unit_input_cny, unit_output_cny, note) VALUES
  ('openrouter', 'google/gemini-2.5-flash',          'tokens', 0.00000204, 0.00000816, '待复核：OpenRouter 兜底'),
  ('openrouter', 'google/gemini-3.1-pro-preview',    'tokens', 0.00005780, 0.00023120, '待复核：OpenRouter 兜底')
ON DUPLICATE KEY UPDATE unit_input_cny=VALUES(unit_input_cny), unit_output_cny=VALUES(unit_output_cny), note=VALUES(note), updated_at=CURRENT_TIMESTAMP;
