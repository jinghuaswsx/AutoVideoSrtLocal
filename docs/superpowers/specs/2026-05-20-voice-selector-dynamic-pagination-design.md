# Voice Selector Dynamic Pagination

Date: 2026-05-20

## Anchor

- Extends `docs/superpowers/specs/2026-05-18-english-redub-speed-aware-voice-match-design.md#详情页`.
- Applies to the shared voice selector used by English redub, multi-translate, omni-translate, ja-translate, and the legacy task detail voice selector.

## Problem

English voice libraries can contain thousands of rows. Loading the entire library on detail page boot creates large JSON downloads and thousands of DOM/audio controls, which can freeze the browser before the user can operate the task page.

## Behavior

- The shared voice selector loads 30 voices on the first request.
- The selector must not auto-fetch every page during initial boot.
- If voice-match candidates are ready, candidate voices are merged into the visible item set and rendered at the top even when they are outside the first 30 rows.
- More voices are fetched only when the user scrolls near the end of the visible voice list or modal voice list.
- The voice selection modal must not pre-render the full voice row set while hidden. When opened, it renders only the currently loaded 30-row page plus merged candidates; scrolling near the bottom of the modal fetches the next page.
- Search and gender filters reset pagination and start again from page 1 with the same 30-row page size.
- The same behavior is required for:
  - `/api/english-redub/<task_id>/voice-library`
  - `/api/multi-translate/<task_id>/voice-library`
  - `/api/omni-translate/<task_id>/voice-library`
  - `/api/ja-translate/<task_id>/voice-library`
  - `/api/tasks/<task_id>/voice-library`

## Verification

- Unit/static tests assert that the frontend uses `VOICE_PAGE_SIZE = 30` and no longer contains a full-library pagination loop.
- Route tests assert that English redub, multi-translate, and omni-translate voice-library endpoints accept `page` and `page_size`.
- Manual verification on a large English library should show only the first page request during initial boot, with later pages requested only after scrolling.
