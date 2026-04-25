# 明空选品 → 推送 任务流水线 主需求文档

> **本文档目的**：固化 2026-04-26 与用户 brainstorm 的全套需求 + 已做决定，作为后续任何模块（C 已实施中、A/B/D/E/F 未开始）继续推进时的"交接包"。
>
> **使用方式**：把本文件丢给任何新会话（Claude / Codex），让其快速理解全局意图，然后定位到具体子系统的 spec / plan / 待 brainstorm 入口，继续推进。
>
> **维护原则**：每完成一个子系统的 brainstorm，把对应章节从"未 brainstorm"翻成"已 brainstorm"并补充链接；每锁定一个新决定，登记到第 4 节"已锁定决定清单"。
>
> **生成日期**：2026-04-26

---

## 0. TL;DR — 一句话与一张图

**一句话**：把"管理员在明空选品发现一个适合外销的视频，一路到上线推送"这条业务主线**系统化**——每个人都知道去哪领任务，每个产物都有审核链路，每个员工的产能都可统计。

**业务主线 ASCII 图**：

```
┌─────────────────────┐
│  明空选品 (mk)        │  起点：admin 浏览，看到一个适合搬运的视频
│  - 视频列表 / 详情     │
└──────────┬──────────┘
           │ "勾选这个视频做搬运"
           ▼
┌─────────────────────┐
│  分支判断             │
│  老品 vs 新品         │
└──────┬──────┬───────┘
       │      │
   老品 │      │ 新品
       │      ▼
       │   ┌─────────────────────┐
       │   │  新品审核 (B 子系统)   │  AI 评估 9 国 + admin 决策
       │   │  - AI Gemini 2.5 Pro  │
       │   │  - 9 国矩阵           │
       │   │  - admin 选要做的国家  │
       │   └────────┬────────────┘
       │            │ 通过
       ▼            ▼
┌─────────────────────────────┐
│  素材管理库 (已存在 + A 子系统补强) │  自动入库一条原始英文素材
│  - media_products             │
│  - media_items                │
└────────────┬────────────────┘
             │ 自动建任务（A 调 C 的接口）
             ▼
┌─────────────────────────────┐
│  任务中心 (C 子系统) ★ 实施中    │  父任务 + 子任务双层模型
│  - 父任务：原始视频段           │
│  - 子任务：每国家一条翻译段      │
│  - 双道审核 + 打回 + 取消        │
└──────┬──────┬───────────────┘
       │      │
       │      └─ 父任务流转 → 原始视频处理人 (D 子系统强化)
       │            ↓
       │         去字幕、去尾巴、上传成品
       │            ↓
       │         审核通过，子任务批量解锁
       ▼
┌─────────────────────────────┐
│  翻译任务池 (E 子系统强化)        │  翻译员逐国做封面/视频/文案
│  - 一人一品                    │
│  - readiness gate 提交完成       │
└────────────┬────────────────┘
             │ 子任务全 done
             ▼
┌─────────────────────────────┐
│  推送管理 (已存在)              │  pushes 页面自动列出 readiness 齐的素材
└────────────┬────────────────┘
             │
             ▼
       上线 / 投放数据接入（未来）

────────────────────────────────────
F 子系统：横向贯穿，查 task_events 出员工日产/通过率/返工率报表
────────────────────────────────────
```

---

## 1. 子系统全景索引

| 标识 | 子系统名 | 状态 | spec / plan | 优先级 |
|---|---|---|---|---|
| A | 选品 → 素材入库 + 新品识别 | 未开始 brainstorm | — | 高（紧跟 C） |
| B | 新品审核 + AI 评估矩阵 | 未开始 brainstorm | — | 中 |
| **C** | **任务中心骨架** | **brainstorm 完，实施中（30 任务，Task 1 已完成）** | [spec](2026-04-26-task-center-skeleton-design.md) / [plan](../plans/2026-04-26-task-center-skeleton.md) | **当前** |
| D | 原始素材任务库强化 | 未开始 brainstorm | — | 中 |
| E | 翻译任务池深度集成 + 推送桥强化 | 未开始 brainstorm | — | 中 |
| F | 员工日产 / 通过率 / 返工率报表 | 未开始 brainstorm | — | 低（依赖 C 的 task_events） |

