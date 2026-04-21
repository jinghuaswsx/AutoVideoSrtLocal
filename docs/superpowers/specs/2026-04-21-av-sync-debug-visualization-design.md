# 视频翻译音画同步(v2)流程调试可视化设计

## Goal

在任务详情页内嵌一个"流程调试"折叠区,让非专业用户能**全流程可视**地排查 v2 音画同步任务:

1. 看到每一个阶段的**中间产物**(ASR / shot_notes / av_translate / TTS / duration_reconcile)
2. 看到每次 LLM 调用的**完整 prompt + 完整 response + token + 耗时**(可展开)
3. 看到每一步的**决策日志**("为什么这句重写了 2 轮")
4. **实时**推送阶段进度(SSE),任务跑的过程中能看进度条一步步出来
5. 保留**每句重写历史**,调试"重写 2 轮仍不达标"这种核心痛点

非侵入式扩展:debug 采集层挂在 `llm_client` 和阶段入口,业务逻辑**零改动**。可通过 env 开关关闭,不影响已上线的 v2 管线。

---

## Confirmed Decisions

| 决策 | 选择 | 理由 |
|---|---|---|
| Q1 可视化形态 | 扩展现有任务详情页折叠区 | 最快落地,不切换 UI |
| Q2 展示粒度 | 结果 + 原始 I/O + 决策日志 | 调试需要定位是 prompt 坏还是 LLM 错 |
| Q3 阶段范围 | v2 + 关键上游(ASR + alignment) | ASR 错会拖垮 v2,必须能看到 |
| Q4 更新机制 | 实时流式(SSE) | 用户要求能看着进度一步步出 |
| Q5 历史策略 | 顶层最新 + 每句重写历史保留 | "重写 2 轮失败"是最痛调试场景 |

---

## Architecture

### 组件总览

```
任务详情页(浏览器)
  ├─ 现有任务详情 UI(不动)
  └─ 流程调试折叠区 [新]
      ├─ EventSource 订阅 /api/tasks/<id>/av_debug/stream
      ├─ 6 个阶段卡片(script_segments / shot_notes / av_translate /
      │                 tts / duration_reconcile / subtitle 预览)
      ├─ 每卡:status dot + 决策日志 + 可展开的原始 I/O
      └─ 重写历史轮次(sentence 级)

Flask 后端
  ├─ runtime.run_av_localize(改)
  │    每阶段前后调 av_debug.emit_stage_*()
  ├─ llm_client(改)
  │    invoke_chat/invoke_generate 透明 capture 到 task.state_json
  ├─ av_debug.py [新]
  │    contextvar 传 task_id;内存 queue per task;decision 日志 API
  ├─ routes/av_debug.py [新]
  │    GET /api/tasks/<id>/av_debug/stream (SSE)
  └─ state_json(扩)
       task.state_json.av_debug.stages.<stage> 记采集数据
       task.state_json.variants.av.sentences[i].rewrite_history
```

### 数据流

```
v2 任务启动
  ↓
runtime 设置 contextvar[current_task_id] = task_id
  ↓
runtime.emit_stage_start("shot_notes") ────→ SSE queue[task_id] put event
  ↓                                              ↓
pipeline.shot_notes.generate_shot_notes()    前端 EventSource 收到
  ├─ llm_client.invoke_generate(...)         ├─ 更新 stage dot 为 running
  │    拦截层读取 contextvar → capture        └─ 不刷整个页面
  │      append task.state_json.av_debug
  │             .stages.shot_notes.llm_calls[]
  │    推 SSE event "llm_call"
  ↓
runtime.emit_stage_done("shot_notes", output_ref="shot_notes")
  ↓                                              ↓
前端收到 event: stage_done                    fetch /api/tasks/<id>/state_json
更新卡片为 done + 刷新逐句笔记表格
```

### 关键设计原则

