# Media Video Material Sticky Pagination Design

## Goal

视频素材管理页在浏览大量素材时，顶部管理区保持可见，并把每页数量固定为 100 条，减少翻页和滚动操作成本。

## Scope

- `素材管理 -> 视频素材管理` 默认每页展示 100 条素材。
- 视频素材管理筛选区上方增加一套分页按钮，和底部分页共享页码状态。
- 翻页或在素材列表内滚动时，页面标题区、产品/视频 tab、筛选区、顶部分页和表头保持在顶部可见。
- 语种和广告计划筛选控件高度、边框、圆角、字体和 focus 状态与搜索框保持一致，遵守 Ocean Blue 控件规范。

## UI Behavior

- 顶部分页放在筛选区下方、表格上方；页数超过 1 页时显示，只有 1 页时隐藏。
- 点击顶部或底部分页按钮都会更新同一个 `page` 状态并重新加载列表。
- 重新加载列表后，滚动容器回到顶部，sticky 顶部区域不随列表内容移动。
- 列表容器高度按当前可视窗口和 sticky 区域高度计算，列表行在容器内滚动，表头贴在列表容器顶部。

## Implementation Notes

- 复用现有 `GET /medias/api/video-materials` 的 `page` / `page_size` 参数，不新增接口。
- 后端默认 `page_size` 从 50 调整到 100，保留最大 100 的上限。
- 前端 `media_video_materials.js` 的默认 `pageSize` 从 50 调整到 100。
- sticky offset 计算必须按当前激活 tab 选择工具栏和列表，避免视频 tab 使用产品 tab 的隐藏节点高度。

## Testing

- Route tests cover video material page rendering of top pager and sticky variables.
- Route tests cover API default `page_size=100` and explicit `page_size` pass-through.
- Service tests cover default page size of 100 and existing clamp behavior.
