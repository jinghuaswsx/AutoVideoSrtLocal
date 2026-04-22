# 图片翻译任务：串行/并行模式选择

- 日期：2026-04-22
- 状态：Design（待实现）
- 作者：与 Claude 结对设计

## 背景 / 问题

图片翻译 runtime（[appcore/image_translate_runtime.py](../../../appcore/image_translate_runtime.py)）当前是**真·串行（1 并发）**：

```python
# start()
for idx in range(len(items)):
    if items[idx]["status"] in {"done", "failed"}:
        continue
    self._process_one(task, task_id, idx)
```

一张跑完（Gemini 调用 + TOS 上传 + DB 更新 + socket 推送）才跑下一张。单次上限 20 张，最慢要跑 20 次调用，耗时长。

希望在任务**提交时**让用户选择处理模式，保守默认串行，新增"并行"加速档位。

## 目标

1. 任务提交时二选一：**串行（默认）** 或 **并行**
2. 串行语义 = 当前行为 100% 不变（1 并发、重试 3 次、熔断不变）
3. 并行语义 = 单批最多 10 张并发，20 张图分 2 批串行跑
4. 两个入口都要支持：
   - 图片翻译菜单（`/image-translate` 新建任务）
   - 素材编辑弹窗「从英语版一键翻译」
5. 重启恢复 / 重试接口沿用任务创建时的模式，不再让用户重选

## 非目标（YAGNI）

- 不做全局或用户级默认切换（总是串行默认）
- 不做可配置并发数（固定 `_BATCH_SIZE = 10`）
- 不做自适应降级（遇限流不自动切串行）
- 重试接口（`/retry`、`/retry-failed`、`/retry-unfinished`）不新增参数
- 不记忆用户上次选择的模式

## 数据模型

任务 state_json 新增一个字段：

| 字段 | 值 | 默认 | 说明 |
|------|----|------|------|
| `concurrency_mode` | `"sequential"` \| `"parallel"` | `"sequential"` | 处理模式 |

**向后兼容**：
- 老任务 state_json 里没这字段 → runtime 视作 `"sequential"`，等于现在的行为
- `resume_inflight_tasks`（服务重启恢复）自动从 state_json 读回原模式

## Runtime 改造（[appcore/image_translate_runtime.py](../../../appcore/image_translate_runtime.py)）

### 分支入口

```python
def start(self, task_id: str) -> None:
    task = store.get(task_id)
    ...
    mode = (task.get("concurrency_mode") or "sequential").strip().lower()
    circuit_msg = ""
    try:
        if mode == "parallel":
            self._run_parallel(task, task_id)
        else:
            self._run_sequential(task, task_id)
    except _CircuitOpen as exc:
        circuit_msg = str(exc) or "上游持续限流，已熔断"
        ...
```

`_run_sequential` 把现有 `for idx ...` 循环原样挪进来。**零行为变更**。

### 并行路径

```python
_BATCH_SIZE = 10

def _run_parallel(self, task: dict, task_id: str) -> None:
    items = task.get("items") or []
    pending_idxs = [
        i for i, it in enumerate(items)
        if it["status"] not in {"done", "failed"}
    ]
    for batch_start in range(0, len(pending_idxs), _BATCH_SIZE):
        batch = pending_idxs[batch_start : batch_start + _BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=_BATCH_SIZE) as pool:
            futures = [pool.submit(self._process_one, task, task_id, idx)
                       for idx in batch]
            # 等全部完成；_process_one 内部吞所有异常（只抛 _CircuitOpen 穿透）
            for fut in as_completed(futures):
                fut.result()  # 让 _CircuitOpen 向外传
```

`_CircuitOpen` 是唯一会向外穿透的异常类型（已存在）。
- 触发后：`with ThreadPoolExecutor` 语句的 `__exit__` 会 `shutdown(wait=True)`，等正在跑的线程落地
- Python 3.9+ `executor.shutdown(wait=False, cancel_futures=True)` 可以显式取消未启动 future，但 `with` 语句默认 wait=True 更简单可靠，10 并发且各自单次调用 ≤ 几十秒，不加 cancel
- 异常穿透到 `start()` 的 except，走现有 `_abort_remaining_items` 清理

### 并发安全

多线程共享状态必须保护。加一把互斥锁 `self._state_lock = threading.Lock()`，管：

| 共享点 | 处理 |
|--------|------|
| `task["items"][idx]` 各字段写 | 天然隔离（线程各写自己 idx），不加锁 |
| `_update_progress(task)` 统计全量 items | `_state_lock` 内调 |
| `store.update(task_id, items=..., progress=...)` | `_state_lock` 内调（和上一条同一个 with 块） |
| `self._rate_limit_hits` deque 读写 | `_state_lock` 内操作 |
| `self.bus.publish(...)` | SocketIO emit 本身线程安全，不加锁 |

