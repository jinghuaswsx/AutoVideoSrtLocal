-- db/migrations/2026_04_20_ai_billing.sql
-- AI 用量账单：扩展 usage_logs，并新增 ai_model_prices 定价表

TRUNCATE TABLE usage_logs;

SET @add_use_case_code = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'usage_logs'
      AND column_name = 'use_case_code'
  ) = 0,
  'ALTER TABLE usage_logs ADD COLUMN use_case_code VARCHAR(64) DEFAULT NULL AFTER service',
  'SELECT 1'
);
PREPARE stmt FROM @add_use_case_code;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @add_module = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'usage_logs'
      AND column_name = 'module'
  ) = 0,
  'ALTER TABLE usage_logs ADD COLUMN module VARCHAR(32) DEFAULT NULL AFTER use_case_code',
  'SELECT 1'
);
PREPARE stmt FROM @add_module;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @add_provider = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'usage_logs'
      AND column_name = 'provider'
  ) = 0,
  'ALTER TABLE usage_logs ADD COLUMN provider VARCHAR(32) DEFAULT NULL AFTER module',
  'SELECT 1'
);
PREPARE stmt FROM @add_provider;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @add_request_units = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'usage_logs'
      AND column_name = 'request_units'
  ) = 0,
  'ALTER TABLE usage_logs ADD COLUMN request_units INT DEFAULT NULL AFTER audio_duration_seconds',
  'SELECT 1'
);
PREPARE stmt FROM @add_request_units;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @add_units_type = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'usage_logs'
      AND column_name = 'units_type'
  ) = 0,
  'ALTER TABLE usage_logs ADD COLUMN units_type VARCHAR(16) DEFAULT NULL AFTER request_units',
  'SELECT 1'
);
PREPARE stmt FROM @add_units_type;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @add_cost_cny = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'usage_logs'
      AND column_name = 'cost_cny'
  ) = 0,
  'ALTER TABLE usage_logs ADD COLUMN cost_cny DECIMAL(12,6) DEFAULT NULL AFTER units_type',
  'SELECT 1'
);
PREPARE stmt FROM @add_cost_cny;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @add_cost_source = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'usage_logs'
      AND column_name = 'cost_source'
  ) = 0,
  'ALTER TABLE usage_logs ADD COLUMN cost_source ENUM(''response'', ''pricebook'', ''unknown'') NOT NULL DEFAULT ''unknown'' AFTER cost_cny',
  'SELECT 1'
);
PREPARE stmt FROM @add_cost_source;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @create_idx_called_at = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'usage_logs'
      AND index_name = 'idx_called_at'
  ) = 0,
  'CREATE INDEX idx_called_at ON usage_logs (called_at)',
  'SELECT 1'
);
PREPARE stmt FROM @create_idx_called_at;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @create_idx_user_module = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'usage_logs'
      AND index_name = 'idx_user_module'
  ) = 0,
  'CREATE INDEX idx_user_module ON usage_logs (user_id, module, called_at)',
  'SELECT 1'
);
PREPARE stmt FROM @create_idx_user_module;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @create_idx_use_case = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'usage_logs'
      AND index_name = 'idx_use_case'
  ) = 0,
  'CREATE INDEX idx_use_case ON usage_logs (use_case_code, called_at)',
  'SELECT 1'
);
PREPARE stmt FROM @create_idx_use_case;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

CREATE TABLE IF NOT EXISTS ai_model_prices (
  id INT AUTO_INCREMENT PRIMARY KEY,
  provider VARCHAR(32) NOT NULL,
  model VARCHAR(128) NOT NULL,
  units_type VARCHAR(16) NOT NULL,
  unit_input_cny DECIMAL(14,8) DEFAULT NULL,
  unit_output_cny DECIMAL(14,8) DEFAULT NULL,
  unit_flat_cny DECIMAL(14,8) DEFAULT NULL,
  note VARCHAR(255) DEFAULT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_provider_model (provider, model)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO ai_model_prices (
  provider,
  model,
  units_type,
  unit_input_cny,
  unit_output_cny,
  unit_flat_cny,
  note
)
VALUES
  ('gemini_aistudio', 'gemini-3.1-pro-preview', 'tokens', 0.00005780, 0.00023120, NULL, '待复核：8.5/34 USD/M ×6.8'),
  ('gemini_aistudio', 'gemini-2.5-flash', 'tokens', 0.00000204, 0.00000816, NULL, '待复核：0.3/1.2 USD/M ×6.8'),
  ('gemini_aistudio', 'gemini-3-pro-image-preview', 'images', NULL, NULL, 0.26520000, '待复核：0.039 USD/image ×6.8'),
  ('gemini_vertex', 'gemini-3.1-flash-lite-preview', 'tokens', 0.00000816, 0.00003264, NULL, '待复核：1.2/4.8 USD/M ×6.8'),
  ('gemini_vertex', 'gemini-3.1-pro-preview', 'tokens', 0.00005780, 0.00023120, NULL, '待复核：8.5/34 USD/M ×6.8'),
  ('doubao', 'doubao-1-5-pro-32k', 'tokens', 0.00000600, 0.00001200, NULL, '待复核：0.006/0.012 RMB/千tok'),
  ('elevenlabs', '*', 'chars', NULL, NULL, 0.00016500, '待复核：≈0.165 RMB/千字符'),
  ('doubao_asr', '*', 'seconds', NULL, NULL, 0.01400000, '待复核：≈0.014 RMB/秒'),
  ('openrouter', '*', 'tokens', NULL, NULL, NULL, '响应 cost 缺失时兜底，留空不计费')
ON DUPLICATE KEY UPDATE
  units_type = VALUES(units_type),
  unit_input_cny = VALUES(unit_input_cny),
  unit_output_cny = VALUES(unit_output_cny),
  unit_flat_cny = VALUES(unit_flat_cny),
  note = VALUES(note);
