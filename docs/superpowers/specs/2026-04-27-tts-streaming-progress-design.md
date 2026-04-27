# TTS 步骤流式进度 + 通用折叠卡片

**日期**：2026-04-27
**作者**：Claude Code（与用户共识后落地）
**关联模块**：`appcore/runtime.py`、`appcore/runtime_ja.py`、`pipeline/tts.py`、`web/templates/_task_workbench.html`、`web/templates/_task_workbench_scripts.html`、`web/templates/_task_workbench_styles.html`

---

## 1. 背景与问题

任务详情页的「语音生成」步骤当前体验：

- 进入 TTS 步骤后，step-msg 区域固定显示 `正在生成英语配音...`，从首轮开始到所有迭代结束都不变。
- 步骤卡片下方的 **Duration Log（时长收敛迭代）** 容器在首轮 `tts_script_regen` 事件到达前是 `hidden`，先经历几百毫秒的空白。
- Duration Log 显示后，`tts_script_regen`（LLM 切分朗读文案，5–30s）和 `audio_gen`（ElevenLabs 串行配音，30s–2 分钟）这两段没有任何子进度。30s 视频 10–20 块、每块 1–5s 的 ElevenLabs 调用现状是黑盒。

用户实际体感：**整段 TTS 步骤要等 1–3 分钟，期间页面只有一行不变的"正在生成英语配音…"，会怀疑卡死**。

## 2. 目标

在**不重排现有 UI 区块**的前提下，把 TTS 步骤里"现在到底在做什么"实时透出来：

1. step-msg 那一行随子任务实时刷新文案。
2. Duration Log 当前 round 卡片里能看到 ElevenLabs **per-segment** 进度计数。
3. Duration Log 顶部加一个折叠/展开按钮——并把它做成**通用 collapsible-card 模式**，后续其他卡片可以直接复用。

非目标（明确不做）：

- 不接 LLM streaming API（`tts_script_regen` / `translate_rewrite` 单次 LLM 调用还是黑盒，能看到"正在做"即可）。
- 不重排步骤卡片之间的相对位置 / 间距 / 层级。
- 不新增 socket event 类型——所有新事件复用 `EVT_STEP_UPDATE` 和 `EVT_TTS_DURATION_ROUND`。

## 3. 总体方案

走"密度更高的就地刷新"路线（A 方案）：

- **顶部 step-msg** —— 复用 `EVT_STEP_UPDATE`，每次子任务切换重发一次，文案模板：
  ```
  正在生成{lang}配音 · {sub_task}
  ```
- **Duration Log per-segment** —— 复用 `EVT_TTS_DURATION_ROUND`，phase 仍为 `audio_gen`，record 里加 `audio_segments_done` / `audio_segments_total` 两个新字段；前端 `_phaseLabel` 渲染时拼上计数。
- **折叠按钮** —— 抽成通用的 `collapsible-card` CSS class + 一段全局 JS 行为。Duration Log 是首个使用者；其他卡片通过添加同一组 class / data 属性即可加入。

## 4. 后端改动

### 4.1 `pipeline/tts.py:90` `generate_full_audio`

新增可选 `on_segment_done` 回调：

```python
from typing import Callable, Optional

def generate_full_audio(
    segments: List[Dict],
    voice_id: str,
    output_dir: str,
    *,
    variant: str | None = None,
    elevenlabs_api_key: str | None = None,
    model_id: str = "eleven_turbo_v2_5",
    language_code: str | None = None,
    on_segment_done: Optional[Callable[[int, int, dict], None]] = None,
) -> Dict:
    ...
    for i, seg in enumerate(segments):
        ...
        generate_segment_audio(...)
        duration = _get_audio_duration(seg_path)
        ...
        if on_segment_done is not None:
            try:
                on_segment_done(i + 1, len(segments), {
                    "segment_index": i,
                    "tts_duration": duration,
                    "tts_text_preview": (text or "")[:60],
                })
            except Exception:
                # 进度回调出错不能影响 TTS 主流程
                log.exception("on_segment_done callback raised")
    ...
```

**契约**：

- 回调签名固定为 `(done: int, total: int, info: dict) -> None`。`done` 从 1 计数到 `total`。
- `info` 现阶段仅放置 `segment_index` / `tts_duration` / `tts_text_preview`（前 60 字符），后续可以扩展。
- 回调抛错被静默捕获，不影响主流程。

### 4.2 `appcore/runtime.py` 新增 `_emit_substep_msg`

紧挨现有 `_set_step`（`appcore/runtime.py:431`）加一个轻量帮手：