**实施顺序建议**：C → A → B → D / E（可并行） → F

**为什么这个顺序**：
- C 是骨架，定义"任务"实体。后面所有子系统都挂在它上面，先做 C 才不返工
- A 接通后，admin 在选品端勾选就能自动建任务，价值最大
- B 让选品决策有数据支持，提升新品决策质量
- D / E 把 C 的 fallback（跳素材管理）替换成深度集成，体验提升
- F 最后做，因为它依赖 C 的 task_events 数据沉淀

---

## 2. 子系统详细需求

### 2.A 选品 → 素材入库 + 新品识别

**状态**：未开始 brainstorm

**业务目标**：admin 在明空选品（`/mk_selection`）页面操作"我要把这个视频做小语种搬运" → 自动产生一条素材库条目 + 自动建任务中心父任务，无需手工。

**用户原话**：
> "发起点是明空选品里面的产品和视频素材，管理员通过从里面发现适合搬运到小语种国家的视频素材，选择商品，选择素材之后，形成一条新的素材管理里面的条目"
>
> "如果商品本身已经在素材管理库，则是新增一条原始英文素材"
>
> "已经在库里的产品，那么它会在原有的素材管理库中新增一条英文视频"
>
> "本质上只是从「手工填写输入」转变为「由上游数据源自动输入」"（C 阶段手工，A 阶段自动）

**已知约束**：
1. **老品 vs 新品判定**：看素材管理库里是否已有该产品（具体判定字段需 brainstorm，可能是 `media_products` 中按产品名 / 链接 / shopify_id 匹配）
2. **老品分支**：在原 `media_products` 行下新增一条 `media_items (lang='en')`，触发 A 调用 C 的 `create_parent_task` 接口（自动沿用产品负责人作为翻译员，符合 C 的"一人一品"原则）
3. **新品分支**：先进 B 子系统（新品审核 + AI 评估），通过后再走老品分支的入库逻辑
4. **触发动作**：明空选品页对每个视频卡片增加"做小语种"按钮 / 勾选框

**字段需求**（A 入库时需要的最小字段）：
- 商品链接（要能跳到原 Shopify / 1688 / 等等）
- 商品英文标题
- 商品主图
- 视频素材文件 / URL
- 来源信息（`source` 字段已有）

**接口（A → C）**：
A 子系统通过调用 `appcore.tasks.create_parent_task(...)` 来建任务（C 已暴露此服务）。A 不直接写 tasks 表。

**待 brainstorm 的问题**：
- 老品判定的精确字段（产品名 / 产品链接 / shopify_id？）
- 新品入库流程：A 是先入 `media_products` 还是先进 B 的"新品库"，B 通过后再 promote 到 `media_products`？
- 明空选品页的 UI 改动：每个视频卡片加什么按钮？批量勾选支持吗？
- 重复检测：同一视频已经被勾过怎么办？阻止 / 提醒 / 允许重做？

### 2.B 新品审核 + AI 评估矩阵

**状态**：未开始 brainstorm

**业务目标**：明空选品页新增 "新品审核" Tab；选品过程中识别为新品的产品进入此 Tab；通过 AI 自动评估"适合在哪些小语种国家推广"；admin 据此决策上架国家。

**用户原话**：
> "在菜单栏左侧'明控选品'逻辑中，在现有页面增加两个 Tab：1. 明控选品 2. 新品审核"
>
> "新品审核：用于处理新入库的产品"
>
> "需对应一张独立的新品数据库表"
>
> "存储字段：商品链接 / 商品英文标题 / 商品主图 / 对应的视频素材"
>
> "使用 FFmpeg 将视频素材统一截断至 15 秒左右"
>
> "调用 AI 大模型（首选 Gemini 1.5 Pro，备选 GPT-5.5 等）"（注：实际选型应 Gemini 2.5 Pro）
>
> "针对单一市场进行精准评估（不建议九国市场汇总评估，以保证准确性）"
>
> "页面需展示国家维度（假设 9 个国家）的评估列，结果以「勾选（对号）」或「打叉」表示"
>
> "悬浮显示：鼠标悬停在图标上时，即时加载显示 AI 给出的具体评分及原因"
>
> "弹窗显示：点击图标后弹出窗口，展示详细的评估逻辑"
>
> "国家筛选：点击「上架」或「不上架」时弹出国家勾选框。弹窗内需预先回显 AI 的建议结果（推荐上架的国家及原因）"
>
> "默认为「不上架」（底色）。点击后变为「上架」（蓝色）"
>
> "完成勾选后，需从现有非管理员员工账号中选择一名负责人"

