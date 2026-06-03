CREATE TABLE IF NOT EXISTS product_name_dictionary (
    product_code VARCHAR(128) NOT NULL PRIMARY KEY,
    product_cn_name VARCHAR(500) DEFAULT NULL,
    product_en_name VARCHAR(500) DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Product code to Chinese and English name mapping dictionary';
