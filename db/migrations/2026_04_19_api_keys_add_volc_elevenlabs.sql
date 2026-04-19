-- api_keys.service 字段已是 VARCHAR(32)，支持任意 service 字符串（含 volc / elevenlabs / gemini_cloud）。
-- 本文件仅作为 LLM 调用统一重构中"新 service 分类纳管"变更的书面记录。
-- 详见 docs/superpowers/plans/2026-04-19-llm-call-unification.md (Task 1)

SELECT 'api_keys.service accepts arbitrary values (volc/elevenlabs/gemini_cloud); no schema change required.' AS note;
