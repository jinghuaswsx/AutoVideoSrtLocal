-- Product link domains: global domain registry plus per-product enablement.

CREATE TABLE IF NOT EXISTS media_link_domains (
  id INT AUTO_INCREMENT PRIMARY KEY,
  domain VARCHAR(255) NOT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  sort_order INT NOT NULL DEFAULT 0,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_media_link_domains_domain (domain),
  KEY idx_media_link_domains_enabled (enabled, sort_order)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS media_product_link_domains (
  product_id INT NOT NULL,
  domain_id INT NOT NULL,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (product_id, domain_id),
  KEY idx_media_product_link_domains_domain (domain_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO media_link_domains (domain, enabled, sort_order)
VALUES
  ('newjoyloo.com', 1, 10),
  ('omurio.com', 1, 20)
ON DUPLICATE KEY UPDATE
  sort_order = VALUES(sort_order);
