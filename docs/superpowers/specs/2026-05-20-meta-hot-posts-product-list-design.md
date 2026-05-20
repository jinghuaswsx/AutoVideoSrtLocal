# Meta 热帖产品列表设计

最后更新：2026-05-20

## 背景

`/xuanpin/meta-hot-posts` 当前按视频素材卡片浏览 Meta 热帖。运营还需要一个按产品聚合的入口，快速看到每个产品的主题、名称，以及该产品下已经采集到的素材数量。

## 范围

1. Meta 热帖子 tab 增加「产品列表」按钮，位置在「我的收藏夹」右侧。
2. 点击「产品列表」后，页面从视频卡片视图切换为产品聚合列表。
3. 产品列表展示：
   - 产品主题：使用现有 `category_l1`，前端显示中文类目名；未分类显示「未分类」。
   - 产品名称：优先使用 `product_title_zh`，其次 `product_title`，再其次商品链接。
   - 素材数：按同一个 `product_url_hash` 聚合统计 `meta_hot_posts` 记录数。
4. 产品列表按素材数倒序、产品名称升序展示，不跟当前素材库筛选条件联动。
5. 不新增数据库表，不改现有同步和分析任务。

## 后端接口

新增只读接口：

- `GET /xuanpin/api/meta-hot-posts/products?page=1&page_size=100`

接口沿用 Meta 热帖页面权限：

- 未登录返回登录跳转。
- 无 `meta_hot_posts` 权限返回 403。
- 有权限用户返回产品聚合数据。

响应字段：

- `items`
- `total`
- `page`
- `page_size`

每个 item 包含：

- `product_url_hash`
- `product_url`
- `category_l1`
- `category_l1_zh`
- `product_title`
- `product_title_zh`
- `product_title_display`
- `product_main_image_url`
- `material_count`

## 前端行为

- 「产品列表」子 tab 激活时隐藏视频卡片网格和视频分页，显示产品表格。
- 表格列为「产品主题」「产品名称」「素材数」。
- 产品名称可点击打开商品链接；没有链接时只显示文本。
- `mhCount` 显示产品总数，状态栏显示当前页产品数、总页数和总产品数。
- 切回素材库、今日新增、Top50 或收藏夹时恢复原卡片网格。

## 验证

- `pytest tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_service.py tests/test_meta_hot_posts_routes.py tests/test_xuanpin_routes.py -q`
- 未登录访问 `/xuanpin/meta-hot-posts` 继续返回 302。
- 登录且有权限用户访问 `/xuanpin/api/meta-hot-posts/products` 返回 200。
