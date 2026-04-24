-- 为 usage_logs 添加懒加载的请求/响应数据存储表
-- 独立表设计：避免主表行体积膨胀；不加 FK 约束，避免级联扫描

CREATE TABLE IF NOT EXISTS usage_log_payloads (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    log_id        BIGINT NOT NULL COMMENT 'usage_logs.id',
    request_data  JSON,
    response_data JSON,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_log_id (log_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
