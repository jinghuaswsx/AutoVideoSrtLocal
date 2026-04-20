# 图片翻译重试按钮改版设计

**日期**：2026-04-20
**所属模块**：`web/routes/image_translate.py` + `web/templates/_image_translate_scripts.html` + `web/templates/image_translate_detail.html` + `web/templates/_image_translate_styles.html`

## 背景与问题

图片翻译任务运行期间，主进程发生重启（部署、崩溃、`systemctl restart`）后：

1. 数据库里 task 状态仍是 `running`，某些 item 状态仍是 `running`；`progress.failed` 仍是 0。
2. Runtime 线程随主进程一起死亡，没有任何代码把"僵尸 running"标成 `failed` 或推进任务。
3. 详情页已有的入口**都不出现**：
   - 全局「一键重新生成失败项」按钮显示条件是 `failed > 0 && status ∉ {queued, running}` → 因为 `failed=0` 且 `status=running` → 不出。
   - 单图「重试」按钮显示条件是 `item.status === "failed"` → 僵尸 `running` 图也不出。
4. 结果：用户看着 11/11 里卡着 10 张永远不动的图，没有任何手动入口，只能删整个任务重跑。

另一个独立痛点：对 `done` 的图不满意（Gemini 图像生成抽卡严重）时，用户无法重生成单张，只能删整个任务。

## 非目标（明确不做）

- **不做自动恢复**。不扩大 `image_translate_runner.resume_inflight_tasks()` 的扫描范围；不新增任何启动期 DB 扫表批量拉起的逻辑。
  - 理由：2026-04-17 已发生过上游限流叠加自动恢复触发 retry 风暴、宿主机 watchdog 重启 VM 的事故（`_RATE_LIMIT_*` 熔断是当时加的）。用户明确要求"不做自动修复，会把服务器搞崩"。
- 不做任务级的"继续翻译"单独入口（改为复用加强后的重试）。

## 方案

### ① 后端：暴露 runtime 活跃状态、放宽重试范围

**`_state_payload()` 追加字段**

```python
"is_running": image_translate_runner.is_running(task_id),
```

进程内存级互斥，重启后任何 task 都是 `False`，天然识别"僵尸 running"。不持久化，不参与恢复。

**`/api/image-translate/<task_id>/retry/<idx>` 校验放宽**

当前：仅 `item.status == "failed"` 可重试，否则 409。

改为：
- 若 `image_translate_runner.is_running(task_id)` → 409 `{"error": "任务正在跑，等跑完再重试"}`。
- 否则放宽到任意 `item.status`（含 `done` / `running` / `pending` / `failed`）。
- 重试前，若 `item.dst_tos_key` 非空，`try: tos_clients.delete_object(dst_tos_key)`（失败不阻断重置），防止扩展名变化产生孤儿文件。
- 把 `item.status = "pending"`、`attempts = 0`、`error = ""`、`dst_tos_key = ""`，重算 `progress`，`task.status = "queued"`，然后 `_start_runner`。

**`/api/image-translate/<task_id>/retry-failed` → 改为 `/retry-unfinished`**

- 路由保留 `/retry-failed` 做兼容（未来清理），新建 `/retry-unfinished`，作用范围从 "status == failed" 扩到"所有非 `done`"（pending / running / failed 全重置为 pending）。
- 若 `is_running(task_id)` → 409。
- 其余逻辑与现 `retry-failed` 相同（清 dst key、重算 progress、置 queued、start）。

### ② 前端：按钮显示条件 + 样式

**`_state_payload()` 新字段 → 渲染用**

```js
const isRunning = state.is_running === true;
```

**全局「重试未完成的图片」按钮**

- 显示条件：`done < total`（任务还没跑完）。
- 启用条件：`!isRunning`（runtime 活跃时禁用，tooltip "任务正在跑，等跑完再重试"）。
- 调用：`POST /api/image-translate/<id>/retry-unfinished`。
- 位置：「进度」卡片主操作位，与进度条同行右侧（替换原「一键重新生成失败项」）。
- 样式：`.btn-primary`，`height: 40px`、`padding: 8px 20px`、`font-size: 15px`、`font-weight: 600`。约为原按钮 2× 视觉面积。
- 文案：「重试未完成的图片」。

**单图重试按钮**

- 显示条件：**所有 item 都显示**（包括 `done`）。
- 启用条件：`!isRunning`（runtime 活跃时禁用 + tooltip）。
- 调用：`POST /api/image-translate/<id>/retry/<idx>`。
- 样式：宽 64px、高 32px、海洋蓝主色描边（次级按钮风格），字号 13px。
- 文案：`done` 图显示「重新生成」；其他状态显示「重试」。

**Socket 事件**

`is_running` 是进程内存，后端变更时没有事件推送。前端每次刷新 state 都会拿到最新值；`item_updated` 事件到达时会触发 `refresh()` 重新拉 state，所以按钮状态随 socket 自动对齐。

### ③ 不动的部分

- `image_translate_runner.resume_inflight_tasks()` 保持不变（仅恢复 `queued`/`running` 状态且有未完成 item 的任务；进程 crash 中途仍有自动拉起能力，但范围不扩大）。
- `web/app.py:_run_startup_recovery()` 里的调用保持不变。
- Runtime 熔断 (`_CircuitOpen`) 逻辑不动。
- `/retry-failed` 路由保留做兼容（待后续清理），前端不再调用。

## 数据流

```
服务重启 → task.status=running, item.status=running/pending, progress.failed=0
      ↓
用户打开详情页 → GET /api/image-translate/<id> → is_running=false (内存里无此 task)
      ↓
前端：done<total → 全局「重试未完成的图片」按钮显示并可点
      ↓
用户点击 → POST /retry-unfinished → is_running 仍 false → reset 全部 !done → start()
      ↓
runtime 线程启动 → is_running=true → 按钮自动变灰
      ↓
socketio 推 item_updated/progress → 前端刷 state → 按钮可见性/可用性随新状态更新
```

## 错误处理

- `delete_object` 失败：仅 `logger.warning`，不阻断重置，孤儿文件可接受。
- `start_runner()` 在 `is_running==true` 时返回 `False`：接口层本来就先查 `is_running`，属于竞态下的保底；若发生则回 409。
- 用户快速双击：前端点击后立刻 `disabled`，直到 response 返回；后端靠 `is_running` 互斥兜底。

## 测试

新增 / 修改测试点：
1. `/retry/<idx>`：`status=done` 可触发、`status=running` 在 `is_running=false` 时可触发、`is_running=true` 时 409。
2. `/retry-unfinished`：`is_running=false` 时把所有非 done 重置、`is_running=true` 时 409；全 done 时无可重置项，409。
3. `_state_payload` 包含 `is_running` 字段。
4. 前端按钮显示/禁用条件：`is_running=true` 时禁用、`done<total` 全局可见、单图全状态可见。

## 触碰文件

- `web/routes/image_translate.py`：`_state_payload`、`/retry/<idx>`、新增 `/retry-unfinished`。
- `web/templates/image_translate_detail.html`：按钮 id / 文案 / 位置。
- `web/templates/_image_translate_scripts.html`：渲染条件、事件绑定、`isRunning` 处理。
- `web/templates/_image_translate_styles.html`（或新增 CSS 片段）：主按钮加大样式、单图按钮样式。
- `tests/test_image_translate_routes.py`：新增 / 改动用例。

## 回滚方案

改动均为 UI + 路由扩展，DB schema 不变。回滚方式：`git revert` 即可；老任务的 state_json 不需要迁移。
