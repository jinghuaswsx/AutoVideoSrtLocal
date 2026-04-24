# Shopify 图片更换任务中心设计

日期：2026-04-25

## 结论

这个方向可行。它不应该只做成一个“自动换图 worker 队列”，而应该做成一套“产品 × 语种”的图片与链接准入状态。任务中心负责自动执行，产品编辑页负责提醒与人工确认，推送接口负责阻断未确认或失败的语种。

## 目标

第一版要完成四件事：

1. 后端能判断某个产品语种是否具备自动换图条件，并生成待处理任务。
2. Worker 能持续领取任务，复用现有 CDP 流程执行轮播图和详情图替换，并回写结果。
3. 产品语种有明确状态：自动换图完成、失败、链接不可用、等待人工确认、人工确认正常。
4. AutoPush 推送准入接入该状态。未人工确认正常的语种不能提交到推送。

## 非目标

第一版不把前台小语种页面的轮播图显示结果作为硬性自动成功条件。原因是 Shopify / 地区适配 / 缓存会导致同一个小语种链接短时间内混杂英文图和小语种图，多刷新后才稳定。前台检测可以作为诊断日志，但不能阻断任务成功。

轮播图的硬性验证改为 EZ 后台读回：换完目标语种后关闭页面，重新进入对应 EZ 页面，等待加载完成，检查每个预期图位下面都有目标语种标记，例如 `it` 对应 Italian 标记。标记数量与预期需要替换的图位数量对上，即认为轮播图后台更换成功。

第一版不把 Shopify 自动化搬到服务器执行。浏览器登录态、CDP Chrome profile、Shopify embedded app 自动化都继续留在 Windows worker。

## 状态模型

每个 `media_products.id × lang` 维护一份状态摘要，落在 `media_products.shopify_image_status_json`：

```json
{
  "it": {
    "replace_status": "auto_done",
    "link_status": "needs_review",
    "last_task_id": 123,
    "last_error": "",
    "result_summary": {
      "carousel_requested": 11,
      "carousel_ok": 10,
      "carousel_skipped": 1,
      "detail_replacement_count": 4,
      "detail_skipped_existing_count": 6
    },
    "confirmed_by": null,
    "confirmed_at": null,
    "updated_at": "2026-04-25T10:00:00+08:00"
  }
}
```

`replace_status` 枚举：

- `none`：没有状态。
- `pending`：已具备条件，等待 worker。
- `running`：worker 已领取并执行中。
- `auto_done`：worker 已完成后台自动化，等待人工确认。
- `failed`：自动化失败，需要负责人处理。
- `confirmed`：人工确认图片和链接正常，可以推送。

`link_status` 枚举：

- `unknown`：未检查或没有记录。
- `needs_review`：自动化完成后等待人工确认。
- `normal`：人工确认链接与图片正常。
- `unavailable`：链接不可用或负责人手动标记不可用。

推送放行条件：

```text
replace_status == confirmed
link_status == normal
```

英文 `en` 不走这套图片更换准入；非英语语种才参与判断。

## 任务表

新增 `media_shopify_image_replace_tasks`：

```text
id
product_id
product_code
lang
shopify_product_id
link_url
status
attempt_count
max_attempts
worker_id
locked_until
claimed_at
started_at
finished_at
error_code
error_message
result_json
created_at
updated_at
```

`status` 枚举：

- `pending`：等待领取。
- `running`：已领取执行。
- `success`：worker 自动化完成。
- `failed`：worker 执行失败。
- `blocked`：素材、链接、Shopify ID 等条件不满足，暂不执行。
- `cancelled`：被人工取消或被新任务取代。

同一 `product_id + lang` 只允许存在一个 active 任务：`pending` 或 `running`。第一版由 DAO 在创建任务前查询并复用 active 任务，避免复杂唯一索引。

## 任务生成条件

后端认为某个产品语种具备换图条件，需要同时满足：

1. 产品存在且未删除。
2. 语种启用，且不是 `en`。
3. `product_code` 有值。
4. Shopify ID 可解析，优先使用产品字段里的 `shopifyid`。
5. 英文参考图存在。
6. 目标语种本地化图片存在。
7. 产品链接可确定。优先用 `localized_links_json[lang]`，没有时用默认链接 `https://newjoyloo.com/{lang}/products/{product_code}`。
8. 当前语种状态不是 `confirmed + normal`。

若条件不满足，不创建可执行任务；需要时写成 `blocked` 或在编辑页展示阻断原因。

## Worker API

新增接口都挂在现有 Shopify localizer OpenAPI 前缀下：

```text
POST /openapi/medias/shopify-image-localizer/tasks/claim
POST /openapi/medias/shopify-image-localizer/tasks/<task_id>/heartbeat
POST /openapi/medias/shopify-image-localizer/tasks/<task_id>/complete
POST /openapi/medias/shopify-image-localizer/tasks/<task_id>/fail
```

