# Pushes Material Text Link Chain Design

- **Date**: 2026-06-03
- **Anchors**:
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-30-autopush-material-copywriting-auto-push-design.md`
  - `docs/明空素材推送接口.md`

## Goal

On the current `/pushes` page, clicking the material push button should run the existing push operations in order:

1. Push the video material.
2. Push localized copywriting.
3. Push product links with the same logic used by the existing "推送链接" tab.

The material push remains the state-changing source of truth. Copywriting and link push results are attached to the same response so the modal can show all three outcomes.

## Behavior

- If material push fails, stop and return the existing material error.
- If material push succeeds, always attempt the localized copywriting step.
- After the copywriting step finishes, attempt the product link step.
- Copywriting or link failures must not roll back a successful material push.
- The manual "推送文案" and "推送链接" tabs remain available for review and retry.

## Scope

- Reuse `web.routes.pushes._push_localized_texts_result()`.
- Reuse `appcore.pushes.push_product_links()` through a small route helper so the material push endpoint and the existing link endpoint share the same error mapping and audit behavior.
- Extend `web/static/pushes.js` to display the backend `product_links_push` result alongside the existing material and copywriting result.

