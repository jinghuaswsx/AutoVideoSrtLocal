-- 创建服务器健康巡查记录存档表
CREATE TABLE IF NOT EXISTS server_health_records (
    id INT AUTO_INCREMENT PRIMARY KEY,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) NOT NULL,
    system_load VARCHAR(100),
    cpu_usage TEXT,
    memory_usage TEXT,
    gpu_usage TEXT,
    disk_usage TEXT,
    issues TEXT,
    suggestions TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
