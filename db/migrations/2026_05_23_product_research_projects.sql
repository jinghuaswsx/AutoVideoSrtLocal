-- Add project-oriented fields to product_research_runs
ALTER TABLE product_research_runs
  ADD COLUMN display_name VARCHAR(255) NOT NULL DEFAULT '' AFTER research_run_id,
  ADD COLUMN resumed_at DATETIME DEFAULT NULL AFTER failed_at;

-- Ensure product_research_assets is ready for production use
-- (table already exists from 2026_05_22_product_research.sql, this migration
-- just verifies it will be populated going forward)
