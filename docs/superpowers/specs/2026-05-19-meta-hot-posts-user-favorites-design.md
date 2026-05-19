# Meta 热帖用户收藏夹设计

## 背景

`/xuanpin/meta-hot-posts` 已经提供素材库、今日新增、欧洲 Top50、美国 Top50 四个子 tab。运营在浏览视频卡片时，需要把值得后续跟进的视频放入个人收藏夹。收藏夹必须按用户维度隔离，每个用户只看到自己的收藏。

## 范围

1. 每张 Meta 热帖视频卡片左上角增加收藏夹操作：
   - 未收藏：显示“加入收藏夹”。
   - 已收藏：显示“取消收藏”。
2. Meta 热帖子 tab 增加“我的收藏夹”按钮，展示当前登录用户收藏的视频卡片。
3. 收藏夹列表默认按收藏时间倒序。
4. 收藏夹列表提供排序选择：
   - 收藏时间：按收藏时间倒序。
   - 互动量：按 `latest_likes` 倒序，再按收藏时间倒序。
   - 帖子创建时间：按 `creation_time` 倒序，再按收藏时间倒序。
5. 收藏夹不复用现有“行 / 不行”标注字段，避免把个人收藏和运营判断混在一起。

## 数据模型

新增 `meta_hot_post_favorites` 表：

- `id`：自增主键。
- `user_id`：收藏所属用户，引用 `users.id`。
- `hot_post_id`：收藏的 Meta 热帖，引用 `meta_hot_posts.id`。
- `created_at`：收藏时间。
- 唯一约束：`user_id, hot_post_id`。
- 索引：`user_id, created_at`、`hot_post_id`。

## 后端接口

沿用 `/xuanpin/meta-hot-posts` 的登录与 `meta_hot_posts` 权限门禁：

- `GET /xuanpin/api/meta-hot-posts/favorites?sort=favorited_at|interactions|creation_time&page=1&page_size=50`
  - 返回当前用户收藏的视频卡片。
  - 默认 `sort=favorited_at`。
  - 返回字段包含 `is_favorited: true` 与 `favorited_at`。
- `POST /xuanpin/api/meta-hot-posts/<post_id>/favorite`
  - body：`{"favorited": true}` 加入收藏。
  - body：`{"favorited": false}` 取消收藏。
  - 返回 `{ok, id, is_favorited}`。

现有素材库、今日新增、欧洲 Top50、美国 Top50 返回的卡片 payload 增加 `is_favorited`，用于前端渲染按钮状态。

## 前端行为

- 子 tab 增加“我的收藏夹”。
- 收藏夹 tab 顶部显示排序下拉框，仅在收藏夹 tab 可见。
- 点击收藏按钮采用乐观更新；请求失败时恢复原状态并在状态栏显示失败。
- 取消收藏后如果当前在“我的收藏夹”tab，立即从当前列表移除该卡片。
- 收藏按钮放在卡片媒体区左上角，优先不挤占现有“行 / 不行”标注区。

## 验证

- `pytest tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_service.py tests/test_meta_hot_posts_routes.py tests/test_db_migration_meta_hot_posts_marked.py -q`
- 未登录访问 `/xuanpin/meta-hot-posts` 继续 302。
- 已登录且有 `meta_hot_posts` 权限用户可访问页面和收藏夹 API。
- POST 收藏接口由前端携带 `X-CSRFToken`。
