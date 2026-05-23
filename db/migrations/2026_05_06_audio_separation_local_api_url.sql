-- 切换多语种翻译人声分离 API 到本机 AudioSeparator 服务。
--
-- 新服务直接监听 http://127.0.0.1:83：
--   GET  /health
--   POST /separate/download
--
-- 只迁移缺省空值和旧内网 GPU 网关地址，避免覆盖管理员手工配置的其它地址。
INSERT IGNORE INTO system_settings (`key`, `value`) VALUES
  ('audio_separation_api_url', 'http://127.0.0.1:83');

UPDATE system_settings
SET `value` = 'http://127.0.0.1:83'
WHERE `key` = 'audio_separation_api_url'
  AND TRIM(TRAILING '/' FROM TRIM(`value`)) IN (
    '',
    'http://172.30.254.12',
    'http://172.30.254.12/separate'
  );
