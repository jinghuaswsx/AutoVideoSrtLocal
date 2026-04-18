# 一键从英文翻译到多语言 · 整体素材批量翻译任务(bulk_translate) · 设计文档

- 日期:2026-04-18
- 作者:Claude Code + noobird(协作)
- 状态:Design · 待进入实施计划
- 所属模块:素材管理(medias) + 视频翻译(translate_lab) + 文案/图片翻译

---

## 0. 目标与背景

### 0.1 业务目标

只维护**一套英文原版**(文案 + 商品主图 + 商品详情图 + 视频)作为单一事实源。通过一键触发,把英文素材自动翻译到所有启用的目标语言(de/fr/es/it/ja/pt),生成对应语言的译本素材。

核心价值:大幅降低多语言素材的运维成本,避免人工逐语种重复劳动。

### 0.2 需求来源

用户(产品负责人)在素材管理模块提出:每个产品现有英文原版素材,需要批量翻译到德语、法语等多语言。翻译动作涉及三类已有模块(视频翻译/文案翻译/图片翻译)的复用与编排。

### 0.3 本设计 11 条关键决策(brainstorming 阶段已锁定)

| # | 决策点 | 选择 |
|---|---|---|
| 1 | 触发入口 | 视频翻译详情页(单语言)+ 素材管理产品行(多语言)两个入口 |
| 2 | 内容类型 | 文案 / 主图 / 详情图 / 视频 四类都有,默认勾选文案+详情图+视频,主图默认不勾 |
| 3 | 已存在译文 | 默认跳过,弹窗提供"强制重新翻译全部"复选框 |
| 4 | 关联标识 | 同时记录 `source_ref_id`(源条目)+ `bulk_task_id`(批次任务)+ `auto_translated` 布尔 |
| 5 | 视频参数 | 12 项全覆盖,合理默认值,按"产品 × 语言"持久化 |
| 6 | 进度展示 | 右下角气泡 + `/tasks/<id>` 详情页 + `/tasks` 任务中心 三件套 |
| 7 | 重跑入口 | 父任务详情页 + 子任务原有详情页双入口(子任务是独立真实实体) |
| 8 | 执行并发 | 本期串行(调度层与执行解耦以便后续切换) |
| 9 | 多语言默认 | 产品行弹窗默认全勾所有启用语言 |
| 10 | 源变化追踪 | 本期不做,用户通过"强制重新翻译全部"手动触发 |
| 11 | 多产品批量 | 本期只支持单产品,下一期考虑 |

### 0.4 额外重要约束

- **视频翻译本期仅支持 de/fr 两种语言**,其他启用的目标语言(es/it/ja/pt 等)**自动跳过视频条目,但仍正常翻译文案和图片**。架构需预留视频翻译模块化多语言扩展接口。
- **绝对不允许任何自动恢复动作**。历史上曾因进程启动时自动恢复运行中任务导致机器卡死。所有恢复/续跑动作必须由用户手动点击按钮触发。

---

## 1. 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│  父任务 bulk_translate_job(projects 表新增 type)             │
│  ├─ 状态机:planning → running → done / paused / error        │
│  │                                        / cancelled         │
│  ├─ state_json:计划 + 子任务引用 + 参数快照 + 审计 + 费用     │
│  └─ 挂载 N 个真实子任务(指向现有 projects 记录):             │
│      ├─ copywriting_translate × N   (每语言 1 个,新增 type)   │
│      ├─ image_translate × N         (每产品 × 每语言 1 个)    │
│      └─ translate_lab × N           (每视频 × 每语言 1 个)    │
└──────────────────────────────────────────────────────────────┘
         ▲                                         ▼
      UI 入口                                   UI 展现
   ┌─────────────────────────┐           ┌─────────────────────────┐
   │ 1. 视频翻译详情页按钮     │           │ 1. 右下角浮动进度气泡    │
   │    "一键从英文翻译"      │           │    (全站悬浮)            │
   │    (单语言,仅 de/fr)     │           │ 2. /tasks/<id> 详情页    │
   │ 2. 素材管理产品行按钮    │           │    (核心,可刷新持久化)   │
   │    "一键多语言翻译"      │           │ 3. /tasks 任务中心列表   │
   │    (多语言)              │           │                          │
   └─────────────────────────┘           └─────────────────────────┘
