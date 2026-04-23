-- Image text detection billing seed.
-- The same Gemini Flash-Lite model is still token-priced for text calls, and
-- also gets a flat per-image placeholder price for image text detection.

INSERT INTO ai_model_prices (
  provider,
  model,
  units_type,
  unit_input_cny,
  unit_output_cny,
  unit_flat_cny,
  note
)
VALUES
  (
    'gemini_vertex',
    'gemini-3.1-flash-lite-preview',
    'tokens',
    0.00000816,
    0.00003264,
    0.00500000,
    'Review needed: image text detection default 0.005 CNY/image; editable in AI pricing'
  ),
  (
    'gemini_aistudio',
    'gemini-3.1-flash-lite-preview',
    'tokens',
    0.00000816,
    0.00003264,
    0.00500000,
    'Review needed: image text detection default 0.005 CNY/image; editable in AI pricing'
  ),
  (
    'openrouter',
    'gemini-3.1-flash-lite-preview',
    'tokens',
    NULL,
    NULL,
    0.00500000,
    'Review needed: OpenRouter image text detection fallback; response cost wins'
  ),
  (
    'openrouter',
    'google/gemini-3.1-flash-lite-preview',
    'tokens',
    NULL,
    NULL,
    0.00500000,
    'Review needed: OpenRouter image text detection fallback; response cost wins'
  ),
  (
    'openrouter',
    '*',
    'tokens',
    NULL,
    NULL,
    0.00500000,
    'Review needed: OpenRouter image text detection fallback; response cost wins'
  )
ON DUPLICATE KEY UPDATE
  unit_input_cny = COALESCE(unit_input_cny, VALUES(unit_input_cny)),
  unit_output_cny = COALESCE(unit_output_cny, VALUES(unit_output_cny)),
  unit_flat_cny = COALESCE(unit_flat_cny, VALUES(unit_flat_cny)),
  note = COALESCE(note, VALUES(note));
