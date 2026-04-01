# ElevenLabs Voice Library Import Design

Date: 2026-04-01
Status: Draft

## Overview

Add a first-party import flow that lets AutoVideoSrt accept either:

- a raw ElevenLabs `voiceId`
- or a full ElevenLabs Voice Library URL containing `voiceId`

The system should resolve that Voice Library voice through the ElevenLabs API, optionally save it into the account's ElevenLabs "My Voices", then register it in AutoVideoSrt's local voice catalog so it becomes selectable everywhere the existing `/api/voices` list is used.

This first version should **not** build a full in-app ElevenLabs search UI. It should focus on a reliable import-by-ID/link workflow that fits the existing voice architecture with minimal disruption.

---

## Goals

- Let admins or operators import an ElevenLabs Voice Library voice by `voiceId` or shared link.
- Keep the existing task page voice selector unchanged: it should continue reading from `/api/voices`.
- Preserve the current TTS generation path, which already uses `voice["elevenlabs_voice_id"]`.
- Make imported voices durable in the local catalog (`voices.json`) so they survive restarts and are visible in the current CRUD UI.
- Expose enough metadata from ElevenLabs to make imported voices understandable in our UI.

---

## Non-Goals

- No full Voice Library browser inside AutoVideoSrt in v1.
- No voice preview playback integration in our UI for v1.
- No per-user isolated voice catalog in v1; reuse the current shared voice library model.
- No replacement of the existing manual voice CRUD screen; import is an addition, not a rewrite.

---

## Current System

Today the voice system works like this:

1. The frontend task workbench requests `/api/voices`
2. `/api/voices` returns entries from `voices/voices.json`
3. Each entry includes:
   - `id`
   - `name`
   - `gender`
   - `elevenlabs_voice_id`
   - description/default flags/tags
4. TTS generation later passes `voice["elevenlabs_voice_id"]` into ElevenLabs text-to-speech

This means the missing capability is not TTS itself. The missing capability is importing Voice Library voices into our local catalog.

---

## Official ElevenLabs Capabilities Relevant To This Feature

### `GET /v1/shared-voices`

Used to search Voice Library voices. This endpoint supports resolving a voice by `voice_id` and returning share metadata and descriptive fields.

Use in our system:

- resolve a pasted `voiceId`
- verify the voice exists and is shareable
- obtain metadata such as name, labels, description, language, gender-like hints, preview URL, and sharing owner information

### `POST /v1/voices/add/:public_user_id/:voice_id`

Used to add a shared voice to the current ElevenLabs account's "My Voices".

Use in our system:

- ensure imported community/shared voices become available in the connected ElevenLabs account
- improve consistency with the My Voices model and avoid relying only on transient library discovery

### `GET /v2/voices`

Used to list voices available to the current ElevenLabs account.

Use in our system:

- verify the imported voice is present after add
- optionally refresh details from the account-visible view instead of relying only on shared-library response payloads

### `GET /v1/voices/:voice_id`

Used to fetch a single voice's details.

Use in our system:

- normalize the imported voice payload before storing it locally
- fetch the latest voice name/description/settings-compatible details

### Constraints

- ElevenLabs documents that Voice Library API access is not available to free tier users.
- Some voices may disappear later if the owner stops sharing them; notice-period behavior is controlled by ElevenLabs, not by us.

---

## Recommended Product Shape

### v1 Entry Point

Add a new import action in the existing voice management flow:

- admin pastes either:
  - `zDBYcuJrpuZ6YQ7AgRUw`
  - or `https://elevenlabs.io/app/voice-library?voiceId=zDBYcuJrpuZ6YQ7AgRUw`
- admin optionally edits:
  - display name override
  - gender
  - description
  - tags
  - default flags
- system imports and stores the voice in the local catalog

This keeps the task page unchanged and adds capability exactly where voice administration already lives.

### Why This Is The Best First Step

- Lowest-risk fit with current architecture
- Reuses `voices.json`, `/api/voices`, and current TTS execution
- Avoids building a second, more complex voice search UI before import semantics are stable
- Gives immediate value for specific shared voices users already know they want

