-- db/migrations/2026_04_25_user_permissions.sql
-- 三级角色 + 菜单级权限矩阵
--
-- 变更：
--   1. 把 users.role 从 ENUM('admin','user') 改为 VARCHAR(16)，新增 'superadmin' 角色
--   2. 新增 users.permissions JSON 列，存储菜单级权限覆盖（NULL 表示沿用角色默认模板）
--   3. 把用户名为 'admin' 的账号 role 升级为 'superadmin'
--
-- permissions 列允许 NULL：缺失时由应用层 appcore.permissions.merge_with_defaults
-- 根据 role 动态返回默认模板，免除一次性回填 SQL 的复杂度。

ALTER TABLE users
    MODIFY COLUMN role VARCHAR(16) NOT NULL DEFAULT 'user',
    ADD COLUMN permissions JSON DEFAULT NULL;

UPDATE users SET role = 'superadmin' WHERE username = 'admin';
