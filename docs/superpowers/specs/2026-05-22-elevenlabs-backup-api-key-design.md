# ElevenLabs Backup API Key Design

Date: 2026-05-22

## Context

ElevenLabs TTS currently reads `llm_provider_configs.elevenlabs_tts.api_key` for TTS, Scribe ASR, and voice-library sync. When that key is exhausted, operators must replace the single stored key, which makes rollback and planned switching slow.

Provider `extra_config` is visible in the settings UI, so backup secrets must not be stored there.

## Design

- Keep `elevenlabs_tts` as the primary provider row.
- Add `elevenlabs_tts_backup` as a second TTS provider row. Its `api_key` field uses the same masked secret handling as other provider rows.
- Store only a non-secret selector in `elevenlabs_tts.extra_config`: `active_key_slot`, either `primary` or `backup`. Missing or invalid values default to `primary`.
- Add an ElevenLabs-specific resolver that returns the active slot's key and raises a clear `ProviderConfigError` if the selected slot has no configured key.
- Route all ElevenLabs runtime callers through that resolver instead of calling `require_provider_api_key("elevenlabs_tts")` directly.
- Add a settings UI select on the `elevenlabs_tts` row so admins can switch between primary and backup without pasting keys.

## Migration

Add an idempotent migration to seed `elevenlabs_tts_backup` with display name `ElevenLabs 配音（备用 Key）`, group `tts`, and base URL `https://api.elevenlabs.io/v1`.

## Tests

- DAO tests cover default primary selection, backup selection, invalid selector fallback, missing selected backup key, and legacy adapter mapping.
- Settings route tests cover saving the active slot to `extra_config` and keeping the normal secret-preserving behavior.

Full local pytest is not safe in this project because some tests can touch `127.0.0.1:3306`; verification should use targeted no-local-MySQL tests plus server/test-environment checks when deploying.
