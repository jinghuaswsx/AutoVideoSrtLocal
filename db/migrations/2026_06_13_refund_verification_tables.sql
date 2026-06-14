CREATE TABLE IF NOT EXISTS refund_verification_batches (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  source_files JSON,
  site_code VARCHAR(16) DEFAULT NULL,
  matched_count INT NOT NULL DEFAULT 0,
  unmatched_count INT NOT NULL DEFAULT 0,
  anomaly_count INT NOT NULL DEFAULT 0,
  total_refund_usd DECIMAL(12,4) NOT NULL DEFAULT 0,
  current_reserve_usd DECIMAL(12,4) NOT NULL DEFAULT 0,
  delta_usd DECIMAL(12,4) NOT NULL DEFAULT 0,
  created_by VARCHAR(64) DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  applied_at DATETIME DEFAULT NULL,
  KEY idx_rvb_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='退款核验批次';

CREATE TABLE IF NOT EXISTS refund_verifications (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  batch_id BIGINT NOT NULL,
  extended_order_id VARCHAR(128) NOT NULL,
  site_code VARCHAR(16) DEFAULT NULL,
  refund_amount_usd DECIMAL(12,4) DEFAULT NULL,
  refund_source VARCHAR(16) DEFAULT NULL,
  order_financial_status VARCHAR(32) DEFAULT NULL,
  matched_package_ids JSON,
  match_status VARCHAR(16) NOT NULL DEFAULT 'matched',
  note VARCHAR(255) DEFAULT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_rv_batch (batch_id),
  KEY idx_rv_order_status (extended_order_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='退款核验明细(订单级)';
