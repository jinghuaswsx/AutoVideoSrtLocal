-- DDL for media_product_shopify_uploaded_images table
CREATE TABLE IF NOT EXISTS media_product_shopify_uploaded_images (
  id                  INT AUTO_INCREMENT PRIMARY KEY,
  product_id          INT NOT NULL,
  lang                VARCHAR(8) NOT NULL,
  domain              VARCHAR(255) NOT NULL,
  image_kind          VARCHAR(32) NOT NULL,    -- 'cover' or 'detail'
  image_id            VARCHAR(64) NOT NULL,    -- e.g. 'cover-de' or 'detail-17808'
  shopify_cdn_url     VARCHAR(1024) NOT NULL,
  updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_prod_lang_dom_kind_id (product_id, lang, domain, image_kind, image_id),
  KEY idx_prod_lang_dom (product_id, lang, domain)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
