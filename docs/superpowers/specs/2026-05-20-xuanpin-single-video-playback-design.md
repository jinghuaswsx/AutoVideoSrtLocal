# Xuanpin Single Video Playback Design

最后更新：2026-05-20

## 背景

选品中心存在多个按卡片浏览视频素材的页面。运营在同一页连续试播素材时，如果上一条视频不自动暂停，会出现多个视频同时播放、声音叠加的问题。

当前扫描结果：

- `/xuanpin/mk` 的明空视频素材库、昨天消耗前100、产品详情视频素材卡片都由 `web/templates/mk_selection.html` 渲染，使用懒加载 `<video class="mk-video-source">`。
- `/xuanpin/meta-hot-posts` 已经在页面内监听 `play` 事件，并暂停 `.meta-hot-page video` 中除当前播放器外的其它视频。
- `/xuanpin/tabcut`、`/xuanpin/today-recommendations`、`/xuanpin/new-products` 当前没有内嵌多 `<video>` 播放器；它们展示封面、表格或外链，不需要新增播放器逻辑。

## 设计锚点

- `AGENTS.md#硬红线`：改代码前必须有文档锚点，且常规开发在隔离 worktree 内进行。
- `AGENTS.md#主题指引`：选品中心相关行为以 `docs/superpowers/specs/` 为事实来源。
- `docs/superpowers/specs/2026-05-18-mingkong-video-card-inline-play-design.md#scope`：明空视频素材库、昨天消耗前100、产品详情视频素材卡片共享同一张卡片播放结构。
- `docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md#后台页面`：Meta 热帖是选品中心视频卡片页，视频点击后才加载真实播放器或 iframe。
- `docs/superpowers/specs/2026-05-20-meta-hot-posts-product-list-design.md#前端行为`：Meta 产品列表下钻后保留原视频卡片交互。
- `web/templates/CLAUDE.md#csrf--路由守卫`：本次不新增路由和 POST；现有守卫保持不变。

## 范围

实现方案 A：补齐 `/xuanpin/mk` 的页面内单播放源逻辑，并为 Meta 热帖保留现有行为加模板回归断言。

需要覆盖的明空卡片区域：

- 视频素材库。
- 昨天消耗前100。
- 产品详情弹层中的视频素材卡片。

不改变：

- 后端 API、数据库 schema、调度任务。
- 视频懒加载策略，继续使用 `data-mk-video-src` 和 `preload="none"`。
- Meta 热帖现有播放加载方式、视频源切换和产品列表下钻流程。
- Tabcut 封面外链行为。

## 前端行为

在 `/xuanpin/mk` 内，当任意 `.mk-video-source` 触发原生 `play` 事件时：

1. 查找当前页面内其它 `.mk-video-source`。
2. 对除当前播放器外、正在播放且未结束的视频调用 `pause()`。
3. 不重置其它视频的 `currentTime`，保留进度，避免用户回看时从头开始。
4. 只作用于明空卡片播放器，不影响页面外或其它模块的媒体元素。

点击封面播放按钮时：

1. 继续走 `playMkVideoFromButton()`。
2. 继续调用 `activateMkVideoTab(videoTab, {play: true})`，保持懒加载和切换 tab 的现有路径。
3. `video.play()` 触发原生 `play` 事件后，由统一监听暂停其它明空播放器。

用户直接点击浏览器原生 controls 播放某个视频时，也必须触发同一套单播放源逻辑。

## 实现计划

- 在 `web/templates/mk_selection.html` 增加 `pauseOtherMkVideos(activeVideo)`。
- 在同文件增加 `handleMkVideoPlay(event)`，确认事件目标是 `.mk-video-source` 后暂停其它明空视频。
- 使用捕获阶段监听 `document.addEventListener('play', handleMkVideoPlay, true)`，确保原生 video controls 也能覆盖。
- 保持 `activateMkVideoTab()` 的 lazy `src` 设置和 `play()` rejection catch 不变。
- 在 `tests/test_xuanpin_routes.py` 增加模板断言，锁定明空单播放源 helper、`play` 捕获监听和 Meta 现有 helper。

## 错误处理

- 缺少 active video、事件目标不是明空视频、页面无其它视频时均为 no-op。
- 对已经暂停或已经结束的视频不重复调用 `pause()`。
- 不捕获或吞掉播放器自身的加载错误；仍交给浏览器原生 controls 展示。

## 验证

自动化：

```bash
pytest tests/test_xuanpin_routes.py -q
```

手动回归：

- 未登录 `GET /xuanpin/mk` 返回 302。
- 登录管理员访问 `/xuanpin/mk` 返回 200。
- 在视频素材库播放第一条视频后，播放第二条视频，第一条暂停且进度保留。
- 在昨天消耗前100播放第一条视频后，播放第二条视频，第一条暂停且进度保留。
- 在产品详情弹层视频卡片中播放第二条视频时，第一条暂停。
- `/xuanpin/meta-hot-posts` 仍保持播放一个热帖视频时暂停其它热帖视频。

## 非目标

- 不做跨选品中心 tab 或跨浏览器标签页的全局互斥播放。
- 不暂停 Facebook iframe 播放器；iframe 仍按当前 Meta 热帖逻辑加载。
- 不新增视频弹窗、播放器组件或静态资源拆分。
