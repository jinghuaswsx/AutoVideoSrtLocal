CREATE TABLE IF NOT EXISTS media_push_readiness_overrides (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  media_item_id INT NOT NULL,
  readiness_key VARCHAR(64) NOT NULL,
  step_key VARCHAR(64) NOT NULL,
  actor_user_id INT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_media_push_readiness_override_item_key (media_item_id, readiness_key),
  KEY idx_media_push_readiness_overrides_item (media_item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