- **非侵入:** 业务代码不感知 debug 层;contextvar + 拦截器做透传,业务只需在阶段入口调 `emit_stage_*`
- **零新表:** 所有 debug 数据落 `state_json.av_debug`,避免 DB schema 变更
- **SSE 不跨进程:** Flask 单 worker(项目现状 `-w 1`),用内存 queue 即可;不引 Redis / pub-sub
- **失败降级:** debug 采集抛异常不能连累业务;`av_debug.emit_*` 全部 `try/except`,静默记 warning log

---

## Data Model

### 顶层 `task.state_json.av_debug`(additive)

```python
task.state_json.av_debug = {
  "enabled": True,
  "started_at": ts,
  "stages": {
    # 每个阶段独立子对象
    "script_segments": {   # 上游产物展示,不采集 LLM(它没走 LLM)
      "status": "done",
      "decisions": ["ASR 识别 12 句,最短 0.8s / 最长 4.2s"],
      "output_ref": "script_segments",
    },
    "shot_notes": {
      "status": "done",          # idle / running / done / error
      "started_at": ts, "ended_at": ts, "elapsed_ms": 12430,
      "llm_calls": [
        {
          "attempt": 1,
          "use_case": "video_translate.shot_notes",
          "messages": None,                 # generate 风格为 None
          "prompt": "...",                   # 完整 user prompt
          "system": "...",                   # 完整 system prompt
          "media_refs": ["output/tasks/xxx/video.mp4"],
          "response_raw": {...},             # LLM 返回的完整 JSON
          "tokens_in": 2134, "tokens_out": 4012,
          "elapsed_ms": 11800,
          "status": "success",               # success / error
          "error": None,
        },
      ],
      "decisions": [
        "LLM 返回 sentences = 12 vs ASR 12 ✓",
        "漏段补齐 0 条",
        "global.product_name = '无糖酸奶'",
      ],
      "output_ref": "shot_notes",    # 指向顶层 task.state_json.shot_notes
    },
    "av_translate": {
      "status": "done",
      "started_at": ts, "ended_at": ts, "elapsed_ms": 8200,
      "llm_calls": [ {...} ],   # 批量调用,可能 1 次,也可能按 structure_range 拆 2-4 次
      "decisions": [
        "#0 role=hook, target_chars=[38,46]",
        "#3 role=demo, target_chars=[45,55]",
        "#11 role=cta, target_chars=[30,38]",
      ],
      "output_ref": "variants.av.sentences",
    },
    "tts": {
      "status": "done",
      "elapsed_ms": 45000,
      "per_segment": [          # 逐句 TTS 生成记录
        {"asr_index": 0, "text": "...", "tts_path": "...", "tts_duration": 2.4,
         "voice_id": "xxx", "speed": 1.0, "elapsed_ms": 3200},
      ],
    },
    "duration_reconcile": {
      "status": "done",
      "decisions": [
        "#0 target=2.5s tts=2.4s overshoot=-4% → status=ok",
        "#5 target=3.0s tts=3.6s overshoot=+20% → round 1 rewrite",
        "#5 round 1: est_chars=52, tts=3.1s overshoot=+3% → status=ok",
        "#11 target=1.8s tts=2.3s overshoot=+28% → round 1 rewrite",
        "#11 round 1: tts=2.1s overshoot=+17% → round 2 rewrite",
        "#11 round 2: tts=2.0s overshoot=+11% → speed=1.12 → status=warning_overshoot",
      ],
      "rewrite_summary": {
        "total_sentences": 12, "needed_rewrite": 2, "rewrite_rounds_total": 3,
        "final_warning": 1,
      },
    },
    "subtitle": {
      "status": "done",
      "srt_path": "output/tasks/xxx/subtitle.av.srt",
      "full_audio_path": "output/tasks/xxx/tts_full.av.mp3",
    },
  }
}
```

### 句级 `variants.av.sentences[i].rewrite_history`(additive)

```python
"rewrite_history": [
  {
    "round": 1,
    "prev_text": "...",
    "prev_tts_duration": 3.6,
    "overshoot_sec": 0.6,
    "new_target_chars": [45, 50],
    "rewrite_prompt": "...",     # 完整的 user message
    "rewrite_response": "...",   # LLM 返回原文
    "new_text": "...",
    "new_tts_duration": 3.1,
    "result_status": "still_overshoot",   # still_overshoot / ok / warning
  },
  { "round": 2, ..., "result_status": "ok" },
]
```

