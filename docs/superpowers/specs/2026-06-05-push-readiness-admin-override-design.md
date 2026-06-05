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
2. 点击后打开 modal，按行展示该素材的所有推送必要条件状态。
3. 每行开头都有“人工确认”按钮；点击后把该必要条件写入人工确认事件，作为最高优先级覆盖，后续 `compute_readiness()` 将该项视为 `True`。
4. 确认成功后刷新该素材推送状态缓存，并刷新推送管理列表，使管理员可以把一条未就绪记录兜底调整为可推送。

## 设计

复用任务中心已有 `task_events.event_type='manual_step_confirmed'`，不新增表。推送管理前端使用现有 `REWORK_ISSUES` 映射，把 readiness key 映射到任务验收 step key：

| readiness key | modal label | task step key |
| --- | --- | --- |
| `is_listed` | 商品上架 | `product_listed` |
| `has_object` | 视频 | `translated_video` |
| `has_cover` | 封面 | `translated_cover` |
| `has_copywriting` | 文案 | `translated_copywriting` |
| `lang_supported` | 链接 | `language_supported` |
| `has_push_texts` | 英文文案格式 | `push_texts` |
| `shopify_image_confirmed` | 图片/链接确认 | `shopify_images` |
| `final_push_confirmed` | 推送人工确认 | `final_push_confirmation` |

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
- 素材必须关联 `task_id`，否则无法写任务事件，返回错误。

成功后调用任务服务写入人工确认事件，刷新该 item 的推送状态缓存，并返回最新 `status` 与 `readiness`。

## UI

`renderReadinessText()` 在两行条件和域名详情后追加一行：

`[人工确认]`

按钮使用白底小按钮风格，作为兜底入口，不放到操作列。modal 内每行结构：

`[人工确认/已确认] 条件名 当前状态 说明`

已就绪项仍可显示“已就绪”，按钮禁用；未就绪项可人工确认。没有 `task_id` 的素材按钮禁用并提示无法兜底。

## 验证

1. `pytest tests/test_appcore_pushes.py::test_admin_override_readiness_key_confirms_child_step_and_refreshes_cache -q`
2. `pytest tests/test_pushes_ui_assets.py::test_pushes_script_renders_readiness_admin_override_modal -q`
3. `pytest tests/test_pushes_routes.py::test_pushes_admin_readiness_override_endpoint_confirms_step -q`
4. `python3 -m compileall appcore/pushes.py appcore/tasks.py web/routes/pushes.py`
