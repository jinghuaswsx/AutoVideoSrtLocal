-- Per-product, per-language, per-domain HTTP availability cache.
-- Powers the "产品链接管理" modal in the medias edit page.
-- Spec: docs/superpowers/specs/2026-05-09-product-link-management-modal.md

CREATE TABLE IF NOT EXISTS media_product_link_availability (
  product_id  INT          NOT NULL,
  lang        VARCHAR(8)   NOT NULL,
  domain      VARCHAR(255) NOT NULL,
  link_url    VARCHAR(1024) NOT NULL,
  http_status SMALLINT     DEFAULT NULL,
  ok          TINYINT(1)   NOT NULL DEFAULT 0,
  error       VARCHAR(255) DEFAULT NULL,
  elapsed_ms  INT          DEFAULT NULL,
  checked_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (product_id, lang, domain),
  KEY idx_media_product_link_avail_product_lang (product_id, lang)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Product link HTTP availability cache (per product x lang x domain).';
