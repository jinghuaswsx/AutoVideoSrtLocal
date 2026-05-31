# AutoPush 素材推送后自动推文案

- **日期**：2026-05-30
- **上位锚点**：
  - `AGENTS.md`
  - `AutoPush/README.md`
  - `docs/明空素材推送接口.md`

## 背景

AutoPush 推送管理里，“推素材”和“推送小语种文案”已经具备各自的手动入口。新品推素材成功后，下游会返回或确认明空 `mk_id`，操作员还需要切换到文案页签再手动推送小语种文案。这个断点容易遗漏，尤其是在新品素材集中发布时。

现有 `/api/push-items/by-keys` 已返回素材 payload、`item_id`、`mk_id`、`localized_text` 和 `localized_texts_request`；AutoPush 也已有 `/api/marketing/medias/{mk_id}/texts` 代理接口。新设计在前端把这两个能力串成一次可观察的流水线。

## 目标

1. 用户在推送列表点击“去推送/重推”后，弹窗仍是推素材入口；点击主按钮后先推素材。
2. 只要素材推送流程拿到可用 `mk_id`，系统立即自动启动小语种文案推送。
3. 弹窗实时展示当前执行步骤、请求数据、响应数据和阻塞原因。
4. 原有“推送小语种文案 / 小语种文案 JSON 预览”手动功能保留，用于复核、补推和异常重试。
5. 素材推送成功但文案推送失败时，不回滚素材结果；界面清楚标记为“素材成功，文案失败”。

## 不做范围

- 不新建独立页面。
- 不改变主项目 `/openapi/push-items/*` 的认证方式、推送状态机或写回语义。
- 不把素材推送和文案推送合并成后端单接口；前端需要逐步展示请求与响应。
- 不删除手动文案推送页签。
- 不新增定时任务、数据库表或迁移。

## 交互设计

推送弹窗改为“自动推送工作台”。顶部继续保留现有页签：

- `推送确认`
- `JSON 预览`
- `推送小语种文案`
- `小语种文案JSON预览`

`推送确认` 页签从单列内容改成双栏：

- 左栏：`素材推送`
  - 展示素材信息、素材 payload 摘要、视频/封面预览。
  - 展示将要发送的素材请求 JSON。
  - 展示状态：等待、加载中、已就绪、校验中、推送中、成功、失败。
  - 成功后展示素材推送响应、写回响应以及当前 `mk_id`。

- 右栏：`文案推送`
  - 初始为“等待 MKID”。
  - 拿到 `mk_id` 后自动进入“准备文案请求”和“推送中”。
  - 展示目标 URL、文案请求 JSON、文案推送响应 JSON。
  - 如果没有 `mk_id`、没有可推送文案或接口报错，展示明确阻塞原因。

底部主按钮文案为 `推送素材并自动推文案`。执行中主按钮禁用，取消按钮禁用；关闭按钮仍可关闭弹窗。执行完成后：

- 全部成功：主按钮显示 `已完成`，关闭弹窗会刷新列表。
- 素材失败：主按钮显示 `重新推送素材`。
- 素材成功、文案失败：主按钮显示 `重试文案推送`，再次点击只重试文案步骤。
- 素材成功、文案因缺 `mk_id` 或缺文案被跳过：主按钮禁用，手动页签保留为复核入口。

## 数据流

1. 打开弹窗时，优先通过三元组调用 `/api/push-items/by-keys`，拿到：
   - `item_id`
   - `payload`
   - `mk_id`
   - `localized_texts_request`
2. 前端渲染素材 payload 和文案 request 预览。
3. 用户点击主按钮后，前端校验素材 payload。
4. 前端调用 `/api/push-items/{item_id}/push`，或在没有 `item_id` 时调用旧 `/api/push/medias`。
5. 素材成功后，前端用以下优先级解析 `mk_id`：
   - `/api/push-items/by-keys` 返回的 `mk_id`
   - 素材推送响应里可识别的 `mk_id` / `mkId` / `data.mk_id`
6. 如果 `mk_id` 存在且 `localized_texts_request.texts` 非空，调用 `/api/marketing/medias/{mk_id}/texts`。
7. 每一步都把请求 URL、请求 JSON、响应 JSON 或错误 detail 追加到对应日志面板。
8. 任一步成功后设置 `anyPushSucceeded=true`；关闭弹窗时触发列表刷新。

## 日志与可视化

日志面板只显示本次弹窗内的运行过程，不持久化。每条日志包含：

- 时间，使用浏览器本地时间，精确到秒。
- 阶段，取值为 `payload`、`material`、`copywriting`。
- 方向，取值为 `request`、`response`、`error`、`info`。
- 标题，例如 `素材推送请求`、`小语种文案推送响应`。
- JSON 或文本详情。

为避免页面过长，日志区域使用固定最大高度并可滚动。请求和响应都以 `<pre>` 显示完整 JSON；无法解析为 JSON 的响应按文本显示。

## 错误处理

- payload 加载失败：停留在加载错误状态，主按钮不可用。
- 素材 payload 校验失败：不发请求，左栏显示校验错误。
- 素材推送 HTTP 失败：左栏显示错误响应，不自动推文案。
- 素材推送成功但写回失败：按现有后端返回的 `writeback_error` 展示警告，仍允许自动推文案，因为下游素材已经成功。
- 缺 `mk_id`：右栏显示“缺少 mk_id，未启动文案推送”，素材成功状态保留。
- 缺文案：右栏显示“当前暂无可推送小语种文案”，素材成功状态保留。
- 文案推送失败：右栏显示错误响应，主按钮切到仅重试文案。

## 实现范围

- `AutoPush/static/app.js`
  - 重构 `openPushModal` 的主按钮流程为显式 pipeline。
  - 增加步骤状态、日志渲染、请求/响应格式化。
  - 自动串联素材推送和文案推送。
  - 保留现有手动小语种文案页签逻辑。

- `AutoPush/static/app.css`
  - 增加双栏工作台、步骤状态、日志面板样式。
  - 低于 900px 时降为单列，避免移动端挤压。

- `tests/test_autopush_ui_assets.py`
  - 静态覆盖自动流水线关键字符串和调用顺序。
  - 覆盖手动文案页签仍存在。

- `tests/test_autopush_routes.py`
  - 复用现有文案代理测试；如前端需要更多字段，仅补充后端返回结构测试。

## 验证

1. `pytest tests/test_autopush_ui_assets.py tests/test_autopush_routes.py -q`
2. 如触及主项目 openapi 结构，补跑：
   `pytest tests/test_openapi_materials_routes.py tests/test_openapi_push_items_service.py -q`
3. 启动 AutoPush：
   `cd AutoPush && python main.py`
4. 浏览器打开 `http://127.0.0.1:8787`，验证：
   - 推送列表可加载。
   - 点击待推送素材后，弹窗显示双栏工作台。
   - 点击主按钮后左栏先推素材，拿到 `mk_id` 后右栏自动推文案。
   - 两栏都打印请求 JSON 和响应 JSON。
   - 手动“推送小语种文案 / 小语种文案JSON预览”页签仍可使用。
