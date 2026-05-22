-- 2026_05_22_product_research.sql
-- 单品 AI 产品调研功能：runs / country_results / assets 三表

CREATE TABLE IF NOT EXISTS product_research_runs (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    research_run_id VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    input_snapshot_json TEXT,
    pipeline_cards_json TEXT,
    product_facts_json TEXT,
    media_understanding_json TEXT,
    pricing_strategy_json TEXT,
    summary_json TEXT,
    frontend_json TEXT,
    metadata_json TEXT,
    error_message VARCHAR(1000) DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    started_at DATETIME DEFAULT NULL,
    completed_at DATETIME DEFAULT NULL,
    failed_at DATETIME DEFAULT NULL,
    UNIQUE KEY uk_research_run_id (research_run_id),
    KEY idx_status (status),
    KEY idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS product_research_country_results (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    research_run_id VARCHAR(64) NOT NULL,
    country_code VARCHAR(8) NOT NULL,
    country_name VARCHAR(64) NOT NULL DEFAULT '',
    country_name_zh VARCHAR(64) NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    scores_json TEXT,
    decision_json TEXT,
    competitor_pricing_json TEXT,
    pricing_strategy_json TEXT,
    shipping_strategy_json TEXT,
    short_video_fit_json TEXT,
    main_image_fit_json TEXT,
    landing_page_localization_json TEXT,
    risks_json TEXT,
    recommendations_json TEXT,
    full_result_json TEXT,
    sources_json TEXT,
    raw_response_json TEXT,
    metadata_json TEXT,
    error_message VARCHAR(1000) DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    completed_at DATETIME DEFAULT NULL,
    failed_at DATETIME DEFAULT NULL,
    UNIQUE KEY uk_run_country (research_run_id, country_code),
    KEY idx_run_id (research_run_id),
    KEY idx_country_code (country_code),
    KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS product_research_assets (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    research_run_id VARCHAR(64) NOT NULL,
    asset_id VARCHAR(64) NOT NULL,
    asset_type VARCHAR(32) NOT NULL,
    asset_url VARCHAR(1024) DEFAULT '',
    local_path VARCHAR(1024) DEFAULT '',
    mime_type VARCHAR(128) DEFAULT '',
    upload_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    gemini_file_id VARCHAR(256) DEFAULT '',
    metadata_json TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_asset_id (asset_id),
    KEY idx_run_id (research_run_id),
    KEY idx_asset_type (asset_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;