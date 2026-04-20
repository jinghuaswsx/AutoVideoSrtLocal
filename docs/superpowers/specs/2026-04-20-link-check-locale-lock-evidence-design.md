# Link Check Locale Lock Evidence Design

日期：2026-04-20

## 背景

`link-check` 当前会抓取 Shopify 商品页图片并与参考图做比对，但在部分店铺上存在地区重定向与首访状态机：

1. 用户输入的是小语种 URL，例如 `.../de/products/...`
2. 首次请求时，Shopify 可能先把请求导向英语页 `.../products/...`
3. 同一个会话内再次强制访问原始小语种 URL，页面又可能恢复到目标语种
4. 即使页面已经锁定到目标语种，如果图片下载阶段被 CDN 或中间层重定向到另一张资源，当前实现也缺少明确证据输出

这会导致两类问题：

1. 抓到的页面不是目标语种页面
2. 用户难以确认“这批下载图片究竟来自哪个页面、下载时是否被偷换”

## 目标

本次改动只解决 locale 锁定与证据链可见性，不扩展图片分析策略。

目标如下：

1. 首次 locale 未锁定时，复用同一个 `requests.Session` 对原始 URL 追加两次强制访问
2. 追加访问之间固定等待 2 秒
3. 任意一次访问锁定成功后，立即停止后续 warm-up 重试
4. 如果 warm-up 重试仍失败，再走现有 `hreflang/canonical` 纠偏逻辑
5. 只有在确认当前页面已经锁定为目标页面后，才允许进入图片下载阶段
6. 只要页面未确认锁定成功，就直接报错，不触发任何图片下载
7. 页面级和图片级证据写入任务状态并通过 API 暴露
8. `link-check` 详情页直接展示这些证据，便于人工确认来源

非目标：

1. 不改 Gemini 分析逻辑
2. 不改参考图匹配算法
3. 不为其他项目类型新增通用证据组件

## 方案对比

### 方案 A：按需 warm-up + 证据持久化

流程：

1. 首次请求原始 URL
2. 若未锁定目标语种，则在同一 Session 内对原始 URL 再请求 2 次
3. 第 2 次与第 3 次请求前分别等待 2 秒
4. 若任意一次命中目标语种则停止 warm-up
5. 若 3 次都失败，再进入现有 `hreflang/canonical` 纠偏
6. 全程记录每次尝试结果，并把图片下载最终落点保存下来

优点：

1. 符合实测到的 Shopify 首访状态机
2. 只在首访失败时产生额外等待，正常站点不受影响
3. 证据完整，可用于详情页展示和后续排查

缺点：

1. 首访失败时最多会增加 4 秒等待
2. 需要扩展抓取器、运行时、API 序列化与详情页渲染

### 方案 B：总是固定访问 3 次

优点：

1. 实现简单
2. 不需要先判断首访是否失败

缺点：

1. 所有任务都会额外变慢
2. 对已稳定命中的站点属于无效请求

### 方案 C：只保留 `hreflang/canonical` 纠偏

优点：

1. 改动最小

缺点：

1. 无法利用已实测存在的 Shopify warm-up 行为
2. 对首访状态机店铺的稳定性不足

结论：采用方案 A。

## 详细设计

## 一、抓取器 warm-up 重试

修改文件：`appcore/link_check_fetcher.py`

在 `fetch_page()` 的 locale lock 过程中引入“原始 URL warm-up”阶段。

处理顺序：

1. 首次请求原始 URL
2. 若首次响应已满足 locale lock，直接返回
3. 若未满足 locale lock，则继续在同一 Session 内请求原始 URL 2 次
4. 第 2 次请求前等待 2 秒
5. 第 3 次请求前再等待 2 秒
6. 若 warm-up 阶段任意一次命中目标语种，则直接返回该次响应
7. 若 warm-up 结束仍未锁定，再按现有逻辑解析 `hreflang/canonical` 生成纠偏 URL
8. 对纠偏 URL 再请求一次，若成功则返回；否则抛出 `LocaleLockError`
9. 只有 `locked=true` 的响应才允许传给 `extract_images_from_html()` 和后续 `download_images()`
10. 只要未拿到 `locked=true` 的最终页面，整个任务直接失败，且 `download` 步骤保持未开始

说明：

1. “强制访问原始 URL”指 warm-up 阶段始终访问用户最初输入的 URL，而不是访问第一次跳转后的英语 URL
2. 原始查询参数必须保留，尤其是 `variant`
3. 仍继续使用 `Accept-Language` 头
4. “目标页面”必须同时满足以下硬条件之一：`html lang` 命中目标语种，或最终 URL 明确落在目标语种 locale 路径，且整个 lock 流程给出 `locked=true`
5. 只要上述条件未满足，就视为未到达目标页面，不允许下载图片

## 二、页面级证据链

任务根对象新增字段：`locale_evidence`

结构：

