-- Newjoyloo Meta 广告户切换（2026-05-07）
--
-- Docs-anchor: docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md
--
-- 用户提供的新 Ads Manager URL：
-- business_id=476723373113063
-- act=1861285821213497
--
-- 旧 newjoyloo 广告户 2110407576446225 已被 Meta 封禁。当前同步必须使用
-- 新账户 1861285821213497；旧账户保留为 disabled 历史账户，供产品盈亏
-- 按 enabled_only=false 映射历史广告费。
SET @meta_ad_accounts_newjoyloo_switch = '[{"code":"newjoyloo","label":"Newjoyloo","account_id":"1861285821213497","business_id":"476723373113063","csv_prefix":"newjoyloo","store_codes":["newjoy"],"enabled":true,"note":"2026-05-07 旧户被封后启用的新广告户"},{"code":"newjoyloo_old","label":"Newjoyloo 旧广告户","account_id":"2110407576446225","business_id":"476723373113063","csv_prefix":"newjoyloo_old","store_codes":["newjoy"],"enabled":false,"note":"2026-05-07 被 Meta 封禁，保留历史广告费分摊"},{"code":"Omurio","label":"Omurio","account_id":"1253003326160754","business_id":"909367947900474","csv_prefix":"Omurio","store_codes":["omurio"],"enabled":true,"note":""}]';

INSERT INTO system_settings (`key`, `value`) VALUES
  ('meta_ad_accounts', @meta_ad_accounts_newjoyloo_switch)
ON DUPLICATE KEY UPDATE `value` = VALUES(`value`);
