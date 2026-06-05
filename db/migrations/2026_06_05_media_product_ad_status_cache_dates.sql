-- Add delivery start time, end time and active days columns to media_product_ad_summary_cache
-- Docs anchor: docs/superpowers/specs/2026-05-28-medias-product-ad-status-cache-design.md
ALTER TABLE media_product_ad_summary_cache
  ADD COLUMN delivery_start_time DATETIME DEFAULT NULL COMMENT '第一次同步到这个产品的广告消耗数据的时间',
  ADD COLUMN delivery_end_time DATETIME DEFAULT NULL COMMENT '最后一次同步到这个产品的投放数据的时间',
  ADD COLUMN active_days INT NOT NULL DEFAULT 0 COMMENT '一天有广告消耗数据就算这一天活跃，累积统计有多少个活跃天';