```

### 1.1 技术复用清单

本期不新造轮子,全部复用现有基础设施:

- `translate_lab`:视频翻译 step 级断点续传、字幕配置参数、SocketIO 进度推送
- `image_translate`:批量处理、重试(`_MAX_ATTEMPTS=3`)、退避、60 秒 rate-limit 熔断保护、项目存档
- `copywriting`:LLM 多提供商适配层(`pipeline.translate.resolve_provider_config`)
- `task_state`:统一任务状态机 + `_active_tasks` 活跃集合
- `task_recovery`:宕机识别(**本设计将其改造为按需触发,取消启动时自动扫描**)
- `EventBus` + `SocketIO`:实时进度事件推送
- `projects` 表 + `state_json` JSON 字段:统一任务持久化

### 1.2 新增代码模块(预告)

```
appcore/
  bulk_translate_runtime.py          # 父任务调度器 + 对账函数
  bulk_translate_plan.py             # 根据产品 + 目标语言 + 内容类型生成 plan
  bulk_translate_estimator.py        # 费用/资源预估
  video_translate_defaults.py        # 12 项参数默认值 + 三层回填
  copywriting_translate_runtime.py   # 新增:纯文本翻译子任务 runtime

web/routes/
  bulk_translate.py                  # /api/bulk-translate/* + /tasks 页面路由

web/templates/
  bulk_translate_list.html           # /tasks 任务中心列表
  bulk_translate_detail.html         # /tasks/<id> 任务详情
  _bulk_translate_dialog.html        # 弹窗组件(两个入口共用)

web/static/
  bulk_translate.js                  # 弹窗 + 详情页交互
  bulk_translate_progress_bubble.js  # 全站浮动气泡组件

db/migrations/
  2026_04_18_bulk_translate_schema.sql
```

---

## 2. 数据模型

### 2.1 父任务——复用 `projects` 表

新增 `type = 'bulk_translate'` 枚举值,不新建表。`state_json` 结构:

```json
{
  "product_id": "prod_xxx",
  "source_lang": "en",
  "target_langs": ["de", "fr"],
  "content_types": ["copy", "detail", "video"],
  "force_retranslate": false,
  "video_params_snapshot": { "...": "12 项参数快照,任务启动时冻结" },

  "initiator": {
    "user_id": "u_xxx",
    "user_name": "张三",
    "ip": "1.2.3.4",
    "user_agent": "Mozilla/5.0 ..."
  },

  "plan": [
    {
      "idx": 0,
      "kind": "copy",
      "lang": "de",
      "ref": { "source_copy_id": "copy_en_1" },
      "sub_task_id": null,
      "status": "pending",
      "error": null,
      "started_at": null,
      "finished_at": null
    },
    {
      "idx": 1,
      "kind": "detail",
      "lang": "de",
      "ref": { "source_detail_ids": ["img_en_1", "img_en_2", "..."] },
      "sub_task_id": null,
      "status": "pending"
    },
    {
      "idx": 2,
      "kind": "video",
      "lang": "de",
      "ref": { "source_item_id": "item_en_1" },
      "sub_task_id": null,
      "status": "pending"
    }
  ],

  "progress": {
    "total": 10, "done": 0, "running": 0,
    "failed": 0, "skipped": 0, "pending": 10
  },
  "current_idx": 0,
  "cancel_requested": false,

  "audit_events": [
    {
      "ts": "2026-04-18T10:00:00+08:00",
      "user_id": "u_xxx",
      "action": "create",
      "detail": { "target_langs": ["de", "fr"], "content_types": ["copy", "detail", "video"], "force": false }
    }
  ],

  "cost_tracking": {
    "estimate": {
      "copy_tokens": 5400,
      "image_count": 48,
      "video_minutes": 10.4,
      "estimated_cost_cny": 21.5
    },
    "actual": {
      "copy_tokens_used": 0,
      "image_processed": 0,
      "video_minutes_processed": 0,
      "actual_cost_cny": 0
    }
  }
}
```

**字段约定**:
- `plan[].status` ∈ { `pending`, `running`, `done`, `error`, `skipped` }
- `skipped` 用于两种场景:① 该条目已存在且未选"强制重翻" ② 视频条目但目标语言不在 de/fr 支持列表内
- `audit_events` 是追加式,永不修改已存在条目
- `video_params_snapshot` 在任务从 planning 转 running 时冻结,之后即使用户修改产品级参数也不影响本任务

### 2.2 子任务——复用现有 `projects` 表,三种子 type

| 子任务 type | 来源 | 对应计划项 `kind` |
|---|---|---|
| `translate_lab` | 现成模块 | `video` |
| `image_translate` | 现成模块(一个任务处理同一产品同一语言的所有图) | `cover`(主图)/ `detail`(详情图) |
| `copywriting_translate` | **新增** type 值 | `copy` |

**为什么新增 `copywriting_translate` 而不是复用 `copywriting`**:现有 `copywriting` 是"从视频生成文案",是创作流程;本需求是"把已有英文文本翻译成德语",是翻译流程。二者 runtime 逻辑、输入输出、提示词、模板完全不同,强行混用会污染现有 `copywriting` 模块。新增子任务类型,复用 `pipeline.translate` 的 LLM 调用能力即可,不需要大重构。

### 2.3 现有素材表加字段

**迁移文件**:`db/migrations/2026_04_18_bulk_translate_schema.sql`

四张素材表(`media_copywritings` / `media_product_detail_images` / `media_items` / `media_product_covers`)**都加**如下字段:

```sql
ALTER TABLE media_copywritings
  ADD COLUMN source_ref_id     VARCHAR(64) NULL COMMENT '指向源英文条目 id',
  ADD COLUMN bulk_task_id      VARCHAR(64) NULL COMMENT '指向父任务 projects.id',
  ADD COLUMN auto_translated   TINYINT(1)  NOT NULL DEFAULT 0,
  ADD COLUMN manually_edited_at TIMESTAMP  NULL COMMENT '用户手工修改过自动翻译结果的时间',
  ADD INDEX idx_source_ref (source_ref_id),
  ADD INDEX idx_bulk_task  (bulk_task_id);