---

## Data Flow

### Import Request

Client submits:

```json
{
  "source": "https://elevenlabs.io/app/voice-library?voiceId=zDBYcuJrpuZ6YQ7AgRUw",
  "name": "",
  "gender": "",
  "description": "",
  "style_tags": [],
  "is_default_male": false,
  "is_default_female": false,
  "save_to_elevenlabs": true
}
```

### Backend Resolution

1. Parse `source`
   - if URL: extract `voiceId`
   - if raw string: treat as `voiceId`
2. Call ElevenLabs `GET /v1/shared-voices` with `voice_id`
3. Find the exact matching shared voice
4. Extract:
   - `voice_id`
   - public owner identifier
   - title/name
   - description
   - labels / category / language
   - preview URL if available
5. If `save_to_elevenlabs = true`, call `POST /v1/voices/add/:public_user_id/:voice_id`
6. Optionally call `GET /v1/voices/:voice_id` or `GET /v2/voices` for final normalization
7. Map the result into our local voice schema
8. Persist into `voices.json`

---

## Backend Design

### New Module

Add a dedicated integration module, for example:

`pipeline/elevenlabs_voices.py`

Responsibilities:

- parse ElevenLabs voice links
- call official voice-library/account voice APIs
- normalize shared voice metadata into our local shape
- raise clear domain errors for UI/API consumption

Suggested public functions:

- `extract_voice_id(source: str) -> str`
- `find_shared_voice(voice_id: str, api_key: str | None = None) -> dict`
- `add_shared_voice_to_account(public_user_id: str, voice_id: str, api_key: str | None = None) -> dict`
- `get_account_voice(voice_id: str, api_key: str | None = None) -> dict | None`
- `import_voice_by_id_or_url(source: str, *, api_key: str | None = None, save_to_elevenlabs: bool = True) -> dict`

### API Key Resolution

Reuse the same ElevenLabs key resolution strategy as the runtime:

- current user's configured ElevenLabs key if available
- otherwise fallback to system `ELEVENLABS_API_KEY`

This keeps import behavior aligned with the rest of the app.

### Error Types

Handle these explicitly:

- invalid URL / no `voiceId` found
- shared voice not found
- shared voice exists but API plan does not permit Voice Library access
- add-to-account failed
- imported voice duplicates an existing local `id`
- imported voice duplicates an existing `elevenlabs_voice_id`

---

## Local Voice Schema Changes

Current schema is sufficient for TTS, but import benefits from a few additional optional fields.

Recommended additions:

- `source`: `"manual"` or `"elevenlabs_voice_library"`
- `source_voice_id`: original ElevenLabs `voice_id`
- `source_public_user_id`: ElevenLabs public owner id if available
- `preview_url`: optional ElevenLabs preview audio URL
- `labels`: optional raw label object from ElevenLabs

Example stored voice:

```json
{
  "id": "serena-calm-friendly-warm",
  "name": "Serena - Calm, Friendly, Warm",
  "gender": "female",
  "elevenlabs_voice_id": "zDBYcuJrpuZ6YQ7AgRUw",
  "description": "Imported from ElevenLabs Voice Library",
  "style_tags": ["warm", "friendly", "narration"],
  "is_default_male": false,
  "is_default_female": false,
  "source": "elevenlabs_voice_library",
  "source_voice_id": "zDBYcuJrpuZ6YQ7AgRUw",
  "source_public_user_id": "public_user_xxx",
  "preview_url": "https://...",
  "labels": {
    "accent": "american",
    "category": "narration"
  }
}
```

These fields should remain optional so existing voices continue to work unchanged.

---

## API Surface

### Option A: Dedicated Import Endpoint

Recommended.

Add:

`POST /api/voices/import`

Request body:

```json
{
  "source": "<voiceId or elevenlabs url>",
  "name": "",
  "gender": "",
  "description": "",
  "style_tags": [],
  "is_default_male": false,
  "is_default_female": false,
  "save_to_elevenlabs": true
}
```

Response:

