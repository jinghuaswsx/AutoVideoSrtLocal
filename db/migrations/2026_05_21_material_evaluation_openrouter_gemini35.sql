-- Docs-anchor: docs/superpowers/specs/2026-05-21-xuanpin-product-ai-evaluation-design.md

INSERT INTO llm_use_case_bindings (
    use_case_code,
    provider_code,
    model_id,
    extra_config,
    enabled,
    updated_by
) VALUES (
    'material_evaluation.evaluate',
    'openrouter',
    'google/gemini-3.5-flash',
    NULL,
    1,
    NULL
) ON DUPLICATE KEY UPDATE
    provider_code = VALUES(provider_code),
    model_id = VALUES(model_id),
    enabled = VALUES(enabled);
