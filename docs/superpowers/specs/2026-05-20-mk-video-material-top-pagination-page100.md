# Mingkong Video Material Top Pagination Page100

Last updated: 2026-05-20

## Context

`/xuanpin/mk` 的 `视频素材库` 和 `昨天消耗前100` 都读取本地归档接口：

- `GET /xuanpin/api/mk-material-library`
- `GET /xuanpin/api/mk-yesterday-top100`

`docs/superpowers/specs/2026-05-18-mingkong-daily-material-snapshot-top100-design.md`
已经规定两个接口的 `page_size` 默认值和上限都是 `100`。后端
`appcore.mingkong_materials._page_bounds()` 也按最大 `100` 处理。

当前页面前端仍以 `24` 条请求卡片列表，而且分页控件只在列表底部。
运营在卡片列表顶部扫素材时，需要不滚到底部也能翻页。

## Scope

本次只调整 `/xuanpin/mk` 页面内两个明空素材卡片列表：

1. `视频素材库` 每页请求 `100` 条。
2. `昨天消耗前100` 每页请求 `100` 条。
3. `视频素材库` 顶部和底部都显示同一套分页控件。
4. `昨天消耗前100` 顶部和底部都显示同一套分页控件。
5. 点击任一处分页按钮都调用现有加载函数，并共享同一个页码状态。

不改后端分页口径，不新增接口，不改变卡片排序、筛选、导入、小语种动作或媒体预览逻辑。

## UI Behavior

- 顶部分页放在状态文本下方、卡片网格上方。
- 底部分页保留在卡片网格下方。
- 两处分页显示 `上一页`、当前页/总页数、`下一页`，和现有视觉样式一致。
- 加载中或加载失败时分页容器清空。
- 响应成功后即使空结果也显示禁用的 `1 / 1` 分页，沿用当前页面现有分页行为。

## Verification

- 模板测试覆盖顶部分页容器、底部分页容器、分页同步渲染函数和 `MK_VIDEO_PAGE_SIZE = 100`。
- 路由测试继续覆盖 `/xuanpin/mk` 页面存在两个本地归档接口。
- 聚焦运行：

```bash
pytest tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
```
