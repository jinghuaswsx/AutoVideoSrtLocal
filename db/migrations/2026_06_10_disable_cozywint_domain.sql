-- Disable cozywint.com domain globally for all products
UPDATE media_link_domains SET enabled = 0 WHERE domain = 'cozywint.com';
