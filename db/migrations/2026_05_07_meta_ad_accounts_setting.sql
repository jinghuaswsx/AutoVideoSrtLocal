-- Meta 广告多账户配置（system_settings.meta_ad_accounts）
--
-- 详细设计：docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md
--
-- 写入逻辑：INSERT IGNORE。如果 key 已经存在（比如线上已经手动改过），
-- 不要覆盖现有 JSON。需要更新种子时显式 UPDATE。
--
-- 种子数据：
--   newjoyloo  enabled=false  2026-05-07 被 Meta 封禁
--   Omurio     enabled=true   正常投放
--
-- 解封后只需把 newjoyloo 那个对象的 enabled 改回 true：
--   UPDATE system_settings
--   SET value = JSON_REPLACE(
--     value,
--     '$[0].enabled', CAST('true' AS JSON))
--   WHERE `key` = 'meta_ad_accounts';
INSERT IGNORE INTO system_settings (`key`, `value`) VALUES
  (
    'meta_ad_accounts',
    '[{"code":"newjoyloo","label":"Newjoyloo","account_id":"2110407576446225","business_id":"476723373113063","csv_prefix":"newjoyloo","enabled":false,"note":"2026-05-07 被 Meta 封禁，等待恢复"},{"code":"Omurio","label":"Omurio","account_id":"1253003326160754","business_id":"909367947900474","csv_prefix":"Omurio","enabled":true,"note":""}]'
  );