-- 其他三张表同结构迁移,略
```

### 2.4 新增表 `media_video_translate_profiles`

视频翻译 12 项参数的持久化。

```sql
CREATE TABLE media_video_translate_profiles (
  id           BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id      VARCHAR(64)  NOT NULL,
  product_id   VARCHAR(64)  NULL,       -- NULL = 用户级默认
  lang         VARCHAR(8)   NULL,       -- NULL = 产品级全语言默认
  params_json  JSON         NOT NULL,   -- 12 项参数
  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_scope (user_id, product_id, lang),
  INDEX idx_user (user_id)
);
```

**回填优先级**:`(user × product × lang)` → `(user × product, lang=NULL)` → `(user, product=NULL, lang=NULL)` → 代码常量 `SYSTEM_DEFAULTS`。

---

## 3. UX 设计

### 3.1 入口 A · 素材管理产品行

**触发点**:产品行操作区新增胶囊按钮 "🌐 一键翻译",Lucide `languages` 图标。

**弹窗(Ocean Blue Modal)**:

```
🌐 一键从英文翻译 — 产品「XX 商品名称」                 ×
─────────────────────────────────────────────────────

📍 源语言  🇬🇧 英文(固定)

▸ 目标语言(默认全选)
   ☑ 🇩🇪 德语   ☑ 🇫🇷 法语   ☑ 🇪🇸 西班牙语
   ☑ 🇮🇹 意大利  ☑ 🇯🇵 日语   ☑ 🇵🇹 葡萄牙语

▸ 翻译内容
   ☑ 商品文案         (3 条英文)
   ☐ 商品主图         (1 张英文)
   ☑ 商品详情图       (8 张英文)
   ☑ 视频素材         (2 个英文)
     ℹ️ 视频翻译仅支持 德语/法语,其他语言将跳过视频

▸ 处理策略
   ☐ 强制重新翻译全部(默认跳过已存在译本)

▸ 视频翻译参数    ▾ 展开(使用上次为本产品保存的配置)

─────────────────────────────────────────────────────
📊 预估消耗(跟随勾选实时更新)
   文案 18 条 · ~5,400 tokens
   详情图 48 张
   视频 4 个(仅德/法) · ~10 分钟
   ─────────────────────────
   💰 预估费用 ≈ ¥21.50

                              [取消]   [▶ 开始翻译]
