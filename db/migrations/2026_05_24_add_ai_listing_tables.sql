-- Create table for AI listing tasks
CREATE TABLE IF NOT EXISTS `ai_listing_tasks` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `product_code` VARCHAR(64) UNIQUE NOT NULL COMMENT 'Shopify Listing 唯一编码',
  `source_type` VARCHAR(32) NOT NULL DEFAULT 'meta_hot_post' COMMENT '任务来源: meta_hot_post / manual_input',
  `source_link` TEXT NOT NULL COMMENT 'Meta 贴链接或直接输入的博客/落地页链接',
  `transit_link` TEXT DEFAULT NULL COMMENT '二跳解析出的真实商品落地页链接',
  `target_store_domain` VARCHAR(255) NOT NULL COMMENT '发布的目标 Shopify 店铺域名',
  `status` VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT '任务状态: pending / parsing / generating / completed / failed',
  `pricing_ratio` DECIMAL(10, 2) NOT NULL DEFAULT 1.00 COMMENT '定价比例系数',
  `pricing_offset` DECIMAL(10, 2) NOT NULL DEFAULT 0.00 COMMENT '定价浮动固定值',
  `generated_title` TEXT DEFAULT NULL COMMENT 'AI 生成的英文商品标题',
  `generated_skus_json` TEXT DEFAULT NULL COMMENT 'AI 生成的 SKU 列表及对应定价价格（JSON格式）',
  `generated_html_desc` LONGTEXT DEFAULT NULL COMMENT 'AI 生成并重构排版后的详情页 HTML',
  `error_message` TEXT DEFAULT NULL COMMENT '报错信息记录',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX `idx_status` (`status`),
  INDEX `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Create table for AI listing assets (Images & Carousels)
CREATE TABLE IF NOT EXISTS `ai_listing_assets` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `task_id` INT NOT NULL COMMENT '外键关联 ai_listing_tasks.id',
  `asset_type` VARCHAR(32) NOT NULL COMMENT '资产类别: carousel (轮播图) / detail_image (详情页图)',
  `original_url` TEXT NOT NULL COMMENT '原站抓取的源图片URL',
  `transformed_url` TEXT DEFAULT NULL COMMENT '本地/TOS存储的翻译/重绘后图URL',
  `ai_classification` VARCHAR(64) DEFAULT NULL COMMENT 'AI 诊断标签: showcase (细节展示) / badge (徽章) / review (买家图) 等',
  `is_selected` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '运营勾选启用状态: 0=不使用 / 1=使用',
  `sort_order` INT NOT NULL DEFAULT 0 COMMENT '排序权重，数字越小排在越前',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (`task_id`) REFERENCES `ai_listing_tasks` (`id`) ON DELETE CASCADE,
  INDEX `idx_task_asset` (`task_id`, `asset_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
