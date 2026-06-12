-- Ad alert handled/ignored markers for the stop-loss workflow.
-- Docs anchor: docs/superpowers/specs/2026-06-12-ad-alert-action-workflow-design.md

CREATE TABLE IF NOT EXISTS ad_alert_actions (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  scope VARCHAR(32) NOT NULL COMMENT 'high_loss | language',
  target_key VARCHAR(255) NOT NULL COMMENT 'high_loss: {ad_account_id}:{code}; language: {product_id}:{lang}',
  action ENUM('resolved','ignored') NOT NULL,
  note VARCHAR(500) DEFAULT NULL,
  operator_user_id INT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_ad_alert_action_target (scope, target_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