```python
def _emit_substep_msg(self, task_id: str, step: str, sub_msg: str) -> None:
    """Emit EVT_STEP_UPDATE with a refreshed message but DO NOT persist to disk.

    Use for high-frequency sub-step progress (per ElevenLabs segment, etc.)
    where persisting every event would thrash task_state.
    """
    task = task_state.get(task_id) or {}
    status = (task.get("steps") or {}).get(step, "running")
    payload = {"step": step, "status": status, "message": sub_msg}
    existing_tag = (task.get("step_model_tags") or {}).get(step, "")
    if existing_tag:
        payload["model_tag"] = existing_tag
    self._emit(task_id, EVT_STEP_UPDATE, payload)
```

**关键差异 vs `_set_step`**：

| | `_set_step` | `_emit_substep_msg` |
|--|--|--|
| 写 `task_state.set_step(...)` | 是 | 否 |
| 写 `task_state.set_step_message(...)` | 是 | 否 |
| 发 socket 事件 | 是 | 是 |

不写盘的代价：刷新页面会回退到上次落盘的 step-msg（通常是初始的 `正在生成英语配音...`），下一个事件会立刻盖上来。这对于 1–5s 间隔的 segment 进度可以接受。

### 4.3 `_step_tts` 入口处的子步骤标记

在 `appcore/runtime.py:1510` 的 `self._set_step(task_id, "tts", "running", ...)` 之后插入：

```python
self._emit_substep_msg(task_id, "tts",
    f"正在生成{lang_display}配音 · 加载配音模板")
```

后续的 `resolve_key`、`_resolve_voice`、`build_timeline_manifest` 这类纯本地操作不需要单独打 substep（毫秒级）。

### 4.4 `_run_tts_duration_loop` 子步骤接入

在 `appcore/runtime.py:468` 的循环里：

| 现有 emit | 旁边新增的 substep |
|----------|-------------------|
| `_emit_duration_round(..., "translate_rewrite")` | `第 N 轮 · 重写译文 attempt M/5（目标 X 词）` —— 每次 attempt 进入循环前调一次 |
| `_emit_duration_round(..., "tts_script_regen")` | `第 N 轮 · 切分朗读文案中` |
| `_emit_duration_round(..., "audio_gen")` | `第 N 轮 · 生成 ElevenLabs 音频 0/{total}` |
| —（per-segment 新增） | `第 N 轮 · 生成 ElevenLabs 音频 X/{total}` |
| —（拼接新增） | `第 N 轮 · 音频拼接中` |
| `_emit_duration_round(..., "measure")` | `第 N 轮 · 校验语言 / 测量时长` |
| `_emit_duration_round(..., "converged")` | `第 N 轮收敛 · 拼接最终音频` |
| `_emit_duration_round(..., "best_pick")` | `5 轮未精确收敛 · 选最接近第 N 轮` |
| `_emit_duration_round(..., "truncate_audio")` | `裁剪音频尾部 → {final_target_hi}s` |

### 4.5 per-segment 同时更新 round 字段

在传给 `generate_full_audio` 的回调里同时做两件事：

```python
def _on_segment_done(done, total, info):
    # 1) 顶部 step-msg 实时刷新
    self._emit_substep_msg(
        task_id, "tts",
        f"正在生成{lang_display}配音 · 第 {round_index} 轮 · 生成 ElevenLabs 音频 {done}/{total}",
    )
    # 2) Duration Log 当前 round 卡片字段更新
    round_record["audio_segments_done"] = done
    round_record["audio_segments_total"] = total
    self._emit_duration_round(task_id, round_index, "audio_gen", round_record)
```

**注意**：这里**不**调 `task_state.update(tts_duration_rounds=...)`——避免每段一次磁盘写入。round 字段只走 socket，刷新页面后从落盘 JSON 读回，丢的是中间某次 segment 进度，无影响。`measure` phase 之后该 round 仍会被 `task_state.update` 持久化一次，最终态是完整的。

### 4.6 `appcore/runtime_ja.py` 同步改造

日语流水线（`appcore/runtime_ja.py:240` 起）有自己的 TTS 入口和 duration loop（不继承 `_run_tts_duration_loop`）。同样模式接 `on_segment_done` + `_emit_substep_msg` + `audio_segments_done/total` 字段。其他流水线（`runtime_de` / `runtime_fr` / `runtime_multi` / `runtime_omni`）都继承自基类 `Runtime`，自动跟着升级。

## 5. 前端改动

### 5.1 step-msg 自动刷新

`renderStepMessages()` 已经把 `task.step_messages.tts` 渲染到 `#msg-tts`，`socket.on(EVT_STEP_UPDATE, ...)` 也已经更新 step-msg DOM。**不需要前端改动**——后端发什么，UI 显示什么。

### 5.2 `_phaseLabel` 支持 audio_gen 计数

`web/templates/_task_workbench_scripts.html:1176` 改造：

