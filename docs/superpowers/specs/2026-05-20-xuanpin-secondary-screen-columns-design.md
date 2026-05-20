# Xuanpin Secondary Screen Columns Design

Last updated: 2026-05-20

## Context

The user's secondary monitor is portrait:

- Display 2 bounds: `1440x2560`
- Display 2 working area: `1440x2512`
- Chrome is running with device scale factor `1.5`
- Practical portrait CSS width is therefore about `960px`

`/xuanpin/mk#products` currently renders a 13-column product table with a
desktop column budget around `1690px`. That works only with horizontal scrolling.
On a portrait secondary monitor the table keeps height, but the right-side
columns are pushed out of view.

## Requirements

1. Keep every product-list column available on the secondary screen.
2. Do not hide business columns in the portrait desktop layout.
3. Prefer compact density over horizontal scrolling for the product table.
4. Release sidebar space on portrait desktop by using the existing drawer
   toggle behavior for navigation.
5. Keep the regular desktop layout readable on wide monitors.
6. Do not access Windows local MySQL for verification.

## Layout Strategy

The Mingkong product table uses three density bands:

1. Wide desktop keeps the existing generous layout.
2. Secondary half-screen / narrow desktop (`<= 2100px` CSS viewport) reduces
   image, text, padding, and numeric column widths so the full 13-column table
   can fit when Chrome is snapped to a narrower secondary-screen region.
3. Portrait desktop (`min-width: 769px` and `orientation: portrait`) auto-collapses
   the sidebar, trims page padding, and applies a tighter 13-column budget tuned
   for about `960px` CSS width.

The portrait band preserves the same table semantics and the same column order.
It only changes density:

- Smaller product image.
- Shorter copy controls.
- Clamped product and Chinese-name text.
- Compact status, material, and action controls.
- Smaller numeric cells.

## Verification

Static tests should assert:

- The product table owns explicit column classes for all columns.
- The portrait desktop media query exists.
- The portrait layout collapses sidebar space for this page.
- The compact column budget uses CSS variables instead of removing columns.

