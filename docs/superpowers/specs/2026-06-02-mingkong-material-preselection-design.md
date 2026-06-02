# Mingkong Material Preselection Design

Last updated: 2026-06-02

## Goal

Add a `素材预选` workflow under `/xuanpin/mk` so operations staff can help admins
preselect Mingkong video materials for later import and small-language translation.

The first authorized operations user is `guqian`, but access must be implemented
through the existing permission-management system, not by hard-coding a username in
route or template logic.

## Anchors

- `AGENTS.md`: non-hotfix feature work must be done in an isolated worktree and code
  changes require a documentation anchor first.
- `docs/superpowers/specs/2026-05-18-mingkong-video-material-library-subtabs-design.md`:
  `/xuanpin/mk` already has local Mingkong subtabs and shared video-card rendering.
- `docs/superpowers/specs/2026-05-18-mingkong-daily-material-snapshot-top100-design.md`:
  Mingkong material lists are read from local archived rows, not live Mingkong calls.
- `docs/superpowers/specs/2026-05-20-mingkong-card-material-ad-status-design.md`:
  card APIs already enrich local product/material library status.
- `docs/superpowers/specs/2026-05-20-user-work-scope-translation-design.md`:
  user-specific operational access is stored in `users.permissions` JSON and managed
  through the admin permission modal.

## Scope

In scope:

1. Register a new permission code for material preselection.
2. Grant that permission to the existing `guqian` user through an idempotent migration.
3. Allow users with the new permission to access only the Mingkong material-card views
   needed for preselection.
4. Add a new `素材预选` subtab under `明空选品`.
5. Add a `素材预选` action on eligible Mingkong video cards.
6. Save selected languages and operations notes for each preselected video material.
7. Show selected languages and notes on the preselection card.
8. Let admins import and create small-language translation tasks directly from the
   preselection tab.
9. Default the small-language modal to the preselected languages while still allowing
   admins to edit the selection before creating tasks.
10. Mark a preselection as processed only after an admin successfully creates a
    small-language translation task.

Out of scope:

- Do not let preselection-only users import materials or create translation tasks.
- Do not treat material import as processed.
- Do not hard-code a fixed country count. Use the existing enabled language source.
- Do not add live Mingkong API calls to render the preselection tab.
- Do not connect to Windows local MySQL during verification.

## Permission Model

Add a permission to `appcore/permissions.py`:

| code | group | label | admin default | user default |
|---|---|---|---|---|
| `mk_material_preselection` | `management` | `明空素材预选` | `true` | `false` |

Behavior:

- `superadmin` remains all-true through the existing permission model.
- `admin` has full `mk_selection` behavior and also sees the new preselection tools.
- A normal user with `mk_material_preselection=true` can open `/xuanpin/mk` in a
  restricted mode.
- A normal user without `mk_material_preselection` and without `mk_selection` cannot
  access the page or APIs.
- The admin user-management permission modal must show `明空素材预选`, so future
  operations users can be granted or revoked without code changes.

Initial grant:

- Add an idempotent migration that updates the existing `guqian` row:
  `users.permissions.$.mk_material_preselection = true`.
- The migration must handle `permissions` being `NULL` or invalid JSON.
- The migration must not create a user account.

## Restricted Operator Access

Users with only `mk_material_preselection` can:

- View `明空选品` video material-card data needed for preselection.
- View `视频素材库`.
- View yesterday-spend material cards.
- View `素材预选`.
- Create/update preselection language choices and notes.

Users with only `mk_material_preselection` cannot:

- View or operate the product-library management surface if it exposes admin-only
  controls.
- Trigger material import.
- Create small-language translation tasks.
- Use admin-only Mingkong refresh/sort/import actions.

The backend must enforce this split. Hiding buttons in the template is not sufficient.

## Data Model

Add `mingkong_material_preselections`.

Required columns:

- `id`
- `material_key`: stable SHA-256 key from the existing Mingkong material identity.
- `product_code`
- `mk_product_id`
- `product_name`
- `product_english_name`
- `product_url`
- `product_main_image_url`
- `video_name`
- `video_path`
- `video_cover_url`
- `media_product_id`
- `media_item_id`
- `selected_countries_json`
- `operator_note`
- `source_snapshot_at`
- `created_by`
- `updated_by`
- `processed_by`
- `processed_parent_task_id`
- `processed_at`
- `created_at`
- `updated_at`

Indexes:

- Unique key on `material_key`.
- Index on `processed_at`.
- Index on `updated_at`.
- Index on `media_item_id`.
- Index on `product_code`.

