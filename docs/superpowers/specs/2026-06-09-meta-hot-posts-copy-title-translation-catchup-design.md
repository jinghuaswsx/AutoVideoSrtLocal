# Meta 热帖文案与标题中文补齐

## 背景

`/xuanpin/meta-hot-posts` 卡片需要让运营默认读到中文。既有定时任务 `meta_hot_posts_translate_messages_tick` 已负责文案 `message_zh_html` 和商品标题 `product_title_zh` 的中文缓存，但存量或失败记录不能因为尝试次数耗尽而长期停留在英文状态。

同时，卡片上未缓存中文的帖子文案也必须提供单卡片即时翻译按钮，避免运营在页面浏览时只能等待后台队列。

## Scope

1. 定时任务继续每 10 分钟扫描 Meta 热帖卡片文案和商品标题，覆盖所有尚未生成中文缓存的记录。
2. 未生成中文缓存的失败记录允许继续进入定时队列；全局 provider 配置/额度类错误仍按现有 stop_reason 立即停止本轮，避免整批无效调用。
3. 新增 `POST /xuanpin/api/meta-hot-posts/<post_id>/message/translate-zh`，沿用 Meta 热帖登录和权限门禁。
4. 单卡文案即时翻译固定走 OpenRouter `google/gemini-3.1-flash-lite`，使用 `meta_hot_posts.translate_message` 计费用例，成功后写回 `message_zh_html` 并返回 hydrate 后的 item。
5. 前端卡片正文区域：未缓存中文且存在文案时显示「翻译」按钮；翻译成功后局部刷新当前卡片，已缓存中文时保留“显示原文案 / 显示翻译文案”切换。
6. 商品标题即时翻译继续沿用 `POST /xuanpin/api/meta-hot-posts/<post_id>/product-title/translate-zh` 和 OpenRouter `google/gemini-3.1-flash-lite`。

## Verification

- `tests/test_meta_hot_posts_message_translation.py` 覆盖单卡文案翻译的 Flash-Lite override。
- `tests/test_meta_hot_posts_service.py` 覆盖文案即时翻译接口服务逻辑和缓存返回。
- `tests/test_meta_hot_posts_routes.py` 覆盖模板按钮与新增 route。
- `tests/test_meta_hot_posts_store.py` 覆盖待翻译队列不会因失败尝试次数耗尽而永久跳过。
- `tests/test_meta_hot_posts_scheduler.py` 覆盖定时翻译继续同时扫描文案和商品标题。
