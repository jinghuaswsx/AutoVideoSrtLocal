# Order Analytics Secondary Screen Adaptation

## Context

The data analysis page already has a mobile shell for viewports below 768px, but a portrait secondary monitor can be much wider than that while still feeling like a mobile aspect ratio. In that state the topbar tabs and actions compete for horizontal space, and the realtime KPI grid keeps a dense desktop layout.

## Scope

- Page: `/order-analytics`
- Frontend only: `web/templates/order_analytics.html`
- No backend metrics, API, database, or scheduling changes
- Keep the global `mobile.css` rules unchanged

## Design

Add a page-scoped compact desktop breakpoint for `max-width: 1180px` or portrait desktop viewports above the mobile shell. In that breakpoint:

- Show the existing in-content `.oa-mobile-tabs` and `.ppr-mobile-actions` controls.
- Hide the duplicated topbar `.oa-tabs-topbar` and `.ppr-actions` controls to prevent crowding.
- Let realtime toolbar controls wrap into stable full-width rows where needed.
- Render realtime summary KPI rows as two columns with compact spacing and content-height cards.

The existing mobile breakpoint remains the stronger rule for phones. The desktop landscape layout remains unchanged for wide monitors.

## Verification

- Static contract test locks the compact breakpoint and realtime summary grid rules.
- Existing order analytics template layout tests continue to cover tab/action placement and mobile table behavior.
