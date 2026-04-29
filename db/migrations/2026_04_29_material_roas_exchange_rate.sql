-- 2026-04-29: 素材管理 ROAS 默认人民币兑美元汇率

INSERT IGNORE INTO system_settings (`key`, `value`)
VALUES ('material_roas_rmb_per_usd', '6.83');
