-- Add explicit "default domain" flag to media_link_domains.
-- Spec: docs/superpowers/specs/2026-05-09-product-link-default-domain.md

ALTER TABLE media_link_domains
  ADD COLUMN is_default TINYINT(1) NOT NULL DEFAULT 0 AFTER enabled;

-- Backfill: when no row has been flagged default yet, promote the first row
-- (lowest sort_order, ties broken by id) so existing installations keep
-- a single canonical default that downstream code can rely on.
-- Both subqueries are wrapped in derived tables to avoid MySQL's
-- "can't specify target table for update in FROM clause" restriction.
UPDATE media_link_domains AS target
  JOIN (
    SELECT id FROM media_link_domains
     ORDER BY sort_order ASC, id ASC
     LIMIT 1
  ) AS first_row ON first_row.id = target.id
   SET target.is_default = 1
 WHERE NOT EXISTS (
       SELECT 1 FROM (
         SELECT id FROM media_link_domains WHERE is_default = 1 LIMIT 1
       ) AS guard
     );
