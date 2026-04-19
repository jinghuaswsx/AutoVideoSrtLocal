-- LLM 调用统一管理：UseCase × Provider×Model 的全局绑定表
-- 详见 docs/superpowers/plans/2026-04-19-llm-call-unification.md (Task 1)

CREATE TABLE IF NOT EXISTS llm_use_case_bindings (
    use_case_code VARCHAR(64) NOT NULL PRIMARY KEY,
    provider_code VARCHAR(32) NOT NULL,
    model_id      VARCHAR(128) NOT NULL,
    extra_config  JSON,
    enabled       TINYINT(1) NOT NULL DEFAULT 1,
    updated_by    INT NULL,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_provider_model (provider_code, model_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
