# Mingkong Video Card Inline Play Design

## Context

`/xuanpin/mk` has two local material-card views:

- `视频素材库`
- `昨天消耗前100`

Both views render cards through `web/templates/mk_selection.html` and already keep a
lazy `<video>` element per card. Today operators must click the card's `视频` tab and
then click the browser video control before playback starts. This slows down the
material-review workflow shown in the Mingkong selection page.

## Design Anchors

- `AGENTS.md#主题指引`: Mingkong selection and task-center behavior must follow the
  existing `/xuanpin/mk` specs.
- `docs/superpowers/specs/2026-05-18-mingkong-daily-material-snapshot-top100-design.md#api-and-ui`:
  `视频素材库` and `昨天消耗前100` use local archived card rows and reuse
  `mk-media` / `mk-video` proxy paths.
- `docs/superpowers/specs/2026-05-18-mingkong-video-material-local-index-design.md#frontend-rendering`:
  `renderMkVideoMaterialCard()` should keep local cover preference and keep MP4
  preview through `/xuanpin/api/mk-video` on demand.
- `web/templates/CLAUDE.md#csrf--路由守卫`: no new POST route is needed; existing
  route guards remain unchanged.

## Scope

Add a center play affordance to the cover area of Mingkong video cards in:

- `视频素材库`
- `昨天消耗前100`
- existing Mingkong product detail modal video cards, because it uses the same card
  preview structure and activation helper.

Do not change backend APIs, database schema, scheduler behavior, material import, or
task creation.

## User Experience

When a card has `video_path`:

1. The cover pane displays a centered circular play button.
2. Clicking that button switches the same card from `图片` to `视频`.
3. The lazy video `src` is set from `data-mk-video-src`.
4. The page calls `video.play()` from the click handler.
5. If the browser blocks autoplay or the video is still downloading, the card still
   stays on the video pane with native controls visible.

When a card has no `video_path`, no play button is rendered and the existing disabled
video-tab behavior remains.

The click must not trigger product-link navigation or import buttons. It only affects
the current card.

## Implementation Notes

- Keep `activateMkVideoTab(tab)` as the central tab-switching helper.
- Add a small helper, for example `playMkVideoFromButton(button)`, that finds the
  nearest `.mk-video-card`, finds the card's video tab, calls `activateMkVideoTab`,
  then calls `video.play()` when available.
- Update `activateMkVideoTab` so callers can request playback after lazy `src`
  assignment without duplicating lazy-load logic.
- Add CSS for an absolute-positioned play button inside `.mk-video-cover-frame`.
  The button should be stable over the cover image, accessible via `aria-label`, and
  visually disabled by absence rather than rendering a disabled overlay.
- Keep `preload="none"` so list rendering does not bulk-download MP4 files.

## Error Handling

- `video.play()` returns a promise in modern browsers. Catch and ignore rejections so
  autoplay policy failures do not surface as console-breaking unhandled promise
  rejections.
- Missing card, missing video tab, missing video element, or missing source should be
  a no-op.

## Verification

Focused automated checks:

- Template test asserts the play button class/data hook exists.
- Template test asserts the click path calls `playMkVideoFromButton`.
- Template test asserts playback still uses `data-mk-video-src` and `/xuanpin/api/mk-video`.
- Template test asserts `video.play()` rejection is handled.
- Existing xuanpin route tests continue to pass.

Commands:

```bash
pytest tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
```

Manual checks after implementation:

- Unauthenticated `GET /xuanpin/mk` returns 302.
- Logged-in admin `GET /xuanpin/mk` returns 200.
- In `视频素材库`, clicking a cover play button switches the card to video and starts
  or prepares playback without a second tab click.
- In `昨天消耗前100`, the same interaction works.

## Non-Goals

- No separate video detail page.
- No modal player.
- No eager MP4 preloading while scrolling the grid.
- No change to Mingkong credential, cache, or proxy behavior.
