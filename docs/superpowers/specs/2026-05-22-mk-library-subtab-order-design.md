# MK Library Subtab Order Design

## Anchor

- `AGENTS.md#文档驱动代码`
- `AGENTS.md#主题指引`
- `docs/superpowers/specs/2026-05-18-mingkong-video-material-library-subtabs-design.md#UI`
- `docs/superpowers/specs/2026-05-18-mingkong-daily-material-snapshot-top100-design.md#API And UI`

## Requirement

On `/xuanpin/mk`, the Mingkong inner library tabs should prioritize the video
material workflow:

1. `视频素材库`
2. `昨天消耗前100`
3. `产品库`

When the page opens without a hash, `视频素材库` is the default active inner tab.
Existing hash deep links keep working:

- `#videos` opens `视频素材库`.
- `#yesterday-top100` opens `昨天消耗前100`.
- `#products` opens `产品库`.

The product table row `素材库` action still switches to `视频素材库` and filters by
the selected product code.

## Implementation

Keep the existing `mk_selection.html` panel structure and APIs unchanged. Only
change tab ordering, initial active state, and the normalization fallback for an
empty or unknown hash.

## Verification

- Template tests assert tab order, default active state, and JavaScript fallback.
- Focused route tests for `/xuanpin/mk` continue to pass.
