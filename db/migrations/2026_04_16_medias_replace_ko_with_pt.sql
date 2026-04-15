-- db/migrations/2026_04_16_medias_replace_ko_with_pt.sql
-- 将素材管理语种从韩语 ko 调整为葡萄牙语 pt

INSERT INTO media_languages (code, name_zh, sort_order, enabled)
VALUES ('pt', '葡萄牙语', 7, 1)
ON DUPLICATE KEY UPDATE
  name_zh = VALUES(name_zh),
  sort_order = VALUES(sort_order),
  enabled = VALUES(enabled);

UPDATE media_languages
SET enabled = 0
WHERE code = 'ko';