Storage rules:

- `selected_countries_json` stores normalized language/country codes from the existing
  small-language configuration.
- At least one language must be selected.
- `operator_note` is optional text. The service should trim it and cap length to a
  bounded value.
- Product/video display fields are copied into the preselection row so the tab can
  render a stable card even if the source archive changes.
- Card-list responses should still enrich current local material status from the
  existing Mingkong material status pipeline when possible.

## API

Add focused APIs under `/xuanpin/api`.

`GET /xuanpin/api/mk-material-preselections`

- Permissions: `mk_selection` or `mk_material_preselection`.
- Filters:
  - `library_status`: `all`, `imported`, `not_imported`.
  - `processed_status`: `all`, `processed`, `unprocessed`.
  - `keyword`.
  - `page`, `page_size`.
- Response includes card-ready rows plus preselection fields.

`POST /xuanpin/api/mk-material-preselections`

- Permissions: `mk_selection` or `mk_material_preselection`.
- Upserts a preselection by `material_key`.
- Requires at least one selected language.
- Saves the latest note.
- Captures the source card metadata needed for stable rendering.

`POST /xuanpin/api/mk-material-preselections/<material_key>/processed`

- Permissions: `mk_selection` only.
- Called after a small-language parent task is successfully created.
- Sets `processed_at`, `processed_by`, and `processed_parent_task_id`.
- Material import must not call this endpoint.

Existing Mingkong material-card APIs:

- `mk_selection` users keep full access.
- `mk_material_preselection` users may read the card-list APIs needed for preselection.
- Mutating admin APIs remain admin-only.

## UI

Under `明空选品`, add a subtab:

- `素材预选`

Video-card action:

- On cards that represent already archived Mingkong video material, render a
  `素材预选` button beside the `昨日消耗` summary area.
- Clicking opens a preselection modal.

Preselection modal:

- Top: product basic info, including Chinese name, English name, product code, and
  main image.
- Middle: language/country capsule buttons from the existing enabled small-language
  source. AI evaluation hints are shown next to the language choices when available.
- Bottom: operations note textarea.
- Save requires at least one selected language.

Preselection tab card:

- Show selected language pills at the top of the card.
- Show the operations note in a compact note area.
- Show local material status and processed status.
- Admins see import and small-language task actions.
- Preselection-only users see no admin actions.

Admin follow-up flows:

- When an admin imports a preselected material, show the operations note at the top of
  the import/progress flow.
- When an admin creates a small-language translation task from a preselected material,
  show the operations note at the top of the task modal.
- The task modal defaults to the preselected languages.
- Admins may uncheck or change languages before creating the task.
- After successful task creation, the frontend calls the processed endpoint.

## Processing Semantics

`processed` means:

- A small-language parent translation task was successfully created from the
  preselected material.

`processed` does not mean:

- The material was imported into the local material library.
- A user merely opened the task modal.
- A user saved or edited the preselection.

## Tests

Focused automated checks:

- Permission tests:
  - `mk_material_preselection` is registered.
  - Admin default is true.
  - User default is false.
  - The permission appears in grouped permissions.
  - A user with stored `mk_material_preselection=true` has the permission.
- Migration/schema tests:
  - `mingkong_material_preselections` exists with required columns and indexes.
  - The `guqian` grant migration sets the permission idempotently.
- Service tests:
  - Upsert requires at least one selected language.
  - Language codes are normalized/deduplicated.
  - Notes are trimmed and persisted.
  - Listing filters imported/unimported and processed/unprocessed.
  - Import alone does not mark processed.
  - Task creation marker marks processed.
- Route tests:
  - Preselection-only users can open restricted `/xuanpin/mk`.
  - Preselection-only users can read allowed material-card APIs.
  - Preselection-only users can save preselection data.
  - Preselection-only users cannot import or create translation tasks.
  - Admins can mark processed.
- Template tests:
  - The `素材预选` tab exists.
  - Video cards have a `素材预选` action near yesterday spend.
  - Preselected language pills and note areas are rendered.
  - Admin task modal has default-language and operations-note wiring.

Verification must avoid any command that connects to Windows `127.0.0.1:3306`.

## Self Review

- Permission is managed through `users.permissions`; `guqian` is only an initial data
  grant, not a route-level special case.
- Admin-only operations remain backend-enforced.
- Processed semantics match the user requirement: only translation-task creation
  counts as processed.
- Language choices are sourced from the existing task-language configuration rather
  than hard-coded to eight countries.
- Notes are shown both on preselection cards and before admin import/task actions.
