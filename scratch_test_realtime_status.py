import sys
sys.path.append('/opt/autovideosrt-test')
from datetime import datetime, date
from appcore.db import query, execute
from appcore.media_product_ad_status_cache import refresh_all

PID = 316
LANG = 'de'
print("--- Setup test database and data ---")

execute("""
CREATE TABLE IF NOT EXISTS `meta_ad_realtime_daily_campaign_metrics` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `import_run_id` bigint NOT NULL,
  `business_date` date NOT NULL,
  `snapshot_at` datetime NOT NULL,
  `data_completeness` varchar(32) NOT NULL DEFAULT 'realtime_partial',
  `ad_account_id` varchar(32) DEFAULT NULL,
  `ad_account_name` varchar(128) DEFAULT NULL,
  `campaign_id` varchar(64) DEFAULT NULL,
  `campaign_name` varchar(255) NOT NULL,
  `normalized_campaign_code` varchar(255) NOT NULL,
  `result_count` int NOT NULL DEFAULT '0',
  `spend_usd` decimal(14,4) NOT NULL DEFAULT '0.0000',
  `purchase_value_usd` decimal(14,4) NOT NULL DEFAULT '0.0000',
  `impressions` int NOT NULL DEFAULT '0',
  `clicks` int NOT NULL DEFAULT '0',
  `raw_json` json DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_meta_rt_campaign_snapshot` (`business_date`,`snapshot_at`,`ad_account_id`,`campaign_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
""")

execute("""
CREATE TABLE IF NOT EXISTS `meta_ad_realtime_daily_adset_metrics` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `import_run_id` bigint NOT NULL,
  `business_date` date NOT NULL,
  `snapshot_at` datetime NOT NULL,
  `data_completeness` varchar(32) NOT NULL DEFAULT 'realtime_partial',
  `ad_account_id` varchar(32) DEFAULT NULL,
  `ad_account_name` varchar(128) DEFAULT NULL,
  `campaign_id` varchar(64) DEFAULT NULL,
  `campaign_name` varchar(255) DEFAULT NULL,
  `normalized_campaign_code` varchar(255) DEFAULT NULL,
  `adset_id` varchar(64) DEFAULT NULL,
  `adset_name` varchar(512) NOT NULL,
  `normalized_adset_code` varchar(512) NOT NULL,
  `product_code` varchar(255) DEFAULT NULL,
  `country_label` varchar(64) DEFAULT NULL,
  `country_code` varchar(16) DEFAULT NULL,
  `material_name` varchar(512) DEFAULT NULL,
  `result_count` int NOT NULL DEFAULT '0',
  `spend_usd` decimal(14,4) NOT NULL DEFAULT '0.0000',
  `purchase_value_usd` decimal(14,4) NOT NULL DEFAULT '0.0000',
  `impressions` int NOT NULL DEFAULT '0',
  `clicks` int NOT NULL DEFAULT '0',
  `raw_json` json DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_meta_rt_adset_snapshot` (`business_date`,`snapshot_at`,`ad_account_id`,`adset_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
""")

execute("""
CREATE TABLE IF NOT EXISTS `meta_ad_realtime_daily_ad_metrics` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `import_run_id` bigint NOT NULL,
  `business_date` date NOT NULL,
  `snapshot_at` datetime NOT NULL,
  `data_completeness` varchar(32) NOT NULL DEFAULT 'realtime_partial',
  `ad_account_id` varchar(32) DEFAULT NULL,
  `ad_account_name` varchar(128) DEFAULT NULL,
  `campaign_id` varchar(64) DEFAULT NULL,
  `campaign_name` varchar(255) DEFAULT NULL,
  `normalized_campaign_code` varchar(255) DEFAULT NULL,
  `adset_id` varchar(64) DEFAULT NULL,
  `adset_name` varchar(512) DEFAULT NULL,
  `normalized_adset_code` varchar(512) DEFAULT NULL,
  `ad_id` varchar(64) DEFAULT NULL,
  `ad_name` varchar(512) NOT NULL,
  `normalized_ad_code` varchar(512) NOT NULL,
  `product_code` varchar(255) DEFAULT NULL,
  `country_label` varchar(64) DEFAULT NULL,
  `country_code` varchar(16) DEFAULT NULL,
  `material_name` varchar(512) DEFAULT NULL,
  `result_count` int NOT NULL DEFAULT '0',
  `spend_usd` decimal(14,4) NOT NULL DEFAULT '0.0000',
  `purchase_value_usd` decimal(14,4) NOT NULL DEFAULT '0.0000',
  `impressions` int NOT NULL DEFAULT '0',
  `clicks` int NOT NULL DEFAULT '0',
  `raw_json` json DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_meta_rt_ad_snapshot` (`business_date`,`snapshot_at`,`ad_account_id`,`ad_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
""")

# 2. Clean existing test realtime metrics for PID 316
execute("DELETE FROM meta_ad_realtime_daily_campaign_metrics WHERE campaign_name = 'sonic-lens-refresher-rjc'")
execute("DELETE FROM meta_ad_realtime_daily_ad_metrics WHERE campaign_name = 'sonic-lens-refresher-rjc'")