实现方式：在 `_process_one` 里每次"写 item 状态 → 重算 progress → store.update → emit" 的那几组相邻操作包到 `with self._state_lock:` 里。`bus.publish` 不在锁内，避免 I/O 阻塞其他线程。

> 串行路径也用同一把锁（走 with 语句但无竞争）；代码一份、复杂度 -1。

### 熔断在并行下的表现

- `_RATE_LIMIT_THRESHOLD = 5` / `_RATE_LIMIT_WINDOW_SEC = 60` 不变
- 并行下 10 个线程更容易在短时间内凑出 5 次 429/5xx → 这是**设计内行为**：并行本就更激进，保护阈值不随模式变
- 熔断后行为：当前批里已在跑的线程结束（单次 Gemini 调用），不再进下一批，剩余 items 走 `_abort_remaining_items` 标 failed

## API 层

### 入口 1：图片翻译菜单

路由：[web/routes/image_translate.py](../../../web/routes/image_translate.py) 的 `api_upload_complete`（约 195 行）

接受新字段：

```python
mode = (body.get("concurrency_mode") or "sequential").strip().lower()
if mode not in {"sequential", "parallel"}:
    return jsonify({"error": "concurrency_mode 必须是 sequential 或 parallel"}), 400
...
task_state.create_image_translate(
    task_id, task_dir,
    ...
    concurrency_mode=mode,
)
```

### 入口 2：素材编辑一键翻译

路由：[web/routes/medias.py](../../../web/routes/medias.py) 的 `api_detail_images_translate_from_en`（约 1620 行）

同样的解析 + 校验 + 透传。

### task_state 扩展

[appcore/task_state.py](../../../appcore/task_state.py) 的 `create_image_translate(...)` 新增 kwarg：

```python
def create_image_translate(
    task_id: str,
    task_dir: str,
    *,
    ...
    concurrency_mode: str = "sequential",
) -> None:
    ...
    state["concurrency_mode"] = concurrency_mode
```

### 重试接口不改

`/retry/<idx>`、`/retry-failed`、`/retry-unfinished` 三个接口**不加参数**。重启 runner 时读任务现有 `concurrency_mode` 继续用。

### 向后兼容

| 情况 | 行为 |
|------|------|
| 请求不带 `concurrency_mode` | 默认 `sequential` |
| 非法值（如 `"fast"`） | 400，`concurrency_mode 必须是 sequential 或 parallel` |
| 老任务 state 无此字段 | Runtime 视作 `sequential` |

## UI 层

### 入口 1：图片翻译菜单（[web/templates/image_translate_list.html](../../../web/templates/image_translate_list.html)）

在「使用模型」pill 组下面、「提示词」上面插一组 pill（复用现有 `.it-pill` / `.it-pill-group` 样式）：

```html
<div class="form-row">
  <label>处理模式</label>
  <div id="itConcurrencyPills" class="it-pill-group" role="radiogroup">
    <button type="button" class="it-pill is-active" data-value="sequential"
            role="radio" aria-checked="true">串行（默认）</button>
    <button type="button" class="it-pill" data-value="parallel"
            role="radio" aria-checked="false">并行</button>
  </div>
  <p class="hint">串行：一张一张跑，稳。并行：单批最多 10 张同时跑，快但对上游限流更敏感。</p>
  <input type="hidden" id="itConcurrencyMode" value="sequential">
</div>
```

JS（放在 [web/templates/_image_translate_scripts.html](../../../web/templates/_image_translate_scripts.html)）：
- 绑定 pill click → 切换 `is-active` → 更新 `#itConcurrencyMode`
- 提交 `api_upload_complete` 时 body 带 `concurrency_mode: <hidden 值>`

### 入口 2：素材编辑一键翻译（[web/templates/_medias_edit_detail_modal.html](../../../web/templates/_medias_edit_detail_modal.html)）

改造 `edDetailTranslateTaskMask` modal。**关键变化**：从"点按钮直接调 API + modal 展示结果"改成"点按钮 → modal 展示选择 + 开始翻译按钮 → 点按钮才调 API"。

modal body 分两态，用 `hidden` 互斥切换：

**态 1（配置态，默认）**

用 medias 设计体系里现有的 `.oc-chip` + `.on` 状态（`.oc-chip` 在 [web/templates/medias_list.html](../../../web/templates/medias_list.html) 里定义，当前用于 filter chip）：

```html
<div id="edDetailTranslateTaskConfig" style="display:grid;gap:var(--oc-sp-3);">
  <div>
    <div style="font-size:13px;font-weight:600;margin-bottom:var(--oc-sp-2);">处理模式</div>
    <div id="edDetailTranslateModeGroup" style="display:flex;gap:var(--oc-sp-2);flex-wrap:wrap;">
      <button type="button" class="oc-chip on" data-mode="sequential"
              role="radio" aria-checked="true">串行（默认）</button>
      <button type="button" class="oc-chip" data-mode="parallel"
              role="radio" aria-checked="false">并行</button>
    </div>
    <p class="oc-hint">串行稳；并行单批 10 张同时跑，遇限流更容易熔断。</p>
  </div>
  <div id="edDetailTranslateTaskMeta" class="oc-hint"></div>
</div>
<div style="display:flex;justify-content:flex-end;gap:var(--oc-sp-2);margin-top:var(--oc-sp-4);">
  <button class="oc-btn ghost" id="edDetailTranslateCancelBtn">取消</button>
  <button class="oc-btn primary" id="edDetailTranslateStartBtn">开始翻译</button>
</div>
```