```

**交互规则**:
- 数字用 `--font-mono`,费用用海洋蓝 `--accent` 高亮
- 勾选变化 → 预估区 debounce 300ms 实时重算(调用 `POST /api/bulk-translate/estimate`)
- 若勾"视频"但目标语言不含 de/fr → 顶部出现 `--warning-bg` 提示条
- 点"开始翻译" → **二次确认弹窗**:"将启动 N 个子任务,预估 ¥21.50,确认?"
- 确认后父任务创建,Modal 关闭,右下角气泡自动出现,页面不跳转

### 3.2 入口 B · 视频翻译详情页

**触发点**:当 `translate_lab_detail.html` 的 `target_language ∈ {de, fr}` 时,顶部按钮栏显示 "🌐 一键从英文翻译" 按钮;其他语言不显示。

**弹窗**:复用入口 A 的 `<BulkTranslateDialog>` 组件,`mode='single-lang'`:
- 目标语言一栏改为只读徽章 `🇩🇪 德语`(不可改)
- 其他字段、预估、二次确认流程完全一致

### 3.3 视频翻译参数 12 项默认值

**🟢 基础档**(弹窗展开即见,5 项最常改):

| # | 参数 | 默认值 |
|---|---|---|
| 1 | 字幕字体 | `Noto Sans`(取代原 `Impact`,支持德法字符) |
| 2 | 字幕大小 | `14` 像素 |
| 3 | 字幕位置 Y | `0.88`(底部) |
| 4 | 字幕颜色 + 描边 | 白字 `#FFFFFF` + 黑边 `#000000` + 宽 `2px` |
| 5 | TTS 音色 | 德语 `Anke` · 法语 `Céline`(最终 `voice_id` 根据可用供应商探测) |
| 6 | 字幕烧录模式 | 烧录进视频 + 同时输出 `.srt` |

**🟡 进阶档**(默认折叠):

| # | 参数 | 默认值 |
|---|---|---|
| 7 | 字幕底条 | 无 |
| 8 | TTS 语速 | `1.0x` |
| 9 | 背景音策略 | `keep`(原声底压至 `-18dB`) |
| 10 | 最大行宽 | `42` 字符/行 |

**⚪ 高级档**(通常不需要改):

| # | 参数 | 默认值 |
|---|---|---|
| 11 | 输出分辨率 | `source`(保持原分辨率) |
| 12 | 编码/码率 | `H.264` · `2000 kbps` · `mp4` |

代码:`appcore/video_translate_defaults.py::SYSTEM_DEFAULTS`。

**保存按钮**(弹窗右下角):
- "保存配置" → 写 `(user × product × lang)`
- "保存为该产品默认" → 写 `(user × product, lang=NULL)`
- "保存为我的默认" → 写 `(user, product=NULL, lang=NULL)`

### 3.4 进度三件套

**件 1 · 右下角浮动气泡**(全站悬浮):
- `position: fixed; bottom: 24px; right: 24px`
- 极简态:180px 宽,显示 `"🌐 2 个任务 · 53%"`
- 展开态:360px 宽,列出进行中任务 + 今日完成数 + "全部任务 →" 链接
- 全部完成后停留 10 秒显示 `"✓ 全部完成"` 再消失
- 失败时边框转 `--danger`,不自动消失

**件 2 · `/tasks` 任务中心列表页**:
- 左侧导航新增入口"翻译任务中心"
- Tab:全部/进行中/已完成/失败/已取消
- 筛选:产品 / 发起人 / 日期范围
- 表格列:任务 · 产品 · 目标语言 · 发起人 · 创建时间 · 状态 · 进度 · 预估/实际费用 · 操作

**件 3 · `/tasks/<task_id>` 任务详情页**(核心):

```
← 返回  · 🌐 产品「XX」多语言翻译
📅 2026-04-18 10:00  🧑 张三 (IP 1.2.3.4)  🌍 6 语言  🆔 task_xxx
─────────────────────────────────────────────────────────────
┌─ 总进度卡 ──────────────────────────────────────────────┐
│  ■■■■■□□□□□  53%  (16/30)                                │
│  ✅ 12   🔄 1   ❌ 2   ⏩ 1   ⏳ 14                          │
│  💰 预估 ¥21.50 / 实际 ¥11.30      ⏱️ 已耗 12:34            │
└─────────────────────────────────────────────────────────┘

[▶ 继续执行]   [⏸ 暂停]   [🔁 重跑所有失败项]   [📜 操作记录]

▼ 🇩🇪 德语  (8/10 完成)
  ✅ 文案 #1 → "Willkommen..."                [查看子任务 ↗]
  ✅ 详情图批量(8 张)                         [查看子任务 ↗]
  🔄 视频 item_123 (45%, 子任务跑到 TTS 步)    [查看子任务 ↗]
  ❌ 视频 item_456 (子任务报错:LLM 超时)       [查看子任务 ↗] [🔁 重跑]

▶ 🇫🇷 法语  (3/10 完成)
```

