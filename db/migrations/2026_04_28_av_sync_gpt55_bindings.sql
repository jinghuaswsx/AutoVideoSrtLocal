INSERT INTO llm_use_case_bindings (
    use_case_code,
    provider_code,
    model_id,
    extra_config,
    enabled,
    updated_by
) VALUES (
    'video_translate.av_localize',
    'openrouter',
    'openai/gpt-5.5',
    NULL,
    1,
    NULL
) ON DUPLICATE KEY UPDATE
    model_id = IF(
        provider_code = 'openrouter'
        AND model_id = 'anthropic/claude-sonnet-4.6',
        VALUES(model_id),
        model_id
    );

INSERT INTO llm_use_case_bindings (
    use_case_code,
    provider_code,
    model_id,
    extra_config,
    enabled,
    updated_by
) VALUES (
    'video_translate.av_rewrite',
    'openrouter',
    'openai/gpt-5.5',
    NULL,
    1,
    NULL
) ON DUPLICATE KEY UPDATE
    model_id = IF(
        provider_code = 'openrouter'
        AND model_id = 'anthropic/claude-sonnet-4.6',
        VALUES(model_id),
        model_id
    );