**态 2（结果态，创建成功/失败后）**

保留现有 `edDetailTranslateTaskMsg` + `edDetailTranslateTaskLink` 块作为结果展示，JS 在成功 / 失败时隐藏 `#edDetailTranslateTaskConfig` 和底部按钮、显示结果块。

JS 改造（[web/static/medias.js](../../../web/static/medias.js) 的 `edStartDetailTranslate` 附近）：

1. **点击「从英语版一键翻译」按钮**：`edOpenDetailTranslateTaskModal()` 改为打开 modal 并重置到"态 1"，chip 复位 `sequential`
2. **chip 点击**：单选互斥（选中的加 `.on`，同组其它去掉）
3. **点「开始翻译」按钮**：读 chip 当前值，POST `/medias/api/products/{pid}/detail-images/translate-from-en` body 带 `{ lang, concurrency_mode }`
4. **成功 / 失败**：切到态 2，沿用现在的 `edDetailTranslateTaskMsg` / `edDetailTranslateTaskLink` 行为
5. **取消 / 关闭 ×**：关 modal，不调 API

### 不做

- 不动其它控件位置
- 不做偏好记忆

## 测试

### 单元测试（pytest）

放在 [tests/test_image_translate_runtime.py](../../../tests/test_image_translate_runtime.py)：

1. **串行回归**：现有测试全通过（保护零行为变更）
2. **并行基础**：20 个 item，mock `_process_one` 使其各 sleep 50ms，assert 总耗时明显 < 串行（< 300ms 以内），每个 item 都被调用
3. **并行分批**：mock `_process_one` 记录调用时间戳，assert 前 10 个几乎同时启动（差 < 50ms），后 10 个在前 10 完成之后启动
4. **并行熔断**：mock `_process_one` 让前 5 个全抛 `GeminiImageRetryable`，assert 触发 `_CircuitOpen`、剩余 items 全部标 failed
5. **混合状态**：items 部分已 done/failed，并行模式只处理剩余（跳过终态）
6. **state_lock 写入一致性**：并行跑完后 `task["items"]` 所有状态正确、`progress` 自洽

API 测试（[tests/test_image_translate_routes.py](../../../tests/test_image_translate_routes.py)）：

7. `api_upload_complete` 接受 `concurrency_mode="parallel"`，任务 state 里正确保存
8. `api_upload_complete` 不带字段 → state 存 `sequential`
9. `api_upload_complete` 非法值 → 400
10. 素材编辑 `api_detail_images_translate_from_en` 同样 3 个用例

### 手测清单

- [ ] 图片翻译菜单：选串行 → 创建 20 张任务 → 观察确实串行
- [ ] 图片翻译菜单：选并行 → 创建 20 张任务 → 观察确实并行（10 个一起跑）
- [ ] 素材编辑 modal：打开后默认串行 pill；切并行；点开始翻译；成功切态 2
- [ ] 素材编辑 modal：点取消按钮、× 都能正确关闭，不发请求
- [ ] 重启服务中：并行任务 resume 后继续并行
- [ ] 任务重试（retry-unfinished）沿用原模式

## 回滚

设计完全向后兼容：
- 不传 / 空值 / 老任务 → 走串行（现行行为）
- 回滚只需 revert PR，无需数据迁移
- state_json 里的 `concurrency_mode` 字段即使残留也不影响老代码（会被忽略）

## 变更范围与规模估计

| 文件 | 变更 | 估算 |
|------|------|------|
| `appcore/image_translate_runtime.py` | 核心改造 | ~60 行 |
| `appcore/task_state.py` | 加字段 kwarg | ~3 行 |
| `web/routes/image_translate.py` | API 校验 | ~5 行 |
| `web/routes/medias.py` | API 校验 | ~5 行 |
| `web/templates/image_translate_list.html` | pill UI | ~12 行 |
| `web/templates/_image_translate_scripts.html` | pill JS + 提交带参 | ~15 行 |
| `web/templates/_medias_edit_detail_modal.html` | modal 配置态 | ~25 行 |
| `web/static/medias.js` | modal 两态切换 + 按钮 | ~40 行 |
| `tests/test_image_translate_runtime.py` | 并行/熔断用例 | ~80 行 |
| `tests/test_image_translate_routes.py` | API 用例 | ~30 行 |

**总计**：~275 行新增 / 小量修改，跨 10 文件。**超出 hotfix 定义，必须走 worktree 实现**。
