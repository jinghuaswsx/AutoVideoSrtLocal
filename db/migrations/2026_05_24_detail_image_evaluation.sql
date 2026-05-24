-- db/migrations/2026_05_24_detail_image_evaluation.sql
-- 针对已生成的商品详情图提供独立质量检测，并将结果持久化在详情图表中

ALTER TABLE media_product_detail_images
  ADD COLUMN eval_status VARCHAR(32) DEFAULT NULL COMMENT 'pending|running|done|failed',
  ADD COLUMN eval_result_json TEXT DEFAULT NULL COMMENT '质量检测模型返回结果的json',
  ADD COLUMN eval_error TEXT DEFAULT NULL COMMENT '评估错误信息',
  ADD COLUMN eval_channel VARCHAR(64) DEFAULT NULL COMMENT 'LLM 供应商',
  ADD COLUMN eval_model_id VARCHAR(128) DEFAULT NULL COMMENT 'LLM 模型',
  ADD COLUMN eval_updated_at DATETIME DEFAULT NULL COMMENT '最新评估时间';
