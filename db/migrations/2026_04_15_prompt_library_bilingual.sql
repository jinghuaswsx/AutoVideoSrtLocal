-- db/migrations/2026_04_15_prompt_library_bilingual.sql
-- 提示词典双语化：content → content_zh，新增 content_en，两个都可空（应用层校验至少一个非空）
ALTER TABLE prompt_library
  CHANGE COLUMN content content_zh MEDIUMTEXT NULL,
  ADD COLUMN content_en MEDIUMTEXT NULL AFTER content_zh;