`rewrite_rounds` 字段仍保留(兼容),等于 `len(rewrite_history)`。

---

## LLM 调用拦截

在 `appcore/llm_client.py` 的 `invoke_chat` / `invoke_generate` 末尾(返回前)新增 capture hook:

```python
def invoke_chat(use_case_code, *, messages, ...):
    ...
    try:
        result = adapter.chat(...)
    except Exception as e:
        _av_debug_capture_llm_error(use_case_code, messages, e, elapsed_ms)
        raise
    _av_debug_capture_llm_success(use_case_code, messages, result, elapsed_ms)
    return result
```

capture 函数在 `appcore/av_debug.py` 里,做的事:

1. 读 `_CURRENT_TASK_ID.get()`(contextvar,由 `runtime.run_av_localize` 在任务开始时 set)
2. 读 `_CURRENT_STAGE.get()`(contextvar,由 `emit_stage_start` 设置)
3. 读 `_AV_DEBUG_ENABLED`(env `AV_DEBUG_CAPTURE`,默认 True)
4. 组装 `llm_call` record append 到 `task.state_json.av_debug.stages[stage].llm_calls[]`
5. 持久化到 DB(走现有 `task_state.save()` 或 `update_state_json()`)
6. emit SSE event `llm_call`

**关键约束**:
- capture 任何异常必须 swallow + warning log,不能中断业务
- `prompt`/`messages`/`response_raw` 存前做体积检查:单条 > 100KB 时截断为 `<truncated; full len=XXXXX>` 避免炸 state_json
- 不缓存任何 API key / user secret(messages 内容按原样存,不额外 redact)

---

## SSE 推流

### 端点

`GET /api/tasks/<task_id>/av_debug/stream`

- 鉴权:同任务详情页权限(用户能访问 task 详情才能订阅)
- Response header: `Content-Type: text/event-stream; Cache-Control: no-cache; X-Accel-Buffering: no`
- 用 `flask.Response(generate(), mimetype='text/event-stream')` + `stream_with_context`

### 事件类型

```
event: connected         data: {ts}
event: stage_start       data: {stage: "shot_notes", ts}
event: stage_progress    data: {stage, note: "批次 2/3 完成", ts}
event: stage_done        data: {stage, elapsed_ms, output_ref, ts}
event: stage_error       data: {stage, error_msg, stack_tail, ts}
event: llm_call          data: {stage, use_case, tokens_in, tokens_out, elapsed_ms, status, ts}
event: decision          data: {stage, message, ts}
event: rewrite_round     data: {asr_index, round, status, ts}
event: task_done         data: {ts}
```

前端收到 `stage_done` / `rewrite_round` 后,**不依赖 event payload 还原状态**,而是 fetch 最新 `state_json.av_debug` 更新 UI —— 这样 event 丢失或顺序乱都能自愈。

### 内存 queue(单 worker 够用)

```python
# appcore/av_debug.py
_task_queues: dict[int, queue.Queue] = {}
_task_queues_lock = threading.Lock()

def get_or_create_queue(task_id: int) -> queue.Queue:
    with _task_queues_lock:
        q = _task_queues.get(task_id)
        if q is None:
            q = queue.Queue()
            _task_queues[task_id] = q
        return q

def emit(task_id: int, event_type: str, data: dict):
    if not _AV_DEBUG_ENABLED: return
    try:
        get_or_create_queue(task_id).put_nowait(
            {"event": event_type, "data": data, "ts": time.time()}
        )
    except Exception as e:
        logger.warning("av_debug emit failed: %s", e)
```

任务结束后发 `task_done` event,前端 EventSource `close()`,后端清理 queue(定时 sweep 超 1 小时无订阅者的 queue)。

