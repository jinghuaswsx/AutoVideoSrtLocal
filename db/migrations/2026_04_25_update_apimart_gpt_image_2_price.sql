-- 修正 apimart gpt-image-2 实测单价：$0.006/image（1K 分辨率），
-- 汇率按 config.USD_TO_CNY=6.8 换算 → ¥0.0408/image，统一按此结算。
-- 依赖 2026_04_25_model_prices_refresh.sql，靠 `update_` 前缀保证排序在其后。
INSERT INTO ai_model_prices (provider, model, units_type, unit_flat_cny, note)
VALUES ('apimart', 'gpt-image-2', 'images', 0.04080000,
        'APIMART GPT-Image-2 实测 $0.006/image（1K），汇率 6.8，统一按此结算')
ON DUPLICATE KEY UPDATE
    units_type    = VALUES(units_type),
    unit_flat_cny = VALUES(unit_flat_cny),
    note          = VALUES(note),
    updated_at    = CURRENT_TIMESTAMP;
