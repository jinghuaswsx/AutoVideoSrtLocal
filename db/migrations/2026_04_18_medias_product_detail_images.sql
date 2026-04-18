-- db/migrations/2026_04_18_medias_product_detail_images.sql
-- 商品详情图：产品关联多张详情图片，按语种区分（第一轮只在英语下维护原始版）

CREATE TABLE IF NOT EXISTS media_product_detail_images (
  id           INT          AUTO_INCREMENT PRIMARY KEY,
  product_id   INT          NOT NULL,
  lang         VARCHAR(8)   NOT NULL,
  sort_order   INT          NOT NULL DEFAULT 0,
  object_key   VARCHAR(512) NOT NULL,
  content_type VARCHAR(64)  DEFAULT NULL,
  file_size    BIGINT       DEFAULT NULL,
  width        INT          DEFAULT NULL,
  height       INT          DEFAULT NULL,
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                     ON UPDATE CURRENT_TIMESTAMP,
  deleted_at   DATETIME     DEFAULT NULL,
  KEY idx_product_lang_sort   (product_id, lang, sort_order),
  KEY idx_product_lang_active (product_id, lang, deleted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
