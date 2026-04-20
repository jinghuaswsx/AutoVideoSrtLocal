# 字幕移除「擦除类型」进阶选项设计

> 日期：2026-04-20
> 状态：已与用户确认，待进入实现计划
> 关联前置设计：[2026-04-15-subtitle-removal-design.md](2026-04-15-subtitle-removal-design.md)

## 1. 背景与目标

当前字幕移除模块（`appcore/subtitle_removal_*`、`web/routes/subtitle_removal.py` 等）在提交 Provider 任务时**没有**显式传 `Operation.Task.Erase.Auto.Type`，等同于走 Provider 默认的 `Subtitle` 模式——只会擦除被识别为「字幕」的文本。

而 Provider API 还支持进阶用法：把 `Auto.Type` 从 `Subtitle` 改为 `Text`，可以连带擦除水印、视频标题等**所有渲染文本**。用户希望把这个能力开放到前端，作为用户可选的「进阶擦除逻辑」。

### 目标

- 用户在提交任务时可以选择「仅字幕」（默认）或「所有渲染文本」两种擦除类型
- 详情页、任务列表都能看到任务实际使用的擦除类型
- 重提（resubmit）时允许重新选择类型

## 2. 范围与非目标

### 2.1 本期范围

- 任务状态新增字段 `erase_text_type`（`subtitle` / `text`），写入 `state_json`，不动 DB schema
- Provider 适配层 `submit_task()` 支持按 `erase_text_type` 构造 `Operation.Task.Erase.Auto.Type` payload
- 详情页「提交去字幕任务」按钮上方加一组两个并列 radio 选项
- 详情页状态面板由两格扩为三格，新增「擦除类型」
- 任务列表页表头加一列「擦除类型」
- `POST /submit`、`POST /resubmit` 接口接收可选字段 `erase_text_type`
- 重提时解禁 radio、默认回填上次选择
- 老任务无字段时按 `subtitle` 兜底

### 2.2 非目标

- 不做批量改类型
- 不做「两种都试」这类组合模式
- 不提供管理员级默认值配置
- 不改 Provider 其它 Erase 参数（`Mode` 仍固定为 `Auto`）
- 不做 DB 迁移脚本或老任务回填（本就没有该概念）

## 3. 数据层改动

### 3.1 任务 state 新增字段

| 字段 | 类型 | 取值 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `erase_text_type` | string | `subtitle` / `text` | `subtitle` | 擦除类型，提交前可改，提交后锁定直到重提 |

字段直接存在 `state_json` 里，不涉及 `projects` 表 schema 变更。

### 3.2 Provider 适配层

`appcore/subtitle_removal_provider.py::submit_task()` 新增关键字参数：

```python
def submit_task(
    *,
    file_size_mb: float,
    duration_seconds: float,
    resolution: str,
    video_name: str,
    source_url: str,
    cover_url: str = "",
    erase_text_type: str = "subtitle",
) -> str:
```

payload 构造规则：

- `erase_text_type == "subtitle"`：payload 保持现状（不传 `operation` 字段），Provider 侧走默认 `Subtitle` 模式，向后兼容
- `erase_text_type == "text"`：payload 额外附加
  ```json
  {
    "operation": {
      "type": "Task",
      "task": {
        "type": "Erase",
        "erase": {
          "mode": "Auto",
          "auto": { "type": "Text" }
        }
      }
    }
  }
  ```
  **Key 命名规范**：现有 `submit_task()` 其它字段均为 `camelCase`（`fileSize` / `videoName` / `notifyUrl` / `taskId`），与 Provider 文档示例中的 `PascalCase`（`Operation.Task.Erase.Auto.Type`）不一致。本次实现沿用**现有 camelCase 风格**（如上所示），与 `biz: "aiRemoveSubtitleSubmitTask"` 保持同一序列化约定。

  实施验证：合入前用一次真实调用（smoke）确认 Provider 接受 camelCase 的 `operation.task.erase.auto.type = "Text"` 能生效，若 Provider 仅接受 PascalCase 则统一改成 PascalCase 并更新本 spec。

枚举外的值 → 抛 `ValueError`，由上游路由转 400。

### 3.3 Runtime

`SubtitleRemovalRuntime._submit()` 从 `task.get("erase_text_type") or "subtitle"` 读出值，作为命名参数传给 `submit_task()`。

## 4. API 契约

### 4.1 `POST /api/subtitle-removal/<id>/submit`

请求 body 追加可选字段：

```json
{
  "remove_mode": "full" | "box",
  "selection_box": { ... },
  "erase_text_type": "subtitle" | "text"   // 新增，缺省 "subtitle"
}
```

校验：取值不在 `{"subtitle", "text"}` → `400`，错误信息 `erase_text_type must be subtitle or text`。

提交成功后写入 `task["erase_text_type"]`。

### 4.2 `POST /api/subtitle-removal/<id>/resubmit`

同 `submit`，接收并覆盖 `erase_text_type`。缺省值沿用「`subtitle`」（与 submit 保持一致，不隐式复制旧值——前端负责回填）。

