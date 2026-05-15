-- Add Niuma subtitle-removal API credentials to infra_credentials.
--
-- The API key is intentionally not seeded here. Configure it from
-- /settings?tab=infrastructure or an operator-run server DB update.

-- This file keeps the handoff-requested 20250515 name. Because that sorts before
-- 2026_05_04_infra_credentials.sql on a fresh database, guard the target table
-- here with the same schema before inserting the Niuma row.
CREATE TABLE IF NOT EXISTS infra_credentials (
  code         VARCHAR(64)  NOT NULL,
  display_name VARCHAR(128) NOT NULL,
  group_code   VARCHAR(32)  NOT NULL DEFAULT 'object_storage',
  config       JSON         NULL,
  enabled      TINYINT(1)   NOT NULL DEFAULT 1,
  updated_by   BIGINT       NULL,
  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (code),
  KEY idx_infra_credentials_group (group_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO infra_credentials (code, display_name, group_code, config) VALUES
  ('niuma_main', '牛马去字幕 API', 'external_api', JSON_OBJECT(
    'api_key', '',
    'base_url', 'https://goodline.simplemokey.com/api/openAi'
  ));