**Flask 单进程限制**:多 worker 下 queue 跨进程失效。项目现有 `gunicorn -w 1 -k eventlet`,符合前提。spec 明确声明该限制;如果未来需要 scale,改 Redis pub/sub,本次不做。

---

## UI 折叠区

### 位置

任务详情页(路径:`web/templates/project_detail.html` 或项目实际文件名)主信息下方新增一张卡片:

```
┌─────────────────────────────────────────┐
│ ▶ 流程调试 [v2 音画同步]        [状态灯] │  ← 折叠头,默认收起
└─────────────────────────────────────────┘
```

展开后:

```
┌─────────────────────────────────────────┐
│ ▼ 流程调试 [v2 音画同步]   ● 运行中...  │
├─────────────────────────────────────────┤
│ ┌──────────────┐  ┌──────────────┐     │
│ │ ● ASR 对齐    │  │ ○ 画面笔记    │     │
│ │  12 句 / 34s │  │  waiting...  │     │
│ └──────────────┘  └──────────────┘     │
│ ┌──────────────┐  ┌──────────────┐     │
│ │ ○ 翻译        │  │ ○ TTS 合成    │     │
│ └──────────────┘  └──────────────┘     │
│ ┌──────────────┐  ┌──────────────┐     │
│ │ ○ 时长闭环    │  │ ○ 字幕产物    │     │
│ └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────┘
```

### 单卡结构

每个阶段卡片:
- 卡片头:status dot(灰 idle / 蓝 running / 绿 done / 红 error)+ 阶段名 + 耗时
- 简要信息区:关键数字(如 av_translate 卡显示 12 句已翻译 / 平均 est_chars)
- **"决策日志"可折叠子区**:倒序列出 `stages[stage].decisions[]`
- **"LLM 调用"可折叠子区**:每条调用一行:use_case / attempt / tokens / elapsed;点击展开完整 prompt + response
- **"数据"可折叠子区**:指向 `output_ref` 的核心产物(表格或 pre-JSON)

### 专用子视图

- **shot_notes 卡**:全局摘要放顶部(product_name / structure ranges / pacing_note),逐句笔记做表格:index / start-end / shot_type / product_visible / scene / action
- **av_translate 卡**:逐句表格:index / role / target_chars / est_chars / text;每行可展开看本句 shot_context + LLM 本句返回 + 决策
- **TTS 卡**:逐句:text / target_dur / tts_dur / overshoot / status / speed / 🔊(内联 audio 播放当前句 mp3)
- **duration_reconcile 卡**:按时间顺序展示 decisions;`warning_overshoot` / `warning_short` 高亮红;有 `rewrite_history` 的句子额外折叠子区展开多轮(每轮显示 prev_text → new_text / prev_tts → new_tts / rewrite prompt + response)

### UI 样式

遵循 Ocean Blue Admin 设计系统:
- 卡片 `1px border + radius-lg`,白底
- 展开子区用 `bg-subtle`,间距 `space-4`
- status dot:idle `fg-subtle` / running `cyan` 带脉冲(唯一允许的动画,180ms 脉冲) / done `success` / error `danger`
- 代码/JSON 用 `font-mono`,`pre` 带 max-height 滚动
- 禁用紫色 / emoji / glassmorphism

---

## 回滚开关

两个 env,独立可控:

| 开关 | 默认 | 关闭后行为 |
|---|---|---|
| `AV_DEBUG_CAPTURE` | `1` | 不再采集 LLM prompt/response 到 state_json;SSE 仅发基础进度事件(stage_start/done),不发 llm_call/decision |
| `AV_DEBUG_UI` | `1` | 前端模板不渲染调试折叠区;后端采集照常(便于出问题后查 state_json) |

两个开关由 `config.py` 读取,塞进 Flask `g`/template context 供前端模板用。

老任务无 `av_debug` 字段 → UI 显示"该任务无调试数据(创建时 v2 版本较早)",不崩。

---

## 文件布局

