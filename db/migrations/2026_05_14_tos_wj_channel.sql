-- Add WJ as a selectable main TOS channel.
--
-- Secrets are intentionally not seeded here. Fill the WJ access key and secret
-- from /settings?tab=infrastructure or by an operator-run DB update.

INSERT IGNORE INTO infra_credentials (code, display_name, group_code, config) VALUES
  ('tos_wj', '495828376@qq.com WJ', 'object_storage', JSON_OBJECT(
    'region', 'cn-shanghai',
    'bucket', 'avs-rjc',
    'endpoint', 'tos-cn-shanghai.volces.com',
    'public_endpoint', 'tos-cn-shanghai.volces.com',
    'private_endpoint', 'tos-cn-shanghai.ivolces.com'
  ));

UPDATE infra_credentials
SET display_name = '3482299@qq.com CJH'
WHERE code = 'tos_main';

INSERT IGNORE INTO system_settings (`key`, `value`) VALUES
  ('infra_credentials.tos_active_channel', 'tos_main');