```json
{
  "voice": { ...normalized local voice record... },
  "imported": true
}
```

This keeps plain CRUD (`POST /api/voices`) separate from external-system import.

### Why Not Overload Existing `POST /api/voices`

- current route expects already-normalized local payload
- import requires network I/O, parsing, external error handling, and API-key resolution
- mixing both paths into one route will make the API harder to understand and test

---

## UI Changes

Add one import section to the current voice management page or admin voice control surface:

- input: `voiceId 或 ElevenLabs 链接`
- optional fields:
  - name override
  - gender override
  - tags
  - set as default
- action button: `导入 ElevenLabs 音色`

Recommended behavior:

- auto-fill metadata preview after successful resolution
- if no gender is confidently derivable from ElevenLabs data, require manual choice before final save

The task workbench dropdown does not need UI changes for v1; imported voices will appear automatically once they are stored locally.

---

## Mapping Rules

### `id`

Generate local `id` from the final display name using the existing slug logic.

If slug already exists:

- first check if it points to the same `elevenlabs_voice_id`
  - if yes, treat import as idempotent update
- otherwise append a suffix derived from `voice_id`

### `name`

Prefer:

1. explicit user override
2. ElevenLabs voice name

### `gender`

Prefer:

1. explicit user override
2. mapped label if ElevenLabs response includes reliable gender metadata
3. require manual selection before save

### `description`

Prefer:

1. explicit user override
2. ElevenLabs description
3. fallback string like `Imported from ElevenLabs Voice Library`

### `style_tags`

Compose from:

- explicit user tags
- selected normalized ElevenLabs labels such as category, accent, tone, language

---

## Idempotency And Updates

Import should be idempotent by `elevenlabs_voice_id`.

Behavior:

- if local voice with same `elevenlabs_voice_id` already exists:
  - update metadata
  - preserve local default flags unless explicitly changed
  - preserve local manual overrides when the request explicitly asks to preserve them

This avoids duplicate local entries for the same ElevenLabs voice.

---

## Failure Handling

### User-Facing Errors

- `无法从链接中解析 voiceId`
- `未找到该 ElevenLabs 共享音色`
- `当前 ElevenLabs 账号无权访问 Voice Library API`
- `添加到 ElevenLabs My Voices 失败`
- `本地音色库中已存在不同音色使用同名 ID`

### Recovery

- if add-to-account fails but shared-voice lookup succeeds, return a specific error and do not write partial local data
- if local save fails after add-to-account succeeds, surface an error that the voice may already exist in ElevenLabs but was not added to AutoVideoSrt's local catalog

---

## Testing Strategy

### Unit Tests

- parse raw `voiceId`
- parse full Voice Library URL
- reject invalid URL/source
- normalize shared voice payload into local schema
- idempotent update when `elevenlabs_voice_id` already exists
- duplicate-slug collision handling

### Route Tests

- `POST /api/voices/import` success path
- shared voice not found
- ElevenLabs permission error
- missing gender requiring explicit input

### Integration Tests

Mock ElevenLabs responses for:

- shared-voice lookup
- add-to-account
- get-account-voice

Ensure imported voices appear in `GET /api/voices`.

---

## Rollout Plan

### Phase 1

- backend import module
- dedicated import endpoint
- small admin/import UI
- local catalog persistence

### Phase 2

- optional metadata preview
- preview audio playback using `preview_url`
- refresh/sync from ElevenLabs account voices

### Phase 3

- in-app Voice Library search UI if needed

---

## Open Questions Resolved For v1

- Full search UI: deferred
- Multi-user private voice catalogs: deferred
- Preview playback: deferred
- Import trigger: admin/manual import by `voiceId` or URL

---

## References

- ElevenLabs Voice Library docs: https://elevenlabs.io/docs/eleven-creative/voices/voice-library
- ElevenLabs Voices capability overview: https://elevenlabs.io/docs/overview/capabilities/voices
- ElevenLabs help: finding voice IDs: https://help.elevenlabs.io/hc/en-us/articles/14599760033937-How-do-I-find-the-voice-ID-of-my-voices-via-the-website-and-API