```javascript
function _phaseLabel(phase, round) {
  if (phase === 'audio_gen' && round && round.audio_segments_total) {
    const done = round.audio_segments_done || 0;
    return `正在生成 TTS 音频 ${done}/${round.audio_segments_total}`;
  }
  return ({
    translate_rewrite: '正在重写译文',
    tts_script_regen:  '正在切分朗读块',
    audio_gen:         '正在生成 TTS 音频',
    measure:           '测量音频时长',
    // ...
  })[phase] || phase || '';
}
```

调用点 `_task_workbench_scripts.html:1549` 同步从 `_phaseLabel(round.__current_phase)` 改成 `_phaseLabel(round.__current_phase, round)`。

### 5.3 通用 `collapsible-card` 模式

#### 5.3.1 HTML 标记约定

任意卡片要变成可折叠，按这个结构组装即可：

```html
<div class="collapsible-card" data-collapsible="duration-log">
  <div class="collapsible-header">
    <!-- 折叠态保留可见的内容（标题、状态标签等） -->
    <span class="collapsible-title">翻译本土化 · 时长控制迭代</span>
    <span class="duration-status-tag running">运行中</span>
    <button type="button" class="collapsible-toggle"
            aria-label="展开/折叠"
            aria-expanded="true">
      <!-- chevron SVG -->
    </button>
  </div>
  <div class="collapsible-body">
    <!-- 折叠时被隐藏的所有详情 -->
  </div>
</div>
```

**约定**：

- `data-collapsible="<key>"` 是 localStorage 命名空间。Duration Log 用 `duration-log`；后续卡片各取唯一 key（如 `quality-assessment`、`subtitle-blocks`）。
- `.collapsible-header` 永远可见。点 header 任意位置或 toggle 按钮均可切换。
- `.collapsible-body` 是折叠对象。

#### 5.3.2 CSS（追加到 `web/templates/_task_workbench_styles.html`）

> 现有 task workbench 的样式都集中在这个 Jinja partial 的 `<style>` 块里（`.duration-log` 在第 481 行），新增 collapsible 样式紧挨已有 duration-log 块写。

```css
.collapsible-card[data-collapsed="true"] > .collapsible-body {
  display: none;
}

.collapsible-card .collapsible-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  cursor: pointer;
  user-select: none;
}

.collapsible-card .collapsible-toggle {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--fg-muted);
  cursor: pointer;
  transition: transform var(--duration) var(--ease-out),
              background-color var(--duration-fast) var(--ease);
}

.collapsible-card .collapsible-toggle:hover {
  background: var(--bg-muted);
  color: var(--fg);
}

.collapsible-card[data-collapsed="true"] .collapsible-toggle {
  transform: rotate(-90deg);
}
```

> 颜色全部走 CLAUDE.md 里 Ocean Blue token，hue 严格 ≤ 240。

#### 5.3.3 JS 行为

写一个全局 `bootCollapsibleCards(rootEl, taskId)`，挂在 `window` 上，在 `renderTaskState()` 末尾调用一次：

```javascript
function bootCollapsibleCards(root = document, taskId = '') {
  root.querySelectorAll('.collapsible-card[data-collapsible]').forEach(card => {
    if (card.dataset.collapsibleBound === '1') return;
    card.dataset.collapsibleBound = '1';
    const key = card.dataset.collapsible;
    const storageKey = `collapsibleCard:${key}:${taskId || 'global'}`;
    const stored = localStorage.getItem(storageKey);
    if (stored === '1') card.dataset.collapsed = 'true';

    const toggle = (ev) => {
      // 不要拦截 header 内的链接 / 按钮
      if (ev && ev.target.closest('a, button:not(.collapsible-toggle), input, select')) return;
      const collapsed = card.dataset.collapsed === 'true';
      card.dataset.collapsed = collapsed ? 'false' : 'true';
      localStorage.setItem(storageKey, collapsed ? '0' : '1');
      const btn = card.querySelector('.collapsible-toggle');
      if (btn) btn.setAttribute('aria-expanded', collapsed ? 'true' : 'false');
    };

    const header = card.querySelector('.collapsible-header');
    if (header) header.addEventListener('click', toggle);
  });
}
```

#### 5.3.4 Duration Log 接入

把 `renderTtsDurationLog()` 输出 HTML 的根容器从 `<div class="duration-log">` 改成：

```html
<div class="duration-log collapsible-card" data-collapsible="duration-log">
  <div class="collapsible-header duration-log-header">
    <span class="collapsible-title">翻译本土化 · 时长控制迭代（Duration Loop）</span>
    ${statusTag}
    ${modelTag}
    <span class="meta">${metaStr}</span>
    <button type="button" class="collapsible-toggle" aria-label="展开/折叠" aria-expanded="true">
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
        <path d="M4 6l4 4 4-4" stroke="currentColor" stroke-width="1.5"
              stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
    </button>
  </div>
  <div class="collapsible-body">
    <!-- sparkline + rounds + 最终摘要 -->
  </div>
</div>
```

