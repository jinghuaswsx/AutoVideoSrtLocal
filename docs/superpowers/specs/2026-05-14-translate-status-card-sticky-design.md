# Translate Status Card Sticky Design

Date: 2026-05-14

## Document Anchors

- `AGENTS.md` requires code changes to use repository documents as anchors and identifies `docs/superpowers/specs/` as the source of truth.
- `web/templates/CLAUDE.md` requires multi / omni translate detail pages to extend `_translate_detail_shell.html` and place additions inside the shared shell / workbench structure.
- `web/static/CLAUDE.md` defines the Ocean Blue admin visual system used by cards, borders, spacing, and responsive states.
- `docs/superpowers/specs/2026-04-18-multi-translate-design.md` defines `/multi-translate/<task_id>` as a workbench page built on `_task_workbench.html`.
- `docs/superpowers/specs/2026-05-13-multi-translate-optional-progress-design.md` defines the top status card as the place for source / target language, current phase, progress percentage, resume action, and optional item state.
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md` defines `/omni-translate/` as a separate experiment workspace that shares the translate detail workbench surface.

## Requirement

On the "全能视频翻译" and "多语种视频翻译" task detail pages, the top status card must stay fixed at the top of the scroll viewport while the user scrolls up or down through the long workbench content. The card must keep progress visible and keep recovery / optional action state reachable without scrolling back to the page top.

## Design

Use the existing shared `#taskStatusCard` in `_translate_detail_shell.html`. Do not duplicate the card or create a second floating mirror.

The card will use CSS `position: sticky` with a top offset below the shared `.topbar`:

- desktop top offset: `68px`, leaving space for the 56px sticky topbar and a small gap;
- mobile top offset: `60px`, leaving space for the 52px mobile topbar and a small gap.

The sticky state keeps the same DOM and the same `renderStatusCard()` updates. This avoids syncing two cards and preserves existing resume button delegation, progress updates, AI analysis status, and translation quality assessment status.

The card gets a higher local z-index and a lightweight shadow so it remains legible above step cards while scrolling. Width stays in normal document flow, so it remains aligned with the content column and does not cover the sidebar or escape the main layout.

The sticky card background must be opaque. Use solid state-tinted backgrounds instead of transparent overlays: a very light blue for running, a very light green for done, a very light amber for waiting, and a very light red for error. The default idle background can stay on `var(--bg-card)`. This keeps the fixed card from showing scrolled content through it while preserving status recognition.

## Non-Goals

- Do not change pipeline behavior, task state, sockets, route payloads, or database fields.
- Do not make the `detail-topbar` sticky with the status card.
- Do not add a separate mini progress bar.
- Do not change `av_sync` unless it already uses the shared `detail_mode in ('multi', 'av_sync')` status card styles. The requested modules are omni and multi.

## Acceptance

1. `_translate_detail_shell.html` keeps a single `id="taskStatusCard"` status card.
2. The status card CSS includes `position: sticky`, top offsets, and a z-index.
3. Mobile CSS keeps the sticky offset below the 52px mobile topbar.
4. Status modifier backgrounds are opaque solid colors and do not use transparent `rgba(...)`, `hsla(...)`, `transparent`, or translucent layout tokens.
5. Existing progress rendering still targets the same `#taskStatusCard`, `#statusProgressFill`, and `#statusResumeBtn` elements.
6. Multi and omni route tests still render their detail pages successfully.

## Verification

Run:

```bash
pytest tests/test_translate_detail_shell_templates.py::test_translate_status_card_is_sticky_below_topbar -q
pytest tests/test_translate_detail_shell_templates.py tests/test_multi_translate_routes.py tests/test_omni_translate_routes.py -q
```

For route smoke checks, start a local dev server on an unused port and verify an unauthenticated detail route returns `302`, not `500`.
