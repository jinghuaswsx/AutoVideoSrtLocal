# Translation Admin User Filter Design

- Date: 2026-05-18
- Modules: multi-translate, omni-translate
- Anchor docs:
  - `AGENTS.md#文档驱动代码`
  - `AGENTS.md#Verification`
  - `docs/p1p2-acceptance-2026-05-07-multi-translate-route.md`
  - `docs/p1p2-acceptance-2026-05-07-omni-translate-route.md`
  - `docs/superpowers/specs/2026-04-26-push-task-stats-design.md#用户维度统计`

## Goal

On the `多语种视频翻译` and `全能视频翻译` list pages, let the superadmin filter the visible task list by creator. The filter option labels must prefer the user's Chinese name and fall back to username when the Chinese name is empty or unavailable.

## Current State

Both list pages already load projects through `appcore.translation_route_store.list_projects_with_creator()` and join `users u ON u.id = p.user_id`.

Creator labels already use `medias._media_product_owner_name_expr()`, which returns `COALESCE(NULLIF(TRIM(u.xingming), ''), u.username)` when the `users.xingming` column exists, and `u.username` otherwise.

Access scope is superadmin-only for all-user project visibility. Normal admins and normal users see only their own projects even if a crafted query string includes another `user_id`.

## Design

Add a `user_id` query parameter to both list routes.

Behavior:

- Superadmin sees a compact user select above the retention notice, next to the existing language pills.
- The first option is `全部用户`.
- Other options are users who have active projects in the current module.
- Option text uses Chinese name first, username fallback.
- Selecting a user keeps the current language filter when present.
- Selecting a language keeps the current user filter when present.
- Invalid, missing, or non-numeric `user_id` means `全部用户`.
- If the selected user has no projects in that module, the page renders the normal empty state for the combined filters.
- Non-superadmin users do not see the user select and the backend ignores `user_id`.

Keep the implementation in the route-store adapter so route modules do not add direct `appcore.db` dependencies.

## Query Contract

Extend `list_projects_with_creator()` with an optional `filter_user_id`.

When `is_admin` is true and `filter_user_id` is present:

```sql
WHERE p.type = '<project_type>'
  AND p.deleted_at IS NULL
  AND p.user_id = %s
```

When `is_admin` is false, keep the existing owner scope:

```sql
WHERE p.user_id = %s
  AND p.type = '<project_type>'
  AND p.deleted_at IS NULL
```

Add `list_project_creators()` to return `{id, display_name}` rows for the select. It uses the same owner display expression pattern and only returns creators for non-deleted projects in the requested type.

## UI

Use existing Ocean Blue tokens and keep the control dense:

- A `.filter-bar` flex row below the language pills.
- A native `<select>` with height 32px.
- No new JavaScript framework.
- A short `onchange` handler updates `user_id` in the URL and preserves `lang`.

The select is rendered only when `show_user_filter` is true.

## Non-Goals

- No schema migration.
- No new API endpoint.
- No changes to detail-page access or task operation permissions.
- No service restart or deployment.

## Verification

- Route-store tests cover SQL for admin filtered list, non-admin ignored filter, and creator option query.
- Multi-translate route tests cover superadmin user filter SQL args and rendered selector label.
- Omni-translate route tests cover the same behavior.
- Existing route tests continue to pass.
- Manual route checks use Flask test clients only; no local MySQL access.
