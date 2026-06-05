# Pushes Modal Progress Workbench Design

- **Date**: 2026-06-04
- **Anchors**:
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-06-03-pushes-material-text-link-chain-design.md`

## Goal

When an operator clicks the material push button in the `/pushes` modal, the modal should switch from the review layout to a full-size progress workbench for the chained push pipeline:

1. 素材推送
2. 文案推送
3. 链接推送

The workbench lets the operator see the request JSON, the returned data, and the final state for every push step without opening the separate JSON tabs.

## Behavior

- The progress workbench uses the same modal envelope size as the existing push page modal.
- On desktop, the workbench is split horizontally into three columns from left to right:
  - 素材推送: 36%.
  - 文案推送: 36%.
  - 链接推送: 28%.
- On narrow screens, the same three sections may stack vertically to avoid text overlap.
- Every step has a title bar. The right side of the title bar shows a large status label:
  - `排队中` with a queue icon.
  - `推送中` with a loading animation.
  - `已完成 ✅`.
  - `推送错误 ❌`.
- Inside each step, the upper panel uses 70% of the step content area for the corresponding push request JSON.
- The lower panel uses 30% of the step content area for the result summary and response JSON.
- Before the network call starts, 素材推送 is `推送中`, 文案推送 and 链接推送 are `排队中`.
- The workbench must not pre-render copywriting or link request JSON from the modal's initial payload load. These later steps depend on the material push result, especially the latest `mk_id`, `handle`, and product code state.
- Copywriting request JSON is filled only after the material step has finished and the backend has generated the copywriting request from the latest product state.
- Link request JSON is filled only after the copywriting step has finished and the backend has generated the link request from the latest product state.
- Backend chained-push responses must include the actual runtime `target_url` and `payload` used by copywriting and link push, including failure cases where the payload was built before the downstream call failed.
- When the chained backend response returns, the material step is marked completed, while the copywriting and link steps are marked completed or error according to `localized_texts_push.ok` and `product_links_push.ok`.
- If the material request throws, the material step becomes `推送错误 ❌` and the queued steps remain visible for operator context.

## Scope

- Frontend-only presentation change in `web/static/pushes.js` and `web/static/pushes.css`.
- Keep the existing manual `推送文案` and `推送链接` tabs unchanged for review and retry.
- Keep the backend chained push contract from `2026-06-03-pushes-material-text-link-chain-design.md`.

## Test Coverage

- `tests/test_pushes_routes.py` asserts the full-size modal envelope and the progress result collapse / reopen controls.
- `tests/test_pushes_ui_assets.py` keeps static frontend asset coverage for the push modal.
