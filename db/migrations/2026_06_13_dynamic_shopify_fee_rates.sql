CREATE TABLE IF NOT EXISTS shopify_fee_rate_snapshots (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    store_code VARCHAR(32) NOT NULL,
    region VARCHAR(16) NOT NULL,
    window_start_date DATE NOT NULL,
    window_end_date DATE NOT NULL,
    window_days INT NOT NULL,
    orders_count INT NOT NULL DEFAULT 0,
    amount_usd DECIMAL(18, 4) NOT NULL DEFAULT 0,
    fee_usd DECIMAL(18, 4) NOT NULL DEFAULT 0,
    effective_rate DECIMAL(12, 8) NOT NULL DEFAULT 0,
    fixed_fee_per_order DECIMAL(10, 4) NOT NULL DEFAULT 0.3000,
    variable_rate DECIMAL(12, 8) NOT NULL DEFAULT 0,
    source_csvs_json JSON NULL,
    sample_status VARCHAR(32) NOT NULL,
    computed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_fee_snapshots_lookup (store_code, region, window_end_date, sample_status),
    KEY idx_fee_snapshots_computed_at (computed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE order_profit_lines
    ADD COLUMN shopify_fee_source VARCHAR(32) NULL,
    ADD COLUMN shopify_fee_rate DECIMAL(12, 8) NULL,
    ADD COLUMN shopify_fee_rate_region VARCHAR(16) NULL,
    ADD COLUMN shopify_fee_rate_window_start DATE NULL,
    ADD COLUMN shopify_fee_rate_window_end DATE NULL,
    ADD COLUMN shopify_fee_basis_json JSON NULL,
    ADD KEY idx_profit_fee_source (shopify_fee_source);

ALTER TABLE shopify_payments_transactions
    ADD COLUMN transaction_date VARCHAR(64) NULL,
    ADD KEY idx_shopify_payments_transaction_date (transaction_date);
