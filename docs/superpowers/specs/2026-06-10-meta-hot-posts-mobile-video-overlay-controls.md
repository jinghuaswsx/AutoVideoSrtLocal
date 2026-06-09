# Meta 热帖移动端视频浮层播放控件

最后更新：2026-06-10

## 背景

`/xuanpin/meta-hot-posts` 和 `/xuanpin/meta-hot-posts/<post_id>` 的视频卡片当前采用封面懒加载：点击封面后，把卡片视频区域替换为真实播放器。移动端浏览时，用户需要一个更明确的全屏播放入口，并且在浮层播放时能够下载视频和退出播放状态。

现有事实来源：

- `docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md#后台页面`：视频卡片只先加载封面，点击后再加载真实视频播放器或 Facebook iframe。
- `docs/superpowers/specs/2026-05-14-meta-hot-posts-video-localization-design.md#Rendering`：本地 MP4 和 TOS MP4 优先，Facebook iframe 兜底。
- `docs/superpowers/specs/2026-05-20-xuanpin-single-video-playback-design.md#前端行为`：Meta 热帖页已保持同页单视频播放，播放一个视频时暂停其它热帖视频。

## 目标

1. 日常点击封面仍保持现有内联播放逻辑，不改变列表刷视频节奏。
2. 视频卡片封面上增加独立全屏播放入口。
3. 全屏浮层覆盖页面播放视频，浮层内提供下载入口。
4. 浮层右上角提供透明关闭按钮，用户可退出全屏播放效果。
5. 关闭浮层时暂停并移除浮层内视频，避免继续占用播放源。
6. 列表页和详情页保持同一套交互。

## 设计

- `renderVideoShell(row, videoHtml)` 继续输出封面、时长、原有播放按钮，并新增全屏按钮。
- 全屏按钮只在当前卡片有可直接播放的 MP4 URL 时渲染：
  - 当前视频源为 TOS 且 `tos_video_url` 存在时使用 TOS MP4。
  - 否则使用 `local_video_url`。
  - 仅有 Facebook iframe 兜底时不展示下载/全屏浮层按钮，继续走现有内联 iframe 逻辑。
- 新增页面级浮层：
  - 固定定位覆盖整个 viewport，深色半透明背景。
  - 中央视频使用 `controls autoplay playsinline preload="metadata"`。
  - 顶部工具区包含下载链接和关闭按钮。
  - 下载链接使用当前 MP4 URL，优先带 `download` 属性；浏览器如因同源限制改为打开新窗口也可接受。
  - 关闭按钮使用透明/半透明底色，不阻挡视频主要画面。
- 关闭方式：
  - 点击关闭按钮。
  - 点击浮层背景。
  - 按 `Escape`。
- 打开浮层前先调用现有 `pauseMetaHotVideos()`，保持单播放源行为。

## 非目标

- 不新增后端 API。
- 不改变本地视频下载/同步任务。
- 不改变数据库 schema。
- 不为 Facebook iframe 生成下载入口。
- 不改变 PC 端卡片 2x 放大逻辑。

## 验证

- `pytest tests/test_meta_hot_posts_routes.py tests/test_xuanpin_routes.py -q`
- 未登录 `/xuanpin/meta-hot-posts` 继续 302。
- 登录后 `/xuanpin/meta-hot-posts` 和 `/xuanpin/meta-hot-posts/<post_id>` 模板包含全屏入口、下载入口、关闭逻辑、Esc 关闭逻辑。
