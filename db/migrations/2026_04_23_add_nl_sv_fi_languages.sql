-- db/migrations/2026_04_23_add_nl_sv_fi_languages.sql
-- Add and enable Dutch, Swedish, and Finnish in the shared medias language table.

INSERT INTO media_languages (code, name_zh, sort_order, enabled)
VALUES
  ('nl', '荷兰语', 8, 1),
  ('sv', '瑞典语', 9, 1),
  ('fi', '芬兰语', 10, 1)
ON DUPLICATE KEY UPDATE
  name_zh = VALUES(name_zh),
  sort_order = VALUES(sort_order),
  enabled = VALUES(enabled);