**关键交互**:
- "查看子任务 ↗":新标签页打开子任务原有详情页(translate_lab / image_translate / copywriting_translate),在那里可做 step 级重试
- "🔁 单项重跑":该项 status → pending,父任务从 error 回到 running,调度器继续
- "🔁 重跑所有失败项":所有 error 项 → pending,继续调度
- "▶ 继续执行":仅 status=error/paused 时显示,触发对账 + 继续调度
- "⏸ 暂停":当前 running 子任务跑完后停住,父任务 → paused
- "📜 操作记录":右侧抽屉展示 `audit_events` 时间线

### 3.5 关联标识 UI

**徽章**(Ocean Blue 胶囊):
- 默认:图标 + 文字 `🔗 英文译本`
- 图片类:icon-only 迷你版,14px 方形
- 色彩:`--accent-subtle` 底 + `--accent` 文字 + `--radius-md`

**出现位置**:
- 文案卡片右上角
- 主图 / 详情图缩略图右上角(icon-only)
- 视频卡片右上角 + 视频详情页顶部

**悬浮卡**(hover 300ms):

```
╭────────────────────────────────────╮
│  🔗 自动翻译条目                    │
├────────────────────────────────────┤
│  来源  英文文案 #3                  │
│        "Welcome to our product..."  │
│  批次  2026-04-18 10:00              │
│        张三 发起                    │
│                                      │
│  [查看源条目 ↗]  [查看批次任务 ↗]  │
╰────────────────────────────────────╯
```

**列表筛选**(素材管理 / 某语言视图):顶部筛选条新增 `来源: [全部] [原创] [自动翻译]`。

**素材详情页来源信息折叠区**:展示源语言/源条目/翻译批次/耗时/使用 token/模型,并提供"重新翻译此条"按钮(等同创建只含该单项的临时父任务)。

**人工编辑后的标识**:若用户编辑自动翻译结果并保存,`manually_edited_at = NOW()`,徽章变为 `🔗 英文译本 · ✏️ 已人工修改`。

**安全退化**:
- 源条目被软删 → 徽章 hover 卡显示灰色 "⚠️ 源已删除",链接失效
- 父任务被软删 → 徽章保留,"查看批次任务"链接失效显示 "⚠️ 批次记录已清理"
- 源条目被修改 → 本期不检测(延后)

---

## 4. 父任务状态机 & 断点续传语义

### 4.1 状态机

```
             ┌──────────┐
  创建任务 ─▶│ planning │  生成 plan,估算费用,等用户二次确认
             └────┬─────┘
                  ▼
             ┌──────────┐
             │ running  │  调度器串行跑子任务
             └────┬─────┘
                  │
     ┌────────────┼────────────┬─────────────┐
     ▼            ▼            ▼             ▼
  ┌──────┐    ┌──────┐    ┌──────┐     ┌──────────┐
  │ done │    │paused│    │error │     │cancelled │
  └──────┘    └──────┘    └──────┘     └──────────┘
```

**状态说明**:
- `planning`:父任务已创建,plan 已生成,费用已估,**未启动调度器**
- `running`:调度器主循环在跑,`task_state._active_tasks` 里有此任务
- `paused`:用户点"⏸ 暂停",当前子任务跑完后整体停,state_json 不变
- `error`:任意子任务 error,或用户主动对账发现 running 项已丢失。**永远停在 error 直到人工处理**
- `cancelled`:用户点"取消"。**不回滚已完成子任务产物**,只停止继续调度
- `done`:plan 里所有项都是 `done` / `skipped`

### 4.2 调度器伪代码

```python
def bulk_translate_scheduler(parent_task_id):
    while True:
        parent = load_task(parent_task_id)
        if parent.state.cancel_requested:
            mark_cancelled(parent); break
        if parent.status == "paused":
            break

        next_item = find_next_pending(parent.plan)
        if next_item is None:
            finalize(parent); break

        # 检查视频条目是否跳过(目标语言不在 de/fr 列表)
        if next_item.kind == "video" and next_item.lang not in {"de", "fr"}:
            mark_item_skipped(parent, next_item, reason="video_lang_not_supported")
            continue

        # 检查已存在且未选强制重翻 → 跳过
        if not parent.state.force_retranslate and exists_translation(next_item):
            mark_item_skipped(parent, next_item, reason="already_exists")
            continue

        mark_item_running(parent, next_item)
        try:
            sub_task_id = dispatch_sub_task(next_item)
            wait_for_sub_task(sub_task_id)
            sub_result = load_task(sub_task_id)
            if sub_result.status == "done":
                mark_item_done(parent, next_item, sub_task_id)
                roll_up_cost(parent, sub_result)   # 子任务 token / 费用回写父任务
                persist_source_ref(next_item, sub_result)  # 写 auto_translated + source_ref_id + bulk_task_id
            else:
                mark_item_error(parent, next_item, sub_task_id, sub_result.error)
                mark_parent_error(parent)
                break   # 铁律:失败即停,绝不跳过继续跑
        except Exception as e:
            mark_item_error(parent, next_item, None, str(e))
            mark_parent_error(parent); break

        emit_progress_event(parent)  # SocketIO 推送 bulk_translate_progress
```

