-- Meta hot posts video copyability analysis results.
-- Docs-anchor: docs/superpowers/specs/2026-05-14-meta-hot-posts-video-copyability-analysis-design.md

CREATE TABLE IF NOT EXISTS meta_hot_post_video_copyability_analyses (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  hot_post_id BIGINT UNSIGNED NOT NULL,
  wedev_post_id BIGINT UNSIGNED NULL,
  product_url VARCHAR(2048) NOT NULL,
  local_video_path VARCHAR(2048) NOT NULL,
  compressed_video_path VARCHAR(2048) NULL,
  status ENUM('pending', 'running', 'done', 'failed', 'suspended') NOT NULL DEFAULT 'pending',
  attempts INT UNSIGNED NOT NULL DEFAULT 0,
  last_error MEDIUMTEXT NULL,
  overall_score DECIMAL(5, 2) NULL,
  copyability_score DECIMAL(5, 2) NULL,
  meta_us_ad_fit_score DECIMAL(5, 2) NULL,
  product_fit_score DECIMAL(5, 2) NULL,
  compliance_risk_score DECIMAL(5, 2) NULL,
  recommendation VARCHAR(32) NULL,
  summary TEXT NULL,
  llm_provider VARCHAR(64) NULL,
  llm_model VARCHAR(128) NULL,
  analysis_json JSON NULL,
  analyzed_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uniq_meta_hot_post_video_copyability_hot_post (hot_post_id),
  KEY idx_meta_hot_post_video_copyability_status (status, attempts, updated_at),
  KEY idx_meta_hot_post_video_copyability_score (status, overall_score, copyability_score, meta_us_ad_fit_score),
  KEY idx_meta_hot_post_video_copyability_product_url (product_url(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
