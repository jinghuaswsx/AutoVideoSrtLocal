-- Browser login credentials stored in plaintext by explicit operator request.
--
-- Docs-anchor: docs/superpowers/specs/2026-05-08-meta-login-plaintext-autofill-design.md

CREATE TABLE IF NOT EXISTS browser_login_credentials (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  env_code VARCHAR(64) NOT NULL,
  provider VARCHAR(64) NOT NULL,
  username VARCHAR(255) NOT NULL,
  password VARCHAR(1024) NOT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  last_login_at DATETIME DEFAULT NULL,
  last_login_status VARCHAR(64) DEFAULT NULL,
  last_error VARCHAR(512) DEFAULT NULL,
  updated_by INT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_browser_login_credentials_env_provider (env_code, provider),
  KEY idx_browser_login_credentials_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Browser login credentials stored in plaintext by explicit operator request';
