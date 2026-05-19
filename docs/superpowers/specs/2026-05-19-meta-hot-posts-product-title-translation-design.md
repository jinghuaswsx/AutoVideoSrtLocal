# Meta 热帖商品标题翻译与卡片顶部布局设计

## 背景

`/xuanpin/meta-hot-posts` 卡片顶部仍使用“帖子”作为主标题，商品页标题只在下方商品分析块中展示。运营扫卡片时更需要第一眼看到商品标题，并且希望商品标题和热帖文案一样有中文缓存：有中文时默认显示中文，没有中文时显示英文，两者都缺失时保留两行空位避免卡片跳动。

## Scope

1. `meta_hot_post_product_analyses` 新增商品标题中文缓存字段：`product_title_zh`、状态、尝试次数、错误和翻译时间。
2. 新增 `meta_hot_posts.translate_product_title` LLM use case，固定默认 `openrouter / google/gemini-3.1-flash-lite / openrouter`。
3. 新增手动接口 `POST /xuanpin/api/meta-hot-posts/<post_id>/product-title/translate-zh`，沿用 Meta 热帖登录和 `meta_hot_posts` 权限门禁。
4. Meta 热帖文案翻译定时任务同时扫描已提取但未缓存中文标题的商品分析记录，翻译成功后写回 `product_title_zh`。
5. 卡片顶部第一行保留帖子 ID、复制/链接按钮，并把“行 / 不行”标记按钮放到右侧同一行。
6. 卡片顶部第二块显示商品页标题，预留两行。默认显示中文缓存；无中文缓存时显示英文；都没有时留空两行。
7. 标题末尾按钮状态：
   - 显示中文时为“英文标题”，点击切到英文。
   - 显示英文且已有中文缓存时为“显示中文”，点击切到中文。
   - 显示英文且无中文缓存时为“翻译中文”，点击接口翻译并落库，成功后显示中文并可中英文切换。

## Data Flow

商品分析仍负责提取英文标题并写入 `product_title`。标题中文翻译由 Meta 热帖翻译 tick 或手动按钮触发，统一调用 `product_title_translation.translate_product_title()`，使用 OpenRouter Gemini 3.1 Flash-Lite。服务层 hydrate 时同时返回英文、中文和默认展示字段；前端只负责显示、切换和手动触发。

## Verification

- `tests/test_meta_hot_posts_product_title_translation.py`
- `tests/test_meta_hot_posts_store.py`
- `tests/test_meta_hot_posts_service.py`
- `tests/test_meta_hot_posts_routes.py`
- `tests/test_meta_hot_posts_scheduler.py`
- `tests/test_llm_use_cases_registry.py`
- `tests/test_db_migration_meta_hot_posts_marked.py`