**已知约束**：
1. **数据库**：新增独立"新品数据库"表（不复用 `media_products`）；通过 admin 决策后再 promote 到素材库
2. **预处理**：用 ffmpeg 把视频截 15 秒短片
3. **AI 模型**：首选 Gemini 2.5 Pro（**不是** 1.5 Pro，原文有误），备选 GPT-5。走 `appcore.llm_client` 统一调用（见根 CLAUDE.md "LLM 统一调用"章节）。**新增 use_case** 例如 `new_product_review.score`
4. **评估粒度**：每个国家**单独**调用一次 AI（不汇总评估），9 国 = 9 次调用
5. **评估输出**：结构化 JSON，含每国家的 `recommended (bool)` + `score (float)` + `reason (string)`
6. **UI 矩阵**：1 行 = 1 个新品；列 = 国家。单元格 = ✓ / ✗
7. **悬浮**：hover 单元格 → tooltip 显示 score + reason 摘要
8. **点击弹窗**：详细评估逻辑（参考素材库现有的"AI 评估"详情写法，已存在）
9. **国家分发**：admin 点"上架"按钮 → 弹窗预填 AI 建议（推荐国家高亮蓝色），admin 可改 → 选择负责人（非管理员账号）→ 触发 A 入库 + C 建任务

**接口（B → A → C）**：
B 完成审核（admin 决策后） → 调用 A 的"promote 新品到素材库" → A 调用 C 的 `create_parent_task`，国家清单和翻译员从 B 的 admin 决策传递过来。

**新数据表草稿**：