### 4.3 `GET /api/subtitle-removal/<id>`

state payload 新增字段：

```json
{
  ...,
  "erase_text_type": "subtitle"
}
```

### 4.4 `GET /api/subtitle-removal/list`

每条 item 新增：

```json
{
  ...,
  "erase_text_type": "subtitle"
}
```

## 5. 前端改动

### 5.1 详情页：`web/templates/subtitle_removal_detail.html`

在控制面板 `.sr-control-card` 内、`#srSubmitSubtitleRemoval` 按钮**上方**插入一个「擦除类型」分组：

```
擦除类型
┌─────────────────────────┐  ┌─────────────────────────┐
│ ◉ 仅字幕                │  │ ○ 所有渲染文本          │
│   只擦除识别为字幕的区域 │  │   字幕 + 水印、标题等   │
└─────────────────────────┘  └─────────────────────────┘
```

**交互规则：**

| 任务状态 | radio 状态 | 默认值 |
| --- | --- | --- |
| `uploaded` / `ready`（未提交） | 可选 | `subtitle` |
| `queued` / `running` / `submitted` | 禁用、只读显示当前值 | 当前 `erase_text_type` |
| `done` / `error` | 禁用（除非用户点「重提」） | 当前 `erase_text_type` |
| 点击「重提」后 | 解禁 | 回填 `erase_text_type` |

**样式：**

- 外层 `.sr-erase-type-group`，内部两张 `.sr-erase-type-option` 卡片并列
- 卡片内边距 `--space-3` / `--space-4`，圆角 `--radius-md`，边框 `1px solid var(--border-strong)`
- 选中卡片：边框换 `--accent`，背景 `--accent-subtle`
- 禁用状态：透明度 0.6、`cursor: not-allowed`
- 响应式：< 640px 垂直堆叠（`flex-direction: column`）

**状态面板三格**：现有「任务状态 / 视频分辨率」后追加「擦除类型」，值格式：
- `subtitle` → `仅字幕`
- `text` → `所有渲染文本`
- 空/未提交 → `—`

### 5.2 列表页：`web/templates/subtitle_removal_list.html`（现有列表模板）

表头追加列 `擦除类型`，行内显示同详情页的中文文案（`仅字幕` / `所有渲染文本` / `—`）。响应式处理：与列表页现有次要列（例如分辨率、时长）使用相同的隐藏断点；若当前列表未做列隐藏，则本期不引入新断点，仅在桌面宽度下加这一列。

### 5.3 前端脚本：`web/templates/_subtitle_removal_scripts.html`

- submit / resubmit 前读取当前选中 radio 的 value，带进 request body
- 接收到 state 更新（`erase_text_type` 字段）后：
  - 同步 radio 选中态
  - 按任务状态切换 radio 的 disabled
  - 更新状态面板中的「擦除类型」文案
- 「重提」按钮点击：解禁 radio、回填上次值

## 6. 重提行为

用户重提时：

1. radio 自动解禁、默认回填上次提交用的 `erase_text_type`
2. 用户可立即切换到另一种类型再点「提交」
3. `resubmit` 接口按 body 覆写 state，不假设「沿用原值」
4. 旧结果（`result_video_path` / `result_tos_key`）按现有 `_cleanup_result_artifacts()` 清理

## 7. 兼容性

| 场景 | 行为 |
| --- | --- |
| 老任务 state 无 `erase_text_type` | 前端显示 `仅字幕`；若用户重提，默认 `subtitle` |
| 前端旧版本不传 `erase_text_type` | 后端视为 `subtitle`，Provider payload 走默认路径 |
| Provider 侧无响应差异 | 选 `subtitle` 时 payload 与改动前完全一致，保证回滚零风险 |

## 8. 测试要点

- `tests/test_subtitle_removal_provider.py`
  - 补充：`erase_text_type="subtitle"` 时 POST 到 Provider 的 payload 无 `operation` 字段
  - 补充：`erase_text_type="text"` 时 payload 含 `operation.task.erase.auto.type == "Text"`
  - 非法值抛 `ValueError`
- `tests/test_subtitle_removal_routes.py`
  - `POST /submit`：带 `erase_text_type=text` → state 里写入 `text`
  - `POST /submit`：带非法值 → 400
  - `POST /resubmit`：可改写 `erase_text_type`
  - `GET /<id>` / `GET /list`：返回 `erase_text_type` 字段
- `tests/test_subtitle_removal_runtime.py`
  - runtime 把 task state 里的 `erase_text_type` 正确传给 `submit_task()`（mock Provider 断言参数）

## 9. 不做的事（明示）

- 不加「进阶选项」折叠面板或下拉入口——用户明确选 A 方案，两个 radio 始终可见
- 不做管理员侧默认值覆盖
- 不改 Provider `Mode` 字段（继续固定 `Auto`）
- 不引入 DB schema 迁移
- 不改字幕选区（`full` / `box`）相关逻辑——擦除类型与选区模式正交