### 4.3 三种人工恢复路径(**无任何自动恢复**)

| 按钮 | 动作 |
|---|---|
| **▶ 继续执行** | 1) 对账 running 项真实状态(心跳丢失则标 error)2) 仅恢复 pending 项 3) 已 error 的保持 error 不自动重置 |
| **🔁 重跑所有失败项** | 1) 对账 2) 所有 error 项 → pending 3) 启动调度器 |
| **🔁 单项重跑** | 1) 该项 error/done → pending 2) 父任务若 done 回到 error 3) 启动调度器 |

**三者都追加 `audit_events`**,记录点击人 + 时间 + IP + UA。

### 4.4 子任务级断点续传

复用现有 `translate_lab` / `image_translate` / `copywriting_translate`(新)的 step 级断点续传。父任务只关心子任务整体 done / error。用户想从子任务内部断点续传 → 点"查看子任务 ↗"进子任务详情页,用其原有 step 重试机制。

### 4.5 宕机/重启后的行为(严格人工触发)

- **进程启动**:不扫描任何 `bulk_translate` 任务,不对账,不标记,不重试。task 保持它最后一次写入 DB 的状态不动
- **打开任务详情页**:不主动对账,直接展示 state_json 快照状态
- **所有恢复动作只在用户按按钮时发生**
- **前端检测 running 但 SocketIO 心跳 30 秒无响应**:只在 UI 上显示黄色提示条"任务可能已中断,点击'继续执行'恢复",绝不代用户决定

---

## 5. 费用预估

### 5.1 端点

```
POST /api/bulk-translate/estimate
body: {
  product_id: "prod_xxx",
  target_langs: ["de", "fr", "es"],
  content_types: ["copy", "detail", "video"],
  force_retranslate: false
}
resp: {
  copy_tokens: 5400,
  image_count: 48,
  video_minutes: 10.4,
  skipped: { copy: 0, detail: 0, video: 0 },
  estimated_cost_cny: 21.50,
  breakdown: {
    copy_cny: 3.2,
    image_cny: 8.4,
    video_cny: 9.9
  }
}
```

### 5.2 算法

- **文案 tokens**:`(英文条目字数 × 1.5 扩展系数) × 目标语种数`。英文 1 字 ≈ 1.3 tokens 假设
- **图片数量**:`英文详情图张数 × 目标语种数`(主图同理,若勾选)
- **视频分钟**:`英文视频总时长 × 目标语种数`(仅计入 de/fr)
- **费用**:
  - 文案:`tokens × LLM 单价`(`pipeline.translate` 中的提供商配置)
  - 图片:`张数 × 图片翻译单价`(`image_translate` 配置)
  - 视频:`分钟 × 视频翻译单价`(TTS + 合成 + LLM 字幕翻译综合估算)

**精度**:差 ±20% 以内即可。目的是给用户心理预期,非精确计费。

### 5.3 实际消耗回写

每个子任务 runtime 完成后,把消耗回写父任务:
- `translate_lab`:视频分钟数 + 字幕翻译 tokens + TTS 字符数
- `image_translate`:图片张数
- `copywriting_translate`:tokens

统一在 `pipeline.translate.resolve_provider_config()` 之后的 LLM 调用处埋点,一次投入长期受益。

---

## 6. 路由与 API

### 6.1 页面路由

| 路径 | 页面 |
|---|---|
| `/tasks` | 任务中心列表 |
| `/tasks/<task_id>` | 任务详情页(父任务) |

