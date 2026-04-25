SET @media_languages_shopify_name_sql = IF(
  (
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'media_languages'
      AND column_name = 'shopify_language_name'
  ) = 0,
  'ALTER TABLE media_languages ADD COLUMN shopify_language_name VARCHAR(80) NOT NULL DEFAULT '''' AFTER name_zh',
  'SELECT 1'
);
PREPARE media_languages_shopify_name_stmt FROM @media_languages_shopify_name_sql;
EXECUTE media_languages_shopify_name_stmt;
DEALLOCATE PREPARE media_languages_shopify_name_stmt;

UPDATE media_languages
SET shopify_language_name = CASE
  WHEN code = 'en' THEN 'English'
  WHEN code = 'de' THEN 'German'
  WHEN code = 'fr' THEN 'French'
  WHEN code = 'it' THEN 'Italian'
  WHEN code = 'es' THEN 'Spanish'
  WHEN code = 'ja' THEN 'Japanese'
  WHEN code = 'pt' THEN 'Portuguese'
  WHEN code = 'nl' THEN 'Dutch'
  WHEN code = 'sv' THEN 'Swedish'
  WHEN code = 'fi' THEN 'Finnish'
  ELSE shopify_language_name
END
WHERE code IN ('en', 'de', 'fr', 'it', 'es', 'ja', 'pt', 'nl', 'sv', 'fi')
  AND shopify_language_name = '';
