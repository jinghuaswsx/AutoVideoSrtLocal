# 推送管理必要条件管理员兜底确认设计

- 日期：2026-06-05
- 上位锚点：
  - `docs/superpowers/specs/2026-06-05-push-final-manual-confirmation-design.md`
  - `docs/superpowers/specs/2026-05-22-push-manual-link-confirm-design.md`
  - `docs/明空素材推送接口.md`

## 背景

推送管理列表的“推送必要条件状态”已经展示每条素材的 readiness 缺项，但现有人工确认入口在任务中心子任务详情里。管理员在推送管理页处理发布时，需要有最高优先级兜底能力：直接在当前行把某个必要条件人工确认成就绪，让这条记录重新计算为可推送。

## 目标

1. 在 `/pushes` 列表的“推送必要条件状态”列最后一行增加白色小按钮“人工确认”。
2. 点击后打开 modal，按行展示该素材可兜底的推送必要条件状态，包含 `final_push_confirmed`。
3. 每行开头都有“人工确认”按钮；点击后把该必要条件写入人工确认事件，作为最高优先级覆盖，后续 `compute_readiness()` 将该项视为 `True`。
4. 确认成功后刷新该素材推送状态缓存，并刷新推送管理列表，使管理员可以把一条未就绪记录兜底调整为可推送。
5. 兜底入口只打开 modal，不执行确认；modal 内每行按钮只确认当前行对应的单个 readiness key，不能批量确认其它必要条件。

## 设计

推送管理前端使用独立的 `READINESS_OVERRIDE_ISSUES` 映射，把 readiness key 映射到任务验收 step key：

| readiness key | modal label | task step key |
| --- | --- | --- |
| `is_listed` | 商品上架 | `product_listed` |
| `has_object` | 视频 | `translated_video` |
| `has_cover` | 封面 | `translated_cover` |
| `has_copywriting` | 文案 | `translated_copywriting` |
| `lang_supported` | 链接 | `language_supported` |
| `has_push_texts` | 英文文案格式 | `push_texts` |
| `shopify_image_confirmed` | 图片/链接确认 | `shopify_images` |
| `final_push_confirmed` | 人工最终推送确认 | `final_push_confirmation` |

`final_push_confirmed -> final_push_confirmation` 允许通过推送管理兜底确认，但只能在该行按钮被点击时写入该单项事件；打开 modal 或点击其它条件行不得顺带确认最终推送项。

持久化规则：

- 素材有关联 `media_items.task_id` 时，沿用任务中心已有 `task_events.event_type='manual_step_confirmed'`，保证任务中心和推送管理看到同一个人工确认结果。
- 素材没有 `task_id` 时，写入 `media_push_readiness_overrides`，以 `media_item_id + readiness_key` 唯一记录管理员在推送管理里的兜底确认。该路径用于没有通过任务中心生成、无法回到任务中心点击最终确认的视频素材。
- `compute_readiness()` 同时读取任务级人工确认和媒体项级管理员确认；两者任一存在即把对应 readiness key 视为 `True`。
- 媒体项级确认只影响当前素材行，不反写任务中心，不创建任务，不批量确认其它 readiness key。

后端新增管理员接口：

`POST /pushes/api/items/<item_id>/readiness-overrides`

请求体：

```json
{ "key": "has_cover" }
```

接口校验：

- 必须登录且是管理员。
- `item_id` 必须存在且可访问。
- `key` 必须是上述允许的 readiness key。
- 素材可以没有 `task_id`；无 `task_id` 时写媒体项级管理员确认记录。

成功后调用任务服务写入人工确认事件，刷新该 item 的推送状态缓存，并返回最新 `status` 与 `readiness`。

## UI

`renderReadinessText()` 在两行条件和域名详情后追加一行：

`[人工确认]`

按钮使用白底小按钮风格，作为兜底入口，不放到操作列。modal 内每行结构：

`[人工确认/已确认] 条件名 当前状态 说明`

已就绪项仍可显示“已就绪”，按钮禁用；未就绪项可人工确认。没有 `task_id` 的素材仍允许确认，确认记录只绑定当前 `media_item_id`。

## 验证

1. `pytest tests/test_appcore_pushes.py::test_admin_override_readiness_key_confirms_child_step_and_refreshes_cache -q`
2. `pytest tests/test_appcore_pushes.py::test_admin_override_readiness_key_confirms_item_without_task -q`
3. `pytest tests/test_appcore_pushes.py::test_compute_status_pending_after_item_level_admin_final_confirmation -q`
4. `pytest tests/test_pushes_ui_assets.py::test_pushes_script_renders_readiness_admin_override_modal -q`
5. `pytest tests/test_pushes_routes.py::test_pushes_admin_readiness_override_endpoint_confirms_step -q`
6. `pytest tests/test_pushes_routes.py::test_pushes_admin_readiness_override_allows_unbound_item_level_confirmation -q`
7. `pytest tests/test_push_status_cache_schema.py::test_push_readiness_overrides_migration_declares_item_level_table -q`
8. `python3 -m compileall appcore/pushes.py appcore/tasks.py web/routes/pushes.py`
