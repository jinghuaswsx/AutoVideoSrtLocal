# 任务中心翻译产物直达检查入口设计

- **日期**：2026-05-21
- **状态**：用户确认方案，待实施
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-step-review-assets-design.md`
  - `docs/superpowers/specs/2026-05-09-product-link-management-modal.md`
  - `docs/superpowers/specs/2026-04-19-medias-localized-detail-image-translation-design.md`
  - `docs/superpowers/specs/2026-04-18-bulk-translate-design.md`

## 背景

任务中心子任务详情抽屉已经展示“翻译产物状态”，后端也能计算视频、封面、文案、详情图、商品链接、Shopify 商品图替换等验收项。但当前 UI 只有一个总的“打开素材管理”入口，以及产出素材列表里的“素材库”按钮。翻译员或审核员看到某一项异常时，仍要自己判断应该去素材管理哪个位置、哪个弹窗、哪个语种 tab 检查。

本次改造要把每一个翻译产物都变成可检查、可定位的对象。已有路由直接复用；没有路由的补轻量路由或 bridge action。

## 目标

1. 子任务“翻译产物状态”里的每一个产物项都带跳转按钮，能直接检查对应产物位置。
2. 产物入口由后端统一返回 `actions`，前端只渲染按钮，不在模板里散落业务 URL 规则。
3. 产品相关产物和视频相关产物分区展示，用户能直接区分应该去素材管理产品编辑、产品链接管理、图片翻译详情，还是视频翻译详情。
4. 已有路由优先复用：素材对象、封面、详情图、图片翻译详情、素材管理页面、产品链接管理 API。
5. 补齐 `copywriting_translate` 子任务只读详情页，避免文案翻译任务没有可查看入口。
6. 不改变任务状态机、不新增数据库表、不改变提交验收 gate 的语义。

## 产物分类

### 产品相关

| 产物 | 验收 key | 检查目标 |
| --- | --- | --- |
| 产品小语种链接 | `product_links` | 素材管理产品编辑弹窗 -> 当前语种 -> 产品链接管理弹窗；同时可打开每个域名实际链接 |
| 小语种文案 | `translated_copywriting` | 素材管理产品编辑弹窗 -> 当前语种 -> 文案区；可查看文案翻译子任务详情 |
| 小语种商品详情图翻译 | `detail_images` | 素材管理产品编辑弹窗 -> 当前语种 -> 商品详情图区；已有图可直接打开 `/medias/detail-image/<id>`；翻译任务可打开 `/image-translate/<id>` |
| 小语种商品图替换 | `shopify_images` | 素材管理产品编辑弹窗 -> 当前语种 -> 产品链接管理弹窗中的 Shopify 状态行 |

### 视频相关

| 产物 | 验收 key | 检查目标 |
| --- | --- | --- |
| 小语种翻译结果视频 | `translated_video` / `localized_media_item` | 直接预览 `/medias/object?object_key=...`；同时定位素材管理对应 `media_item` |
| 视频封面 | `translated_cover` | 直接打开 `/medias/item-cover/<item_id>`；同时定位素材管理对应 `media_item` 封面 |

## 后端契约

### `actions`

`appcore.tasks.get_child_readiness()` 返回的每个 `checks[]` 项新增 `actions` 数组：

```json
{
  "key": "translated_video",
  "label": "视频翻译结果",
  "ok": true,
  "required": true,
  "reason": "",
  "actions": [
    {
      "label": "预览视频",
      "url": "/medias/object?object_key=...",
      "kind": "preview",
      "primary": true
    },
    {
      "label": "定位素材",
      "url": "/medias/?q=robot-kit-rjc&from_task=44&product=9&lang=de&action=video&item=5",
      "kind": "locate"
    }
  ]
}
```

字段规则：

- `label`：按钮文案，中文。
- `url`：只允许站内相对 URL 或实际商品链接；站内优先。
- `kind`：`locate | preview | task | external | recheck`。
- `primary`：同一产物最推荐点击的按钮。
- `disabled_reason`：当暂时不能生成 URL 时返回原因，前端渲染为禁用按钮或提示。

### action 生成规则

1. `localized_media_item`
   - 有目标语种 `media_item`：返回“定位素材”。
   - 没有目标语种 `media_item`：返回“去生成/绑定素材”，指向 `action=translate`。
2. `translated_video`
   - 有 `object_key`：返回“预览视频”和“定位素材”。
   - 无 `object_key`：返回“定位素材”，用于补上传。
3. `translated_cover`
   - 有 `cover_object_key`：返回“查看封面”和“定位封面”。
   - 无封面：返回“定位封面”。
4. `translated_copywriting`
   - 返回“定位文案”，指向 `action=copywriting`。
   - 若能找到最近的 `copywriting_translate` 或 bulk translate 子任务，返回“查看文案翻译任务”。
5. `detail_images`
   - 返回“定位详情图”，指向 `action=detail_images`。
   - 若已有目标语种详情图，返回若干“查看详情图”链接，最多展示前 3 张。
   - 若能找到关联 `image_translate` 任务，返回“查看图片翻译任务”。
6. `shopify_images`
   - 返回“检查商品图替换”，指向 `action=product_links&focus=shopify_images`。
7. `product_links`
   - 返回“检查产品链接”，指向 `action=product_links&focus=product_links`。
   - 对每个解析出的链接返回“打开 <domain>”。

### 产出素材面板

`list_task_artifacts()` 保持返回 `media_items`，但 `/tasks/api/<tid>/artifacts` 的响应为每个 item 补充 `actions`：

- “预览视频”
- “查看封面”（有封面时）
- “定位素材”
- “翻译任务记录”

父任务聚合子任务产物时按语种排序；前端可分组展示。

## 素材管理 bridge action

`/medias/?q=<product_code>&from_task=<task_id>&product=<pid>&lang=<lang>&action=<action>` 继续作为任务中心到素材管理的统一入口。新增 action：

| action | 行为 |
| --- | --- |
| `video` | 打开产品编辑弹窗，切到目标语种，滚动并高亮对应 `media_item` |
| `cover` | 打开产品编辑弹窗，切到目标语种，滚动并高亮对应素材封面 |
| `copywriting` | 打开产品编辑弹窗，切到目标语种，滚动到文案区 |
| `detail_images` | 打开产品编辑弹窗，切到目标语种，滚动到商品详情图区 |
| `product_links` | 打开产品编辑弹窗，切到目标语种，自动打开产品链接管理弹窗 |

可选 query：

- `item=<media_item_id>`：定位具体视频素材或封面。
- `focus=shopify_images|product_links`：产品链接管理弹窗里高亮对应状态块。
- `detail_image=<id>`：定位具体详情图。

旧 action `translate` 和 `history` 保持兼容。

## 缺失路由

### `copywriting_translate` 只读详情

新增页面路由：

```text
GET /copywriting-translate/<task_id>
```

新增 API：

```text
GET /api/copywriting-translate/<task_id>
```

页面内容：

- 任务状态、源语言、目标语种、父 bulk translate 任务链接。
- 源英文文案 ID 和源文案内容。
- 目标语种输出文案内容。
- 失败原因、创建时间、更新时间。

权限：

- `@login_required`。
- 任务创建者可见；管理员可见。
- 只读，不提供本期外的新重跑或编辑入口。

同时修正 bulk translate 的 `copywriting_translate` 子任务详情 URL，不再指向 `/copywriting/<id>`。

## 前端展示

### 任务详情抽屉

“翻译产物状态”面板改成结构化列表：

- 行左侧：状态、产物名、原因、计数、域名状态。
- 行右侧：后端返回的 `actions` 按钮。
- 成功项按钮文案偏检查，例如“预览视频”“查看封面”。
- 失败项按钮文案偏补齐，例如“定位文案”“检查产品链接”。
- 移动端按钮换行，不能挤压文本。

### 审核流程

步骤内审核素材继续沿用 `review-assets` 的视频和图片预览。新按钮只补在验收项和产出素材面板，不替代审核卡片里的实际预览。

### 产出素材面板

每个产出素材行显示：

- 语种 badge
- 文件名
- 推送状态
- actions：预览视频、查看封面、定位素材、翻译任务记录

## 不做范围

1. 不新增任务子状态，不把视频、文案、封面、详情图拆成独立任务中心节点。
2. 不新增数据库表或迁移。
3. 不改变 `submit_child()` 的验收 gate。
4. 不在提交时实时 HTTP 探活。
5. 不重做素材管理编辑弹窗，只加 deep-link 定位和高亮。
6. 不把 `copywriting_translate` 变成完整编辑器，本期只做只读详情。

## 实施顺序

1. `appcore.tasks`：新增 action builder，扩展 child readiness 和 artifacts 响应。
2. `web/templates/tasks_list.html`：渲染 check actions 和 artifact actions。
3. `web/templates/medias_list.html` / `web/static/medias.js`：扩展 bridge action 定位能力。
4. `web/routes/copywriting_translate.py` + 模板：新增只读详情页和 API。
5. `appcore.bulk_translate_projection` / `web/static/bulk_translate_detail.js`：修正 `copywriting_translate` detail URL。
6. 测试和手工验证。

## 验证计划

1. `pytest tests/test_appcore_tasks_supporting_data.py tests/test_tasks_routes.py tests/test_task_review_assets_service.py -q`
2. `pytest tests/test_copywriting_translate_routes.py tests/test_bulk_translate_projection.py -q`
3. `python3 -m compileall appcore/tasks.py appcore/bulk_translate_projection.py web/routes/tasks.py web/routes/copywriting_translate.py`
4. 手工打开 `/tasks/`：
   - 子任务抽屉每个验收项都有按钮。
   - 视频可直接预览，封面和详情图可直接打开。
   - 文案、详情图、产品链接按钮能打开素材管理对应语种和对应区域。
   - 产品链接按钮能自动打开产品链接管理弹窗。
   - `copywriting_translate` 子任务能打开只读详情页。
   - 移动端按钮不溢出、不遮挡状态文本。

本项目规则禁止连接 Windows 本机 MySQL；涉及真实 DB 的验证不使用 `127.0.0.1:3306`。
