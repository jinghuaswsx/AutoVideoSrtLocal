-- 订单分析：Shopify 订单明细表 + 产品标题缓存表

CREATE TABLE IF NOT EXISTS shopify_orders (
  id                 BIGINT AUTO_INCREMENT PRIMARY KEY,
  shopify_order_id   BIGINT       NOT NULL COMMENT 'Shopify order Id (globally unique per order)',
  order_name         VARCHAR(32)  DEFAULT NULL COMMENT 'Order number like #10353',
  created_at_order   DATETIME     DEFAULT NULL COMMENT 'Order creation timestamp',
  lineitem_name      VARCHAR(500) NOT NULL COMMENT 'Lineitem name — product title from Shopify',
  lineitem_sku       VARCHAR(128) DEFAULT NULL COMMENT 'Lineitem sku',
  lineitem_quantity  INT          NOT NULL DEFAULT 1,
  lineitem_price     DECIMAL(12,2) DEFAULT NULL COMMENT 'Unit price',
  billing_country    VARCHAR(8)   DEFAULT NULL COMMENT '2-letter country code',
  total              DECIMAL(12,2) DEFAULT NULL COMMENT 'Order total',
  subtotal           DECIMAL(12,2) DEFAULT NULL,
  shipping           DECIMAL(12,2) DEFAULT NULL,
  currency           VARCHAR(8)   DEFAULT NULL,
  financial_status   VARCHAR(32)  DEFAULT NULL,
  fulfillment_status VARCHAR(32)  DEFAULT NULL,
  vendor             VARCHAR(128) DEFAULT NULL,
  product_id         INT          DEFAULT NULL COMMENT 'FK to media_products.id after matching',
  imported_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_order_lineitem (shopify_order_id, lineitem_name(191)),
  KEY idx_created_at_order (created_at_order),
  KEY idx_billing_country (billing_country),
  KEY idx_product_id (product_id),
  KEY idx_lineitem_name (lineitem_name(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Shopify order line items imported from CSV/Excel';

CREATE TABLE IF NOT EXISTS product_title_cache (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  product_id   INT          NOT NULL COMMENT 'FK to media_products.id',
  product_code VARCHAR(128) NOT NULL COMMENT 'Shopify handle slug',
  page_title   VARCHAR(500) NOT NULL COMMENT 'Title fetched from English product page',
  fetched_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_product_id (product_id),
  KEY idx_page_title (page_title(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Cached product page titles for order-product matching';
