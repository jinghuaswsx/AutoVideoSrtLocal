# Pushes Mobile List Display Fix

- **Date**: 2026-06-03
- **Anchor Docs**:
  - `docs/superpowers/specs/2026-05-01-mobile-ios-responsive-design.md`
  - `docs/superpowers/specs/2026-04-18-push-management-design.md`
  - `docs/superpowers/specs/2026-05-22-pushes-list-fast-path-design.md`

## Problem

On iPhone-width screens, `/pushes` shows the filter panel but the list data below it can be effectively invisible. The current mobile CSS still keeps the push list as a 1160px-wide table and constrains `.push-table-shell` height with the sticky filter header height. When the filter panel is tall, that calculated max-height can collapse the data area.

## Requirements

- Keep the existing `/pushes/api/items` data contract, filters, pagination, and push actions unchanged.
- On mobile, the filter header must not reserve sticky height that hides the list.
- On iPhone-width screens, push rows must render as readable stacked cards instead of a wide horizontal table.
- Desktop and secondary-screen table layouts remain unchanged.

## Implementation Notes

- Make `.push-header-sticky` static on mobile and remove the table shell height cap.
- Under `max-width: 480px`, hide the table header/colgroup and render `tbody > tr` as card-like grids.
- Use existing cell classes emitted by `renderRow()` so no API or row data changes are needed.

## Verification

- Add asset tests that assert the mobile CSS contains the static mobile header, uncapped table shell, and card layout rules.
- Run `pytest tests/test_pushes_ui_assets.py tests/test_pushes_routes.py -q`.
- Check `web/static/pushes.js` with `node --check`.
