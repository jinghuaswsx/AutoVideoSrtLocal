-- Meta 广告多账户配置（system_settings.meta_ad_accounts）
--
-- 详细设计：docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md
--
-- 写入逻辑：只做首次种子。后续线上切户见
-- db/migrations/2026_05_07_newjoyloo_meta_ad_account_switch.sql。
--
-- 种子数据：
--   newjoyloo      store_codes=["newjoy"]  enabled=true   2026-05-07 旧户被封后启用的新广告户
--   newjoyloo_old  store_codes=["newjoy"]  enabled=false  2026-05-07 被 Meta 封禁，保留历史分摊
--   Omurio         store_codes=["omurio"]  enabled=true   正常投放
--
-- 若旧户未来恢复，应新增或启用独立 code，不要覆盖当前 newjoyloo 新户。
INSERT IGNORE INTO system_settings (`key`, `value`) VALUES
  (
    'meta_ad_accounts',
    '[{"code":"newjoyloo","label":"Newjoyloo","account_id":"1861285821213497","business_id":"476723373113063","csv_prefix":"newjoyloo","store_codes":["newjoy"],"enabled":true,"note":"2026-05-07 旧户被封后启用的新广告户"},{"code":"newjoyloo_old","label":"Newjoyloo 旧广告户","account_id":"2110407576446225","business_id":"476723373113063","csv_prefix":"newjoyloo_old","store_codes":["newjoy"],"enabled":false,"note":"2026-05-07 被 Meta 封禁，保留历史广告费分摊"},{"code":"Omurio","label":"Omurio","account_id":"1253003326160754","business_id":"909367947900474","csv_prefix":"Omurio","store_codes":["omurio"],"enabled":true,"note":""}]'
  );
