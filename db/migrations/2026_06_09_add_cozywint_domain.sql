INSERT INTO media_link_domains (domain, enabled, sort_order)
VALUES ('cozywint.com', 1, 30)
ON DUPLICATE KEY UPDATE enabled = 1;