```sql
CREATE TABLE new_product_reviews (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_link VARCHAR(2048),
  english_title VARCHAR(512),
  main_image_url VARCHAR(2048),
  source_video_url VARCHAR(2048) DEFAULT NULL,
  source_video_item_id INT DEFAULT NULL,        -- 如果从素材库引用
  preprocessed_short_url VARCHAR(2048) DEFAULT NULL, -- ffmpeg 截 15s 后的短片
  ai_evaluation_json JSON DEFAULT NULL,          -- {"DE": {"recommended": true, "score": 8.5, "reason": "..."}, ...}
  ai_evaluated_at DATETIME DEFAULT NULL,
  decision_status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending',
  decided_countries JSON DEFAULT NULL,           -- ["DE", "FR"] admin 决策的上架国家
  decided_translator_id INT DEFAULT NULL,
  decided_at DATETIME DEFAULT NULL,
  decided_by INT DEFAULT NULL,
  promoted_product_id INT DEFAULT NULL,          -- promote 到 media_products 后回填 id
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

**待 brainstorm 的问题**：
- "已启用的小语种国家"清单怎么动态拉取？（应该是 `media_languages.enabled=1` 的 lang 列）
- AI 评估失败 / 部分国家评估失败的兜底（重跑 / 跳过 / 手填）
- ffmpeg 截 15s 的策略（前 15s / 中间 15s / 关键帧聚合？）
- 已有素材库现成的 AI 评估写法在哪里复用？（用户提到"可参考现有视频素材库中具备条件的 AI 评估写法"——需 grep `media_products.ai_evaluation_*` 字段）
- promote 到 `media_products` 时：按"产品链接"防重复？还是允许重复？
- "悬浮"是预加载还是 hover 时再请求？
- 新品 Tab 是 admin only 还是更细粒度权限？

### 2.C 任务中心骨架 ✅ 已 brainstorm + 实施中

**状态**：spec 完成，30 任务实施计划完成，Task 1 已完成（commit `04dd2bb`），剩余 29 任务待执行。

**资料**：
- spec：[2026-04-26-task-center-skeleton-design.md](2026-04-26-task-center-skeleton-design.md)
- plan：[../plans/2026-04-26-task-center-skeleton.md](../plans/2026-04-26-task-center-skeleton.md)
- worktree：`g:\Code\AutoVideoSrtLocal\.worktrees\task-center`，分支 `feature/task-center-skeleton`

**业务目标摘要**：建立"任务中心"作为业务主线的中枢——把"原始视频处理 → 翻译"这条流水线系统化，谁该领什么任务、谁该审核、什么时候该推送，都从 UI 上看得到。

**核心模型**：
- **双层任务**：父任务 = 素材级（原始视频段），子任务 = 国家级（翻译段，N 国 = N 子任务）
- **数据表**：`tasks`（单表父子混存）+ `task_events`（审计/统计基础）
- **状态机**：父 `pending → raw_in_progress → raw_review → raw_done → all_done | cancelled`；子 `blocked → assigned → review → done | cancelled`
- **角色 / 权限**：通过 `users.permissions` JSON 加 `can_process_raw_video` / `can_translate` 两个能力位，外加菜单权限 `task_center`
- **认领制**：父任务创建为 `pending`，处理人主动"领"才进入 `raw_in_progress`
- **强制必填**：创建父任务时国家清单 + 翻译员都强制填
- **一人一品**：一个产品只配一个翻译员（吃下所有国家子任务）
- **老品自动沿用**：`media_products.user_id` 已有 → 自动作为翻译员
- **owner 联动**：`media_products.user_id` 变更 → 未完成子任务的 assignee 跟换；已完成冻结
- **打回原路返回**：reject 不带换人，换人在素材管理页做
- **半集成**：子任务详情页放"翻译"+ "翻译任务记录"按钮，跳到素材管理（带 query string 锁输入），不在任务中心内嵌完整翻译能力
- **readiness gate**：子任务"提交完成"前，后端调 `appcore.pushes.compute_readiness` 检查产物齐全
- **取消支持**：父级联取消所有非 done 子任务，已 done 子任务保留

**接口给上下游**：
- 上游（A）：调 `appcore.tasks.create_parent_task(media_product_id, media_item_id, countries, translator_id, created_by)` 等价于 admin 在 UI 手工创建
- 下游（推送）：子任务 done → 翻译员产物齐 → `compute_readiness` 自动 OK → `pushes_list` 页面自动列出，无需写新表
- F 子系统：直接查 `task_events` 表，所有事件类型都已埋好（created / claimed / raw_uploaded / submitted / approved / rejected / cancelled / completed / assignee_changed / unblocked）

### 2.D 原始素材任务库强化

**状态**：未开始 brainstorm

**业务目标**：原始视频处理人有专门的工作面板：从池子里**认领**任务 → 下载视频 → 本地预处理（去字幕、去尾巴）→ 上传成品 → 自动绑定回素材库条目。

**用户原话**：
> "建立原始素材任务库：我们需要新增一个名为「原始素材任务库」的菜单"
>
> "所有选中的新原始英文素材进入素材管理库后，如果没有对应的原始视频，该条目会自动进入「原始视频任务库」"
>
> "相关人员可以从任务库中认领并下载视频"
>
> "完成预处理（例如去字幕、去尾巴等操作）后，再进行上传"
>
> "上传后的视频会自动提交并绑定到素材库中对应条目的「原始素材」按钮下"

**与 C 的关系**：
- C 阶段已经能跑通：父任务详情页有 [ 已上传 ] 按钮，跳到素材管理上传，再回任务中心打 tag（fallback 是 prompt 输入 item id）
- D 是把这条最小桥**做强**：内嵌上传 UI、批量认领、下载队列、自动绑定

**已知约束**：
1. **菜单**：左侧栏新增 "原始素材任务库"（其实可以就是任务中心的一个 Tab：处理人专属视图）
2. **认领**：C 已实现"待认领池"；D 强化为支持批量认领、按产品分组、按时间排序等
3. **下载**：当前没有，D 增加"下载视频"按钮（直接从 `media_items.object_key` 拉 TOS 文件）
4. **预处理是本地操作**（去字幕用 Shopify Image Localizer 或类似工具？或者另开一个独立工具？需 brainstorm）
5. **上传**：D 内嵌上传 UI（不再跳素材管理）；上传成功自动写 `media_items` + 自动绑回 task

**接口给 C**：
- 替换 C §5.3 的"跳转素材管理上传"为任务中心内嵌上传
- 仍然调用 C 的 `mark_uploaded(task_id, actor_user_id)` 翻状态
- 自动写 `media_item_id` 字段（C 的 `bind_item` PATCH 接口已有）

**待 brainstorm 的问题**：
- 内嵌上传 UI 复用素材管理的上传组件，还是新写？
- 下载策略：直接 TOS pre-signed URL 给浏览器？还是先 cp 到本地？
- "去字幕、去尾巴"是手工操作还是有工具集成？
- 批量认领的粒度（一次最多几个？同一产品才能批量？）
- 处理人之间的协作（A 认领了又不做，B 能不能抢？需要"释放"动作？）

### 2.E 翻译任务池深度集成 + 推送桥强化

**状态**：未开始 brainstorm

**业务目标**：翻译员在子任务详情页直接做完所有翻译工作，不用跳来跳去；翻译完成后整条素材自动进入"待推送"。

**用户原话**：
> "翻译人员基于任务完成封面、视频、文案的翻译，并处理好相关链接"
>
> "任务标注为「完成」，整条素材状态变为「待推送」"
>
> "翻译和翻译任务管理，它跟素材管理页那两个按钮是完全一致的"
>
> "但是它能选择的输入（例如视频源和封面），对应的只能是该任务下的那一条素材，以及对应的分配国家。也就是这一个品下面确定要做的那些国家才可以勾选"

**与 C 的关系**：
- C 阶段：子任务详情页有 [ 翻译 ] [ 翻译任务记录 ] 两个按钮，跳到素材管理页（带 query string 锁定输入到该任务范围）
- E 是把这条桥**做强**：在子任务详情页**内嵌** super-workbench（封面 / 视频 / 文案 / 链接 处理 4 个 tab 在同一页）

**已知约束**：
1. 复用素材管理现有翻译组件（不重写）
2. 输入约束：视频源 / 封面 / 目标语种都受任务范围锁定，不能跳出
3. 下游已 OK：子任务 done + readiness 齐 → `pushes_list` 自动列出（C 已实现）

**接口给 C**：
- 替换 C §5.4 的"跳转素材管理翻译"为内嵌
- 仍然调用 C 的 `submit_child(task_id, actor_user_id)` 翻状态（含 readiness gate）

**待 brainstorm 的问题**：
- 内嵌的 4 个 tab 用什么前端模式（iframe / SPA 嵌入 / 新组件）？
- 翻译记录跨语种切换？还是每个子任务独立？
- 推送页面需要新增"任务来源"列吗？
- 国家分发弹窗：C 阶段已经在创建任务时一次性勾国家。E 阶段是否需要"半路加国家"功能？

### 2.F 员工日产 / 通过率 / 返工率报表

**状态**：未开始 brainstorm

**业务目标**：通过 task_events 数据沉淀，统计每个员工的工作量、质量、效率，做精准考核。

**用户原话**：
> "系统需要统计员工每天完成的任务量"
>
> "记录任务的质量数据：有多少是完全通过的，有多少需要返工"
>
> "通过这些数据进行通过率考核，从而精准掌握每个员工每天的工作状态和效率"

**与 C 的关系**：
- C 已埋好所有 task_events（`event_type` 包含 created / claimed / raw_uploaded / submitted / approved / rejected / cancelled / completed / assignee_changed / unblocked）
- F 是基于这些事件做 SQL 聚合 + 可视化，不需要新表

**已知约束**：
1. 维度：按人 / 按天 / 按产品 / 按国家 / 按子系统类型（原始视频 vs 翻译）
2. 关键指标：
   - 日产任务数（按 actor 聚合 `event_type IN ('approved', 'completed')`）
   - 通过率 = approved / (approved + rejected)
   - 返工率 = rejected / submitted
3. 入口：`/admin_usage` 类似的报表页面（已有 admin_usage / admin_ai_billing 两个类似页面可参考）

**待 brainstorm 的问题**：
- 报表页面位置（独立菜单 / 子菜单 / 整合到 admin_usage？）
- 时间粒度（小时 / 天 / 周 / 月？）
- 导出（CSV / PDF？）
- 阈值告警（连续 3 天没产 / 通过率 < 80%？）
- 个人视图 vs 全员视图（普通员工是否能看自己的）

---

## 3. 已锁定决定清单（贯穿子系统）

> 这些决定在 brainstorm C 子系统时锁定，但**适用于所有子系统**。后续推 A/B/D/E/F 时不要重新质疑这些决定，除非发现实际矛盾。

### 3.1 任务模型与状态

| # | 决定 | 影响子系统 |
|---|---|---|
| 1 | **双层任务模型**：父=素材级（原始视频段）+ 子=国家级（翻译段，每国一条） | C / A / D / E / F |
| 2 | **两道审核**：原始视频上传后 + 翻译提交后，都必须管理员审核 | C / D / E |
| 3 | 创建父任务时，国家清单 + 翻译员**强制必填**，子任务一并物化为 `blocked` 状态 | C / A / B |
| 4 | 父任务原始视频处理人**不必填**，进认领池，由有 `can_process_raw_video` 权限的人主动认领 | C / D |
| 5 | **一人一品**：一个产品只配一个翻译员，吃下所有国家子任务 | C / A / B / E |
| 6 | 老品（`media_products.user_id` 已存在 + 该 user 仍 active + 有 `can_translate`）→ 自动沿用作为翻译员；新品 → admin 选 | C / A / B |
| 7 | **打回 = 原路返回**，任务中心不带换人按钮；换人在素材管理做（`update_product_owner` 入口） | C / A / D / E |
| 8 | `media_products.user_id` 变更 → **状态非 done/cancelled** 的子任务的 assignee 自动跟换；已 done / cancelled 冻结快照 | C |
| 9 | **取消（cancel）** 是终态：父取消时级联取消所有非 done 子任务，已 done 保留；子取消不影响父 | C / A / D / E |

### 3.2 权限与角色

| # | 决定 | 影响子系统 |
|---|---|---|
| 10 | 角色保持现有 `users.role ∈ {superadmin, admin, user}`，**不加新 role 值** | 全部 |
| 11 | 任务能力通过 `users.permissions` JSON 加位实现：<br>- `task_center`：菜单可见性<br>- `can_process_raw_video`：能领原始视频任务<br>- `can_translate`：能被指派为翻译员 | C / D / E |
| 12 | 一个用户可同时拥有两种能力（一人多职） | C / D / E |

### 3.3 集成与桥接

| # | 决定 | 影响子系统 |
|---|---|---|
| 13 | 子任务页**半集成**：复用素材管理的"翻译"+ "翻译任务记录"按钮，输入受任务范围锁定（query string 传 `from_task` / `product` / `item` / `lang`） | C / E |
| 14 | 父任务"上传原始视频" = 跳到素材管理上传页（带 `from_task` query），上传成功跳回任务中心标 tag；E/D 阶段可强化为内嵌 | C / D |
| 15 | "完成"按钮 = 后端调 `appcore.pushes.compute_readiness` **gate 检查**，封面 / 视频 / 文案缺哪件就拦下 | C |
| 16 | "待推送"**不是** DB 字段，是 `pushes_list` 实时算出来的 — 翻译员把素材产物齐了就自动出现，不需要任务中心写额外标志 | C / E |
| 17 | 国家代码统一**大写 ISO**（DE / FR / JA / NL / SV / FI / ...）；与 `media_languages.lang` 列对齐（**实施时 grep 验证大小写**，必要时 normalize） | 全部 |
| 18 | 审计 / 事件流统一写 `task_events` 表，`event_type` 枚举包含 created / claimed / raw_uploaded / submitted / approved / rejected / cancelled / completed / assignee_changed / unblocked | C / F |

### 3.4 UI / UX

| # | 决定 | 影响子系统 |
|---|---|---|
| 19 | 任务中心 UI 走 **Ocean Blue** 设计系统（参考根 CLAUDE.md "Frontend Design System"），布局参照素材管理列表风格 | C / E |
| 20 | 任务中心默认页有"**待派单素材**"Tab，admin 从这入口"一键创建任务"；A 阶段后变为自动入库，admin 操作不变 | C / A |
| 21 | 同时拥有两个能力位的用户在"我的任务" Tab 看到混合（父 + 子任务） | C / D / E |
| 22 | 打回 / 取消 modal：reason **必填 ≥10 字符** | C / D / E |
| 23 | admin 自审（actor == assignee）**不拦**——admin 角色就是有这个特权 | C |

### 3.5 LLM 调用

| # | 决定 | 影响子系统 |
|---|---|---|
| 24 | 所有 LLM 调用走 `appcore.llm_client`（见根 CLAUDE.md "LLM 统一调用"），新业务功能在 `appcore/llm_use_cases.py` 加 use_case，不直接 `from openai import` | B（首要）/ 其他 |

---

## 4. 跨子系统统一约定

### 4.1 命名

- **数据表**：`tasks` / `task_events`（C 已建）；后续模块加表用 snake_case，不加 `task_center_` 前缀（保持简洁）
- **Blueprint**：`tasks`（C 已建，前缀 `/tasks`）；其他模块用 `mk_selection_*` / `new_product_review_*` / 等等
- **服务模块**：`appcore/tasks.py`（C 已建）；其他模块按业务命名 `appcore/<domain>.py`
- **前端模板**：`tasks_list.html`（C 已建）；其他模块 `<domain>_list.html` / `<domain>_detail.html`
- **国家代码常量**：`SUPPORTED_COUNTRIES = ('DE','FR','JA','NL','SV','FI','...')`（动态从 `media_languages` 拉，不硬编码）

### 4.2 术语表

| 中文 | 英文 / 字段 | 含义 |
|---|---|---|
| 父任务 | parent task / `tasks WHERE parent_task_id IS NULL` | 素材级，承载原始视频处理段 |
| 子任务 | child task / `tasks WHERE parent_task_id IS NOT NULL` | 国家级，承载翻译段 |
| 待派单素材 | dispatch pool | `media_products` 中无活跃父任务的产品 |
| 待认领池 | claim pool | `tasks` 中 `status='pending' AND parent_task_id IS NULL` |
| 一人一品 | one translator per product | 同一产品所有国家子任务 → 同一翻译员 |
| 老品 | old product | `media_products.user_id` 已有合格 user |
| 新品 | new product | `media_products` 不存在该产品 / `user_id` 失效 |
| 高层状态 | high-level status | 进行中 / 已完成 / 终止（前端 rollup，不入库） |
| 能力位 | capability | `users.permissions[<code>]` JSON 中的 bool flag |
| 原路返回 | original-route return | 打回时不换 assignee，回原人 |
| owner 联动 | owner cascade | `update_product_owner` 时自动同步未完成子任务的 assignee |
| readiness gate | — | submit 子任务前用 `compute_readiness` 检查产物齐全 |
| 半集成 | half-integration | 任务中心 UI 用现有素材管理按钮（跳转），不内嵌完整翻译能力 |

### 4.3 文件规范

- **新 spec**：`docs/superpowers/specs/YYYY-MM-DD-<subsystem>-design.md`
- **新 plan**：`docs/superpowers/plans/YYYY-MM-DD-<subsystem>.md`
- **新 migration**：`db/migrations/YYYY_MM_DD_<purpose>.sql`，启动时自动 apply（`appcore/db_migrations.py`）
- **新测试**：`tests/test_<domain>_*.py`，pytest 风格

---

## 5. 已存在的相关代码（实施新模块时参考）

| 文件 | 责任 | 与新模块的关系 |
|---|---|---|
| `web/templates/mk_selection.html` | 明空选品页 | A / B 改造对象 |
| `web/routes/medias.py` | 素材管理路由 | A 入库时 upsert |
| `web/templates/medias_list.html` | 素材库列表 | E 复用翻译按钮 |
| `web/templates/_medias_edit_modal.html` | 素材编辑 modal | C/E 跳转目标，需检查 query 锁输入 |
| `web/templates/_medias_edit_detail_modal.html` | 素材详情 modal | C/E 跳转目标 |
| `appcore/medias.py` | 素材 service | C `update_product_owner` 已加 owner-cascade hook |
| `appcore/medias.py:update_product_owner` | 改产品负责人单一入口 | C/A 联动入口 |
| `appcore/pushes.py:compute_readiness` | 推送就绪判定 | C readiness gate |
| `web/routes/pushes.py` | 推送页面 | C 下游（子任务 done → readiness 自动 OK） |
| `appcore/llm_client.py` | LLM 统一调用 | B 必走 |
| `appcore/llm_use_cases.py` | use_case 注册表 | B 加新条目 |
| `appcore/permissions.py` | 权限模板 | C 已加 3 个 permission |
| `appcore/db_migrations.py` | 启动时自动 apply migration | 全部模块 |
| `tests/conftest.py` | 测试 fixture（含 `logged_in_client`） | 全部模块测试 |
| `tests/test_multi_translate_routes.py` | 集成测试样板 | 全部模块测试参考 |

---

## 6. 推进新模块时的标准流程

> 给未来的会话（Claude / Codex）：你被要求继续推进 A / B / D / E / F 中某一个时，请按以下流程：

### Step 1：读关键背景
1. 通读本文件（全文）— 理解全局意图 + 锁定决定
2. 读 [C 的 spec](2026-04-26-task-center-skeleton-design.md) — C 的实现细节是后续模块的接口边界
3. 读对应子系统在第 2 节的"用户原话"和"待 brainstorm 的问题"

### Step 2：调用 brainstorming skill
- `superpowers:brainstorming`
- 把第 2 节里该子系统的"待 brainstorm 的问题"作为出发点
- **一次问用户一个问题**（用户硬性要求）
- 锁定决定后写入第 3 节"已锁定决定清单"

### Step 3：写 spec
- 输出到 `docs/superpowers/specs/YYYY-MM-DD-<subsystem>-design.md`
- spec 要包含：范围 / 数据模型 / 状态机（如有）/ 路由 / 交互 / 测试策略 / 接驳点
- 引用本文件作为上位规划

### Step 4：写 plan + 实施
- `superpowers:writing-plans` → 30-task 分解
- 在新 worktree 隔离开发（参考 C 的做法：`.worktrees/<subsystem>` + `feature/<subsystem>` 分支）
- 用 `superpowers:subagent-driven-development` 推任务

### Step 5：完成后回写本文件
- 子系统状态从"未 brainstorm"→"实施中"→"已完成"
- 链接到 spec 和 plan
- 新锁定的决定登记到第 3 节
- 与本文件中其他子系统冲突的部分要解决（要么改本文件，要么 escalate 给用户）

---

## 7. 推送 & 投放（未来）

用户原话：
> "任务提交后进入推送管理，之后再进入投放环节。后续我们还可以考虑如何接入投放数据。"

**当前**：
- 推送管理页 `/pushes` 已存在，按 readiness 自动列出待推送素材
- 投放数据接入**未在本文档范围**——属于完全独立的新业务，到时另立项

---

## 8. 维护者备忘

- **本文档不是"做完一次就归档"** — 它是活文档。每次 brainstorm 一个新子系统、每次锁定一个新决定，都要回来更新。
- **冲突优先级**：本文件 > 子系统 spec > 子系统 plan > 代码注释
- **过时风险**：如果某个章节超过 30 天没更新但相关子系统仍在迭代，应该 challenge 它的有效性
- **生成日期**：2026-04-26
- **下次大修触发条件**：A 子系统 brainstorm 完，或 C 子系统全 30 任务实施完，或用户提出大需求变更
