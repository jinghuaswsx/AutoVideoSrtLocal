ALTER TABLE media_products
  ADD COLUMN shopify_image_status_json JSON NULL COMMENT '按语种记录 Shopify 图片替换和链接确认状态 {lang: payload}';

CREATE TABLE IF NOT EXISTS media_shopify_image_replace_tasks (
  id                 BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
  product_id          INT          NOT NULL,
  product_code        VARCHAR(128) NOT NULL,
  lang                VARCHAR(8)   NOT NULL,
  shopify_product_id  VARCHAR(64)  NOT NULL,
  link_url            VARCHAR(1024) DEFAULT NULL,
  status              VARCHAR(24)  NOT NULL DEFAULT 'pending',
  attempt_count       INT          NOT NULL DEFAULT 0,
  max_attempts        INT          NOT NULL DEFAULT 3,
  worker_id           VARCHAR(128) DEFAULT NULL,
  locked_until        DATETIME     DEFAULT NULL,
  claimed_at          DATETIME     DEFAULT NULL,
  started_at          DATETIME     DEFAULT NULL,
  finished_at         DATETIME     DEFAULT NULL,
  error_code          VARCHAR(64)  DEFAULT NULL,
  error_message       TEXT         DEFAULT NULL,
  result_json         JSON         DEFAULT NULL,
  created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_shopify_image_task_status_lock (status, locked_until, id),
  KEY idx_shopify_image_task_product_lang (product_id, lang, status),
  KEY idx_shopify_image_task_worker (worker_id, status, locked_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Shopify 图片替换任务中心';
