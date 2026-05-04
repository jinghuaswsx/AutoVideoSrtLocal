-- 2026-05-07: campaign-product 人工配对兜底表
--
-- 用途：当 Meta 广告 campaign 名无法自动匹配到 media_products（normalized_campaign_code
-- 跟 product_code 对不上时），让业务方在 /order-profit 看板里手工指定映射。
--
-- 写入后：
--   1. 立刻 UPDATE 历史 meta_ad_daily_campaign_metrics，把 product_id 写进去
--   2. 未来同步进来的新广告数据，meta_ads.resolve_ad_product_match 优先查这个表
--
-- 详细 plan：docs/superpowers/plans/2026-05-04-order-profit-calculation.md 阶段 5

CREATE TABLE IF NOT EXISTS campaign_product_overrides (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  normalized_campaign_code VARCHAR(255) NOT NULL,
  product_id INT NOT NULL,
  product_code VARCHAR(128),
  reason VARCHAR(255),
  created_by VARCHAR(64),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_campaign_override_code (normalized_campaign_code),
  KEY idx_override_product (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Manual campaign-to-product overrides for ads not auto-matched';