**新增**:
- `appcore/av_debug.py` — contextvar + queue manager + capture 函数 + emit 函数
- `web/routes/av_debug.py` — SSE 端点 `/api/tasks/<id>/av_debug/stream`
- `web/static/av_debug.js` — EventSource 订阅 + 渲染
- `web/static/av_debug.css` — 折叠区样式(Ocean Blue token)
- `web/templates/av_debug_panel.html` — 折叠区模板,被任务详情页 include

**改动**:
- `appcore/llm_client.py` — `invoke_chat` / `invoke_generate` 末尾加 capture hook
- `appcore/runtime.py` — `run_av_localize` 每阶段前后 `emit_stage_start/done/error`;设置 contextvar
- `pipeline/shot_notes.py` / `av_translate.py` / `duration_reconcile.py` — 在关键决策点调 `av_debug.log_decision(stage, msg)`
- `config.py` — 读 `AV_DEBUG_CAPTURE` / `AV_DEBUG_UI`
- 任务详情页 template — include `av_debug_panel.html`

**不动**:
- 现有任务详情页结构、状态字段、结果展示逻辑
- v2 业务逻辑(shot_notes / av_translate 的功能代码零改动,只加 decision 日志调用)

---

## Migration & 向前兼容

- 老任务(v2 之前 / v2 但无 av_debug 字段)打开详情页时:调试折叠区显示"该任务无调试数据"
- v2 新任务自动启用采集(除非 env 关闭)
- 数据累计占用:每 LLM 调用平均 10-50KB(prompt + response),每任务预计 50-200KB;`state_json` 以前已经有 shot_notes + variants 几 MB 级别,新增占比 <5%,不担心撑爆

---

## Risks

1. **prompt/response 过长导致 state_json 膨胀** — 缓解:单条 > 100KB 时截断存;全任务 `av_debug` 总大小 > 5MB 时告警日志
2. **SSE 连接泄漏** — 浏览器关闭但 queue 还堆事件:定时 sweep 1h 无订阅者的 queue
3. **多 worker 部署时 SSE 失效** — spec 里明确限制单 worker;如果后续扩容要改 Redis pub/sub
4. **LLM capture 抛异常连累业务** — 所有 capture 包 try/except;有单元测试专门验"capture 抛异常不影响 invoke_chat 返回"
5. **contextvar 跨线程** — `asyncio.create_task` 会继承 contextvar,但 `ThreadPoolExecutor.submit` 不会;项目 v2 是同步单线程流水线,无此风险;若未来改多线程要显式传 context
6. **敏感信息泄漏** — prompt 中可能含产品价格/目标受众等内部信息;但任务详情页本身就是有权限的人才能看,不增加暴露面

---

## Test Plan

### 单元测试

`tests/test_av_debug.py`:
- `test_capture_appends_to_state_json` — mock task_state,调 capture 验 llm_calls 数组增长
- `test_capture_swallows_exception` — mock task_state.save 抛异常,验 invoke_chat 正常返回不报错
- `test_capture_truncates_oversized_prompt` — 100KB+ prompt 截断
- `test_emit_when_disabled` — AV_DEBUG_CAPTURE=0 时 emit 不 put queue
- `test_queue_sweeper_removes_stale` — 超 1h 无订阅的 queue 被清

### 集成测试

`tests/test_av_debug_integration.py`:
- `test_full_run_emits_all_stages` — 用 mock 跑一次 run_av_localize,验 SSE event 序列完整:connected → stage_start*6 → stage_done*6 → task_done
- `test_rewrite_history_captured` — mock duration_reconcile 触发 2 轮 rewrite,验 sentence.rewrite_history 长度 = 2 且内容合法

### UI 冒烟

- 跑一个真实 v2 任务,打开详情页:
  - 折叠区默认收起,点击展开后 6 个卡片渲染正确
  - 任务跑的过程中能看到 status dot 实时切换
  - 展开 shot_notes LLM 调用能看到完整 prompt + response
  - `warning_overshoot` 句子展开能看到 rewrite_history 各轮
- AV_DEBUG_CAPTURE=0 时:折叠区仍展示 6 个卡,但 LLM 调用区显示"采集已关闭"
- AV_DEBUG_UI=0 时:详情页完全无折叠区