### 6.2 API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/bulk-translate/estimate` | 费用/资源预估 |
| `POST` | `/api/bulk-translate/create` | 创建父任务(planning 状态) |
| `POST` | `/api/bulk-translate/<id>/start` | 二次确认后启动调度器 |
| `GET` | `/api/bulk-translate/<id>` | 查询父任务详情 |
| `GET` | `/api/bulk-translate/list` | 列表(支持 Tab 筛选) |
| `POST` | `/api/bulk-translate/<id>/pause` | 暂停 |
| `POST` | `/api/bulk-translate/<id>/resume` | 继续执行(先对账后调度) |
| `POST` | `/api/bulk-translate/<id>/cancel` | 取消 |
| `POST` | `/api/bulk-translate/<id>/retry-item` | 单项重跑 `{ idx: N }` |
| `POST` | `/api/bulk-translate/<id>/retry-failed` | 重跑所有失败项 |
| `GET` | `/api/bulk-translate/<id>/audit` | 审计事件流(用于抽屉展示) |
| `GET` | `/api/video-translate-profile` | 查询视频参数(按 user/product/lang 回填) |
| `PUT` | `/api/video-translate-profile` | 保存视频参数(含 scope 参数) |

### 6.3 SocketIO 事件

| 事件名 | 推送时机 | 负载 |
|---|---|---|
| `bulk_translate_progress` | 子任务状态变化 / 调度器步进 | `{ task_id, progress, current_idx, last_event }` |
| `bulk_translate_done` | 父任务完成 | `{ task_id, status, cost_actual }` |

---

## 7. MVP 范围边界

### 7.1 ✅ 本期做

- 两个入口 · 4 类内容 · 视频仅 de/fr · 文案+图片跟随启用语言
- 父任务编排单产品串行调度
- 子任务复用 + 新增 `copywriting_translate`
- 断点续传(父级 + 子级,**仅人工触发**)
- 单项 / 批量 重跑
- 进度三件套 · 关联徽章 · 费用预估 · 审计追踪

### 7.2 ❌ 本期不做(明确延后)

| 延后项 | 理由 | 后续时机 |
|---|---|---|
| **进程启动自动恢复** | 铁律:历史事故,永不实现 | 永不 |
| 多产品批量 | MVP 简化 | 下一期包一层 for 循环 |
| 并行执行 | 调度接口预留 | 视频稳定后开并发上限 |
| 英文源变化追踪 | 数据模型复杂度 | 下一期加 `content_hash` |
| 视频翻译扩展 es/it/ja/pt | 需 per-语言 prompt 架构改造 | 下一期模块化改造 |
| 跨任务审计报表 | 本期审计挂 state_json | 下一期抽独立表 |
| TTS 音色 per 条目精细调节 | 需求不明确 | 视反馈 |
| 翻译质量 A/B | 成本高 | 视反馈 |
| Webhook / 邮件通知 | UI 气泡已足够 | 视反馈 |
| 任务模板跨产品复用 | "上次保存"已覆盖单产品 | 视反馈 |

### 7.3 🛡️ 必须遵守的开发铁律

1. **绝不自动恢复任何 bulk_translate 任务** —— 进程启动不扫描、不对账、不触发任何执行
2. **子任务失败 → 父任务立即停**,绝不跳过继续跑
3. **"开始翻译"点击后必须过二次确认**,展示预估费用
4. **所有恢复/重跑 API 都记 `audit_events`**(发起人 + 时间 + IP + UA)
5. **父任务取消不回滚已完成子任务产物**
6. **视频翻译仅对 de/fr**,其他语言勾视频项在调度阶段静默 `skipped` 而非 `error`
7. **`copywriting_translate` 是新 type**,不要塞进现有 `copywriting` 路由
8. **不新建 ORM 实体,只用一个迁移 SQL 文件**
9. **LLM token 计数埋点统一在 `pipeline.translate` 层做**
10. **弹窗组件复用**:`<BulkTranslateDialog>` 通过 `mode` 参数切换两入口

---

## 8. 验收标准

### 8.1 功能验收

