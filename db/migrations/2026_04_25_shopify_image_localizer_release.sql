CREATE TABLE IF NOT EXISTS system_settings (
  `key` VARCHAR(191) NOT NULL PRIMARY KEY,
  `value` TEXT,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO system_settings (`key`, `value`) VALUES (
  'shopify_image_localizer_release',
  '{"version":"1.0","released_at":"2026-04-25 14:34","release_note":"Shopify Image Localizer desktop tool 1.0","download_url":"/static/downloads/tools/ShopifyImageLocalizer-portable-1.0.zip","filename":"ShopifyImageLocalizer-portable-1.0.zip"}'
) ON DUPLICATE KEY UPDATE `value` = VALUES(`value`);
