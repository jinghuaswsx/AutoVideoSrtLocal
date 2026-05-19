# Tabcut Mark Status Design

## Scope

Tabcut video and goods rows need the same local annotation states used by Meta hot posts: empty, ok, and bad. The page should let operators set or clear ok/bad directly on each card or row, and the toolbar should filter by those three states.

## Data Model

Store annotations on the stable entity tables:

- `tabcut_videos` for video cards, keyed by `video_id`.
- `tabcut_goods` for goods rows, keyed by `item_id`.

Each table gets `is_marked`, `mark_status`, `marked_at`, and `marked_by`, matching the Meta hot posts convention. `mark_status` is `ok`, `bad`, or `NULL`; `is_marked` is a compatibility boolean derived from whether a status is present.

## API

Add POST endpoints for admins:

- `/medias/api/tabcut-selection/videos/<video_id>/mark`
- `/medias/api/tabcut-selection/goods/<item_id>/mark`
- `/xuanpin/api/tabcut/videos/<video_id>/mark`
- `/xuanpin/api/tabcut/goods/<item_id>/mark`

Payloads use `{ "mark_status": "ok" }`, `{ "mark_status": "bad" }`, or `{ "mark_status": null }`.

## UI

Add a `标注` select to the Tabcut toolbar with `全部`, `空`, `行`, and `不行`. Video cards and goods rows render two compact checkbox-style buttons: `行` and `不行`. Clicking the active option clears it.