- [ ] 素材管理产品行可见"🌐 一键翻译"按钮
- [ ] 视频翻译详情页(de/fr)可见"🌐 一键从英文翻译"按钮
- [ ] 弹窗默认勾选符合约定(目标语言全勾,内容 ①③④ 勾)
- [ ] 勾选变化 → 预估实时更新
- [ ] 视频 × 非 de/fr 语言 → 黄色提示条出现
- [ ] 二次确认 → 展示预估费用
- [ ] 父任务创建成功 → 右下角气泡自动出现
- [ ] `/tasks` 列表 + `/tasks/<id>` 详情均可访问,刷新后状态持久
- [ ] 调度器串行跑完全部子任务,无 error 场景下父任务 → done
- [ ] 子任务失败 → 父任务立即停在 error
- [ ] 单项重跑 / 批量重跑失败项 / 继续执行 → 三个按钮均生效
- [ ] 已存在译本默认跳过;勾"强制重新翻译" → 覆盖
- [ ] 视频条目 × 非 de/fr → 标记 `skipped`
- [ ] 译本条目上显示 `🔗 英文译本` 徽章 + 悬浮卡
- [ ] 徽章"查看源条目"/"查看批次任务"链接正确跳转
- [ ] 人工编辑后徽章变 `🔗 英文译本 · ✏️ 已人工修改`
- [ ] `audit_events` 完整记录创建/取消/重跑/继续

### 8.2 非功能验收

- [ ] 进程重启后:**已有的 running 任务状态不被任何自动动作修改**
- [ ] 进程重启后:右下角气泡不自动冒出任何任务;打开 `/tasks/<id>` 不自动对账
- [ ] 用户点"▶ 继续执行"后才做对账
- [ ] 预估精度:实际费用在预估 ±20% 范围内
- [ ] UI 全程符合 Ocean Blue 设计系统(零紫色,OKLCH 色调在 200-240)
- [ ] 全流程键盘可达(Tab / focus / Esc)
- [ ] 响应式:`< 1024px` 侧栏折叠,`< 768px` 主内容单列

---

## 9. 已识别风险

| 风险 | 缓解 |
|---|---|
| 视频翻译长耗时阻塞串行队列 | 本期接受,下一期开并发 |
| LLM 提供商 rate limit 中断任务 | 子任务内部已有 3 次重试 + 60s 熔断(复用 image_translate 机制) |
| 费用预估偏差大,用户误点后超支 | 二次确认 + 父任务详情页显眼展示实际费用,超预估 50% 时前端提示 |
| 自动翻译质量不达标 | 本期不做质量门禁,用户可手工编辑(带 `manually_edited_at` 标识) |
| 视频翻译 de/fr 之外的语言实现需要独立模块化改造 | 延后到下期,明确在 MVP 边界不做 |
| SocketIO 掉线导致气泡进度不更新 | 前端 30 秒心跳检测,失联显示"可能已中断"黄条,**不自动恢复** |

---

## 10. 后续工作展望

本设计 done 后,可按如下顺序继续:

1. **视频翻译多语言扩展**(最优先):参照 `image_translate` 改为 per-语言 prompt 配置,支持 es/it/ja/pt
2. **英文源变化检测**:加 `content_hash`,译本上显示"源已更新"徽章
3. **多产品批量 + 并行**:素材管理多选产品 + 全局并发上限(默认 3)
4. **跨任务审计报表**:抽独立 `bulk_translate_audit_log` 表,做按用户/按月统计
5. **翻译质量工具**:人工校对打分 + 统计,反哺 prompt 调优

---

## 附录 A · 关键决策追溯

| 决策 | 用户选择 |
|---|---|
| 入口 | C(两个入口都要) |
| 内容类型 | C(四类都有,默认勾 ①③④,② 不勾) |
| 已存在译文 | D(默认跳过 + 强制重翻复选框) |
| 关联标识 | D(`source_ref_id` + `bulk_task_id` 都加) |
| 视频参数 | C(12 项全要,合理默认,产品 × 语言持久化) |
| 进度展示 | D(气泡 + 详情页 + 任务中心三件套) |
| 重跑入口 | B(父任务详情 + 子任务详情双入口) |
| 并发度 | 1(全串行先跑通) |
| 多语言默认 | A(全勾所有启用语言) |
| 源变化追踪 | D(本期不做) |
| 多产品批量 | A(本期只支持单产品) |

## 附录 B · 本设计受过的额外补丁

- **审计与费用追踪**:第 1 节 F 段新增(发起人/IP/UA/audit_events/cost_tracking)
- **绝不自动恢复铁律**:第 3 节宕机恢复段完全重写,第 4 节 4.5 段增加"进程启动不扫描"的明确约束,第 7 节铁律第 1 条置顶
- **视频翻译仅 de/fr**:第 0.4 节明确,第 4 节 4.2 调度器伪代码中做显式 `skipped` 处理,第 7 节延后清单明确
- **`manually_edited_at` 字段**:第 3.5 节关联徽章补充,第 2.3 节迁移 SQL 一并加入

---

**End of Design**
