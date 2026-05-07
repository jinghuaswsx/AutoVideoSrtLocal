# Domain Input UI Sizing Design

## Docs Anchor

- Primary anchor: `AGENTS.md#Frontend Design System — Ocean Blue Admin`
- Product context: `docs/superpowers/plans/2026-05-07-multi-domain-unification.md#Task 6: Material Management UI`

## Requirement

On `/admin/settings?tab=domains`, enlarge the "新增域名" add-domain control row so it is easier to see and operate:

- The "新增域名" label text is roughly twice the previous size.
- The domain input is roughly twice the previous width and height.
- The whole add-domain row has `100px` of vertical separation before the domain list below it.

## Scope

Only the admin settings domain-management template changes. The domain add/save/delete behavior, route handlers, database calls, and material-management domain resolver remain unchanged.

## Acceptance

- The add-domain label uses a dedicated template class and renders at `26px`.
- The add-domain input uses a dedicated template class and renders at `440px` wide and `48px` tall, with `max-width:100%` for smaller screens.
- The add-domain row renders with `margin-bottom:100px`.
- Existing `/admin/settings?tab=domains` route behavior continues to pass focused tests.