# 3. Find or insert media_item for German
items = query("SELECT id, filename FROM media_items WHERE product_id = %s AND lang = %s AND deleted_at IS NULL", (PID, LANG))
if not items:
    # insert a dummy media item
    execute("""
        INSERT INTO media_items (product_id, lang, filename, display_name, user_id) 
        VALUES (%s, %s, '2026.04.15-隐形眼镜清洗器-原素材-补充素材(德语)-指派-蔡靖华.mp4', '2026.04.15-隐形眼镜清洗器-原素材-补充素材(德语)-指派-蔡靖华.mp4', 1)
    """)
    items = query("SELECT id, filename FROM media_items WHERE product_id = %s AND lang = %s AND deleted_at IS NULL", (PID, LANG))

print(f"Media item: {items[0]}")

# 4. Insert realtime ad metric with NULL country_code (tests SQL CASE ad_name parsing fallback)
# The ad_name contains "德国" which maps to "de".
execute("""
    INSERT INTO meta_ad_realtime_daily_ad_metrics 
    (import_run_id, business_date, snapshot_at, data_completeness, ad_account_id, ad_account_name, 
     campaign_id, campaign_name, normalized_campaign_code, adset_id, adset_name, normalized_adset_code, 
     ad_id, ad_name, normalized_ad_code, country_code, spend_usd, purchase_value_usd, impressions, clicks, raw_json)
    VALUES (999, CURDATE(), NOW(), 'realtime_partial', '12345', 'test_acc', 
            'camp123', 'sonic-lens-refresher-rjc', 'sonic-lens-refresher-rjc', 'set123', 'adset_test', 'adset_test',
            'ad123', 'sonic-lens-refresher-(2026.04.15-SonicLensRefresher-原素材-补充素材(德语)-指派-蔡.mp4)德国(05.27)-AP', 'ad123',
            NULL, 25.50, 50.00, 1000, 50, '{}')
""")

# We also need a campaign level realtime metric for overall product status to match campaign name
execute("""
    INSERT INTO meta_ad_realtime_daily_campaign_metrics
    (import_run_id, business_date, snapshot_at, data_completeness, ad_account_id, ad_account_name,
     campaign_id, campaign_name, normalized_campaign_code, result_count, spend_usd, purchase_value_usd,
     impressions, clicks, raw_json)
    VALUES (999, CURDATE(), NOW(), 'realtime_partial', '12345', 'test_acc',
            'camp123', 'sonic-lens-refresher-rjc', 'sonic-lens-refresher-rjc', 50, 25.50, 50.00, 1000, 50, '{}')
""")

print("--- Running Refresh ---")
print(refresh_all())

print("--- Checking Cache Results ---")
cache = query("SELECT * FROM media_product_ad_summary_cache WHERE product_id = %s", (PID,))
print(f"Product Overall Cache: {cache}")

lang_cache = query("SELECT * FROM media_product_lang_ad_summary_cache WHERE product_id = %s AND lang = %s", (PID, LANG))
print(f"Language Cache (German): {lang_cache}")

# Now let's test the second case: country_code is populated
execute("DELETE FROM meta_ad_realtime_daily_campaign_metrics WHERE campaign_name = 'sonic-lens-refresher-rjc'")
execute("DELETE FROM meta_ad_realtime_daily_ad_metrics WHERE campaign_name = 'sonic-lens-refresher-rjc'")

execute("""
    INSERT INTO meta_ad_realtime_daily_ad_metrics 
    (import_run_id, business_date, snapshot_at, data_completeness, ad_account_id, ad_account_name, 
     campaign_id, campaign_name, normalized_campaign_code, adset_id, adset_name, normalized_adset_code, 
     ad_id, ad_name, normalized_ad_code, country_code, spend_usd, purchase_value_usd, impressions, clicks, raw_json)
    VALUES (999, CURDATE(), NOW(), 'realtime_partial', '12345', 'test_acc', 
            'camp123', 'sonic-lens-refresher-rjc', 'sonic-lens-refresher-rjc', 'set123', 'adset_test', 'adset_test',
            'ad123', 'sonic-lens-refresher-(2026.04.15-SonicLensRefresher-原素材-补充素材(德语)-指派-蔡.mp4)德国(05.27)-AP', 'ad123',
            'DE', 15.20, 30.00, 1000, 50, '{}')
""")

execute("""
    INSERT INTO meta_ad_realtime_daily_campaign_metrics
    (import_run_id, business_date, snapshot_at, data_completeness, ad_account_id, ad_account_name,
     campaign_id, campaign_name, normalized_campaign_code, result_count, spend_usd, purchase_value_usd,
     impressions, clicks, raw_json)
    VALUES (999, CURDATE(), NOW(), 'realtime_partial', '12345', 'test_acc',
            'camp123', 'sonic-lens-refresher-rjc', 'sonic-lens-refresher-rjc', 50, 15.20, 30.00, 1000, 50, '{}')
""")

print("--- Running Refresh 2 (with country_code='DE') ---")
print(refresh_all())

print("--- Checking Cache Results 2 ---")
cache = query("SELECT * FROM media_product_ad_summary_cache WHERE product_id = %s", (PID,))
print(f"Product Overall Cache: {cache}")

lang_cache = query("SELECT * FROM media_product_lang_ad_summary_cache WHERE product_id = %s AND lang = %s", (PID, LANG))
print(f"Language Cache (German): {lang_cache}")

# Clean up test data
execute("DROP TABLE IF EXISTS meta_ad_realtime_daily_campaign_metrics")
execute("DROP TABLE IF EXISTS meta_ad_realtime_daily_adset_metrics")
execute("DROP TABLE IF EXISTS meta_ad_realtime_daily_ad_metrics")
print("Test completed and cleaned up.")
