-- 文案创作模块数据库迁移

-- 1. projects 表新增 type 字段
ALTER TABLE projects ADD COLUMN type VARCHAR(20) NOT NULL DEFAULT 'translation' AFTER user_id;

-- 2. 新增 copywriting_inputs 表
CREATE TABLE IF NOT EXISTS copywriting_inputs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    product_title VARCHAR(255) DEFAULT '',
    product_image_url TEXT,
    price VARCHAR(50) DEFAULT '',
    selling_points TEXT,
    target_audience VARCHAR(255) DEFAULT '',
    extra_info TEXT,
    language VARCHAR(10) NOT NULL DEFAULT 'en',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. user_prompts 表新增 type 字段
ALTER TABLE user_prompts ADD COLUMN type VARCHAR(20) NOT NULL DEFAULT 'translation' AFTER user_id;