```json
{
  "target_language": "de",
  "requested_url": "https://shop.example.com/de/products/demo?variant=123",
  "lock_source": "warmup_attempt_2",
  "locked": true,
  "failure_reason": "",
  "attempts": [
    {
      "phase": "initial",
      "attempt_index": 1,
      "wait_seconds_before_request": 0,
      "requested_url": ".../de/products/demo?variant=123",
      "resolved_url": ".../products/demo?variant=123",
      "page_language": "en",
      "locked": false
    },
    {
      "phase": "warmup",
      "attempt_index": 2,
      "wait_seconds_before_request": 2,
      "requested_url": ".../de/products/demo?variant=123",
      "resolved_url": ".../de/products/demo?variant=123",
      "page_language": "de",
      "locked": true
    }
  ]
}
```

规则：

1. `attempts` 按真实发生顺序写入
2. `phase` 只允许 `initial`、`warmup`、`alternate_locale`
3. `lock_source` 标记最终命中的来源，如 `initial`、`warmup_attempt_2`、`warmup_attempt_3`、`alternate_locale`
4. 若最终失败，`locked=false` 且 `failure_reason` 写入可读错误

## 三、图片级证据链

每个抓取图片项新增 `download_evidence`

结构：

```json
{
  "requested_source_url": "https://shop.example.com/cdn/shop/files/a.jpg?v=1",
  "resolved_source_url": "https://shop.example.com/cdn/shop/files/a.jpg?v=1",
  "redirect_preserved_asset": true,
  "variant_selected": true,
  "evidence_status": "ok",
  "evidence_reason": ""
}
```

规则：

1. `requested_source_url` 为页面解析得到的图片 URL
2. `resolved_source_url` 为实际下载响应的最终 URL
3. 若下载最终落到不同路径或不同 host，`redirect_preserved_asset=false`
4. 若 `redirect_preserved_asset=false`，当前实现仍直接抛错，不把该图当作成功下载
5. `variant_selected=true` 表示该图来自当前 `variant` 的 featured media 提升顺序

## 四、运行时持久化

修改文件：`appcore/link_check_runtime.py`、`appcore/task_state.py`

要求：

1. `create_link_check()` 初始化 `locale_evidence`
2. `fetch_page()` 返回页面证据
3. `runtime.start()` 只有在 `locale_evidence.locked=true` 时才进入下载步骤
4. 若 `locale_evidence.locked=false` 或抓取器抛出 `LocaleLockError`，任务立即失败，不创建任何 site image item
5. 下载完成后，每个 `item` 都带上 `download_evidence`
6. `task_state.update()` 持久化这些字段到 `projects.state_json`

## 五、API 暴露

修改文件：`web/routes/link_check.py`

要求：

1. `/api/link-check/tasks/<task_id>` 返回任务级 `locale_evidence`
2. `items[*]` 返回 `download_evidence`
3. 保持向后兼容，不删除现有字段

## 六、详情页展示

修改文件：`web/static/link_check.js`、`web/templates/link_check_detail.html`，必要时补 `web/static/link_check.css`

详情页新增两个展示区：

1. 页面锁定证据
   - 目标语种
   - 最终锁定来源
   - 每次访问尝试表
   - 每次访问前等待秒数
   - 每次请求 URL、最终 URL、`html lang`、是否命中

2. 图片下载证据
   - 每张图的请求 URL
   - 最终下载 URL
   - 是否保持同一资源
   - 是否来自当前 `variant`

展示原则：

1. 优先可读，不展示内部异常栈
2. 失败项使用现有 warning/danger 样式
3. 不引入新页面，只扩展现有 detail 页面

## 七、测试

新增或修改测试：

1. `tests/test_link_check_fetcher.py`
   - 首访失败后 warm-up 第 2 次命中
   - 首访失败后 warm-up 第 3 次命中
   - warm-up 三次都失败后再走 `alternate_locale`
   - warm-up 之间确实等待 2 秒
   - `locale_evidence.attempts` 顺序正确
   - 下载证据包含 `requested_source_url` / `resolved_source_url`

2. `tests/test_link_check_runtime.py`
   - runtime 会持久化 `locale_evidence`
   - runtime 会把 `download_evidence` 写入 item

3. `tests/test_link_check_routes.py`
   - 任务详情 API 会返回 `locale_evidence` 与 `download_evidence`

4. `tests/test_link_check_ui_assets.py`
   - detail 页面脚本会渲染页面锁定证据
   - detail 页面脚本会渲染图片下载证据

## 八、风险与边界

1. warm-up 等待会让首访失败的任务变慢，最长增加约 4 秒
2. 如果目标站点对高频请求更敏感，未来可把 warm-up 次数与间隔做成配置，但本次不做
3. 若某些店铺的图片下载会合法跳到不同 CDN host，而资源路径不变，当前“必须同 host 同 path”判定可能过严

本次先保守执行当前规则，因为现有目标是“确保不是被偷换成别的图”。如果后续发现存在稳定合法跨 host 场景，再单独放宽判定策略。

## 验收标准

1. 对存在首访重定向的 Shopify 店铺，`de` URL 在 warm-up 或 `alternate_locale` 后能稳定锁到 `de`
2. 任务结果 API 中可直接看到完整页面锁定证据
3. 详情页中可以人工确认“最终是第几次访问锁定成功”
4. 详情页中可以人工确认“每张图下载前后的最终 URL 是否一致”
5. 只要最终页面不是目标页面，任务必须在下载前直接失败
6. 若图片下载被重定向到不同资源，任务明确失败并输出原因