`renderTtsDurationLog()` 末尾调一次 `bootCollapsibleCards(container.parentNode, taskId)`，触达新生成的 toggle。

#### 5.3.5 默认状态 & 持久化

- 首次访问任务（无 localStorage 记录）：默认**展开**。
- 用户折叠 → `localStorage['collapsibleCard:duration-log:<taskId>'] = '1'`，刷新保持折叠。
- 用户展开 → `localStorage[...] = '0'`。
- 不同 taskId 之间互不影响（避免 A 任务折了 B 任务也折）。

## 6. 文件改动清单

| 文件 | 改动概要 |
|------|----------|
| `pipeline/tts.py:90` | `generate_full_audio` 加 `on_segment_done` 回调 |
| `appcore/runtime.py:431` | 加 `_emit_substep_msg` |
| `appcore/runtime.py:1510` | TTS 入口加"加载配音模板"子步骤 |
| `appcore/runtime.py:468` | duration loop 各 phase 旁挂子步骤 + per-segment 回调 |
| `appcore/runtime_ja.py:240` | 日语流水线同步加同一套子步骤 + per-segment |
| `web/templates/_task_workbench_scripts.html:1480` | `renderTtsDurationLog` 输出新的 `collapsible-card` 结构 |
| `web/templates/_task_workbench_scripts.html:1176` | `_phaseLabel(phase, round)` 支持 audio_gen 动态计数 |
| `web/templates/_task_workbench_scripts.html:1549` | `_phaseLabel` 调用点传第 2 参数 |
| `web/templates/_task_workbench_scripts.html` 末尾 | 新增 `bootCollapsibleCards` 全局函数 + 在 `renderTaskState` / `renderTtsDurationLog` 末尾调用 |
| `web/templates/_task_workbench_styles.html:481` 附近 | 追加 `.collapsible-card`、`.collapsible-header`、`.collapsible-toggle` 样式（沿用 Ocean Blue token） |

## 7. 风险与取舍

| 风险 | 评估 | 处理 |
|------|------|------|
| socket 事件高频（per-segment 50–80 条/任务） | SocketIO 没问题；前端 `_upsertDurationRound` 每次重渲整个 Duration Log，但 DOM 量小（<50 节点），实测应该流畅 | 先不节流，性能不达标再加 `requestAnimationFrame` |
| substep msg 不写盘 → 刷新回退 | 可接受：下一个事件 1–5s 内会盖上来 | 在文案上不依赖"上次到哪了"，每条事件都是自包含的 |
| `audio_gen` 中途 ElevenLabs 失败 | 现有错误处理保持不变；最后一次 segment 计数停在 X/Y，phase 仍是 `audio_gen`，最终被 `_set_step("tts", "error", ...)` 覆盖 | 不需要额外处理 |
| `on_segment_done` 回调抛错破坏主流程 | `try/except` 捕获 + `log.exception` | 已在 4.1 设计 |
| 通用 collapsible-card 与 step 卡片样式冲突 | step 卡片用 `<div class="step">`，与 `.collapsible-card` 不在同一层；目前没有交叉 | 后续 step 卡片要折叠时再单独评估 |
| localStorage 配额耗尽 | 每个 key < 50 字节，1000 个任务 ≈ 50KB，远低于 5MB 配额 | 不处理 |

## 8. 验证

最小验证集（手动 + 自动）：

1. **新单元测试**：`tests/test_tts_pipeline_callback.py`（新增）—— 验证 `generate_full_audio` 在每段调一次 `on_segment_done`，参数正确，回调抛错不影响返回值。
2. **现有测试不破坏**：`pytest tests/ -k tts` 全绿。
3. **本地端到端**：跑一条多语种翻译任务（任意短视频），观察：
   - step-msg 实时变化（肉眼能看到从"加载配音模板"→"切分朗读文案"→"生成 ElevenLabs 音频 1/X"…→"测量时长"）
   - Duration Log 当前 round 卡片显示 `正在生成 TTS 音频 5/15`（数字递增）
   - 点击折叠按钮 → 卡片只剩 header；刷新页面 → 仍折叠
   - 切换到另一个任务 → 折叠状态独立
4. **回归**：日语 / 德语 / 法语任务的 TTS 步骤照常完成，duration log 内容不变。

## 9. 范围之外（明确不在本次做）

- LLM streaming 接入（`tts_script_regen` / `translate_rewrite` 流式输出）。
- 给 step 卡片本身加折叠（语音生成 / 字幕生成 / 翻译质量评估等）——这次只做通用 collapsible-card pattern + Duration Log 一个使用点；其他模块复用是后续单独的 PR 工作。
- step-msg 刷新写盘（用户接受刷新页面短暂回退到通用文案）。