`claim` 入参：

```json
{
  "worker_id": "desktop-hostname-pid",
  "lock_seconds": 900
}
```

`claim` 返回任务参数：

```json
{
  "task": {
    "id": 123,
    "product_id": 456,
    "product_code": "sonic-lens-refresher-rjc",
    "lang": "it",
    "shopify_product_id": "8559391932589",
    "link_url": "https://newjoyloo.com/it/products/sonic-lens-refresher-rjc"
  }
}
```

没有任务时返回：

```json
{"task": null}
```

`complete` 入参包含现有 runner 的结果 JSON。后端写入任务 `success`，并把产品语种状态更新为：

```text
replace_status = auto_done
link_status = needs_review
```

`fail` 入参包含 `error_code`、`error_message` 和可选 `result_json`。后端写入任务 `failed`，并把产品语种状态更新为：

```text
replace_status = failed
link_status = needs_review 或 unavailable
```

当失败原因明确是链接不可用时，使用 `link_status = unavailable`。

## 自动成功判定

Worker 自动化成功不等于可推送，只表示后台换图流程完成。

轮播图自动成功条件：

- EZ 后台每个需要处理的 slot 都完成上传保存，状态为 `ok`。
- 已存在目标语种图的 slot 状态为 `skipped`，也算成功。
- 没有 `failed` slot。
- 完成后关闭并重新进入 EZ 页面，等待页面加载完成。
- 重新进入后，预期处理的图位都能读到目标语种标记。
- 目标语种标记数量与预期图位数量对得上。
- 不要求前台小语种页面立即读回全部轮播图。

详情图自动成功条件：

- TAA 后台上传图片成功并获得 Shopify CDN URL。
- `body_html` 完成整体替换并保存。
- 保存后重新读回 TAA 后台 HTML，新增 CDN URL 都存在。
- GIF、没有目标素材但使用 fallback original 的图片按现有 runner 规则记录，不作为失败。

可选诊断：

- Worker 可以额外记录前台 storefront 读到的图片数量和 URL，但该结果只写入 `result_json.diagnostics`，不参与成功失败判断。

## 人工确认

产品编辑页在每个非英语语种展示状态提醒：

- 自动换图完成，等待确认。
- 自动换图失败，需要处理。
- 链接不可用，已阻止推送。
- 已确认正常，可以推送。

提供操作：

- `确认链接图片正常`：设置 `replace_status=confirmed`、`link_status=normal`，记录 `confirmed_by`、`confirmed_at`。
- `标记链接不可用`：设置 `link_status=unavailable`，阻止推送。
- `重新排队换图`：创建或复用 `pending` 任务，清除当前失败提示。
- `清除确认状态`：回到 `needs_review`，用于发现问题后重新阻断。

## 推送准入

`appcore.pushes.compute_readiness()` 新增布尔项：

```text
shopify_image_confirmed
```

对于非英语语种，该项只有在 `replace_status=confirmed` 且 `link_status=normal` 时为 true。AutoPush 列表和 `by-keys` payload 接口都会继承这个阻断逻辑。

当阻断时，返回的 readiness 里额外包含可读原因，例如：

```json
{
  "shopify_image_confirmed": false,
  "shopify_image_reason": "图片已自动替换，等待人工确认"
}
```

## 失败提醒

第一版的提醒落在两个地方：

1. 产品编辑页对应语种的醒目提示。
2. OpenAPI push item 的 readiness reason，让 AutoPush 侧能看到不可推送原因。

后续如果要做主动通知，可以在 `failed` 和 `unavailable` 状态写入后接 OpenClaw 或站内通知，但这不阻塞第一版。

## 测试策略

实现必须按 TDD 推进，测试覆盖：

- DAO：状态 JSON 解析、更新、确认、标记不可用、重新排队。
- 任务中心：创建任务、复用 active 任务、claim 锁、complete/fail 写回。
- OpenAPI：worker 领取、完成、失败接口。
- 推送准入：未确认时 `not_ready`，确认正常后 `pending`。
- Worker 客户端：claim 到任务后调用现有 runner，成功和失败分别回写。
- 轮播图验证：模拟 EZ 读回结果，目标语种标记数量匹配时成功，缺少标记时失败。

## 验收标准

1. 后端能生成并领取 Shopify 图片替换任务。
2. Worker 成功执行后，产品语种进入 `auto_done + needs_review`。
3. 自动完成但未人工确认时，该语种不能推送。
4. 人工点击确认后，该语种可以推送。
5. 自动失败或链接不可用时，编辑页和推送 readiness 都能看到原因。
6. 轮播图以 EZ 后台重新进入后的目标语种标记为成功依据，不因前台缓存检测失败而误判自动化失败。
