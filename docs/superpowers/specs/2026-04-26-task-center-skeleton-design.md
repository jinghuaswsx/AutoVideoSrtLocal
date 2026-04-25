# 任务中心骨架（C 子系统）设计文档

- **日期**：2026-04-26
- **作者**：与 Claude 协作
- **状态**：已完成 brainstorm，待实施计划
- **范围标识**：子系统 **C — Task Center Skeleton**

---

## 0. 上位规划与本 spec 定位

整个"明空选品 → 素材入库 → 任务流转 → 推送"业务主线，被拆为 6 个独立子系统，各自有独立 spec：

| 标识 | 子系统 | 状态 |
|---|---|---|
| **A** | 选品 → 素材入库 + 新品识别 | 待立 spec |
| **B** | 新品审核（明空选品 Tab + AI 评估矩阵 + ffmpeg 截 15s） | 待立 spec |
| **C** | **任务中心骨架（本 spec）** | **本文档** |
| **D** | 原始素材任务库（认领队列 / 批量下载 / 上传强化） | 待立 spec |
| **E** | 翻译任务池高度集成 + 国家分发弹窗 + 推送桥接强化 | 待立 spec |
| **F** | 员工日产 / 通过率 / 返工率报表 | 待立 spec |

**实施顺序**：C → A → D / B → E → F（C 是骨架，后面所有子系统都挂到它上面）。

C 的核心交付：把"任务"这个实体定义出来 + 用户能"手工维护"地把任务全流程跑通。完成后，A 子系统接通时只是把 C 的"手工创建任务"动作换成"上游自动推送"。

---

## 1. 范围与边界

### 1.1 本 spec 做什么

1. 新增 `tasks` 表 + `task_events` 审计表
2. 新增"**任务中心**"顶级菜单 + 单页（含 3 个 tab：`我的任务` / `全部任务` / `待派单素材`）
3. 后端 Blueprint `web/routes/tasks.py` + service 层 `appcore/tasks.py`，覆盖：
   - 创建父任务 + 一并物化子任务
   - 处理人认领原始视频
   - 标"已上传"（含跳转素材管理上传后回跳）
   - 双道审核：原始视频审核 + 翻译审核
   - 打回（原路返回）
   - 取消（父级联，已 done 子任务保留）
   - 翻译员"完成"按钮（带 `compute_readiness` gate）
   - 子任务自动解锁（父审核通过时）
   - 父任务自动 `all_done`（所有子 done 时）
4. 用户能力位扩展：`users.permissions` JSON 增加 `can_process_raw_video` / `can_translate` 两个 key
5. 联动：素材管理改 `media_products.user_id` 时同步**状态非 `done`/`cancelled`** 的子任务的 `assignee_id`

### 1.2 本 spec 不做什么

- ❌ 选品自动推送（A）：手工维护
- ❌ 新品 AI 评估矩阵（B）：另立 spec
- ❌ 原始素材下载队列 / 批量认领（D）：仅做"跳转素材管理上传"作最小桥
- ❌ 翻译流深度集成 / 国家分发弹窗（E）：子任务页只放 2 个跳转按钮 + 完成按钮
- ❌ 员工产能报表（F）：本 spec 只把 `task_events` 写好，让 F 后续能查
- ❌ 站内 / 邮件 / IM 通知：靠用户主动开"我的任务"页

### 1.3 关键依赖

- `media_products`（含 `user_id` 作为产品负责人；**注意**：本仓无 `owner_id` 列，"负责人"= `user_id`，名字通过 `_media_product_owner_name_expr()` 在 `appcore/medias.py` join `users` 表算出）
- `media_items` / `media_languages` / `media_copywritings`
- `appcore.pushes.compute_readiness(item, product)` + `is_ready(readiness)`
- `users.permissions` JSON 模型（已存在，2026-04-25 migration）

### 1.4 风险点（实施时必须先核）

- `media_products.user_id` 的"修改产品负责人"入口是否只有一处？多处必须每处都补 owner-cascade 钩子，漏一处即数据静默不一致
- `_medias_edit_modal.html` / `_medias_edit_detail_modal.html` 的"翻译"+ "翻译任务记录"按钮可否最小改造支持 query string 锁定输入？改不动就退化为子任务页内嵌最小翻译表单
- 素材管理上传成功 callback 可否被无创扩展接 redirect 回任务中心？不行就退化为用户上传完手动回任务中心点"我已上传"
- 现有 `web/routes/task.py` 蓝图的 URL 前缀是什么？撞了就把新蓝图前缀改成 `/task_center`

### 1.5 命名

- 数据表：`tasks` / `task_events`
- 蓝图：`tasks`，前缀 `/tasks`（与 `task.py` 的 `task` 蓝图区分；如撞 URL 改 `/task_center`）
- 模板：`tasks_list.html`
- service 模块：`appcore/tasks.py`
- 国家代码：大写 ISO（`DE` / `FR` / `JA` / `NL` / `SV` / `FI` …），与 `media_languages.lang` 列对齐——实施前 grep 验证当前存储是大写还是小写

---

## 2. 数据模型

### 2.1 `tasks` 表

```sql
CREATE TABLE tasks (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  parent_task_id  INT DEFAULT NULL,                  -- NULL = 父任务，否则 = 子任务
  media_product_id INT NOT NULL,                     -- FK media_products.id
  media_item_id   INT DEFAULT NULL,                  -- 父：英文原片 item id（创建可空，上传后回填）；子：与父同步
  country_code    VARCHAR(8) DEFAULT NULL,           -- 子任务必填；父为 NULL
  assignee_id     INT DEFAULT NULL,                  -- 父：处理人，认领前 NULL；子：翻译员，创建即填
  status          VARCHAR(24) NOT NULL,
  last_reason     TEXT DEFAULT NULL,                 -- 最近一次打回 / 取消的原因（进 review 时清空）
  created_by      INT NOT NULL,                      -- 创建者（admin）
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  claimed_at      DATETIME DEFAULT NULL,
  completed_at    DATETIME DEFAULT NULL,
  cancelled_at    DATETIME DEFAULT NULL,
  KEY idx_parent (parent_task_id),
  KEY idx_product (media_product_id),
  KEY idx_assignee_status (assignee_id, status),
  KEY idx_status_parent (status, parent_task_id),
  UNIQUE KEY uk_parent_country (parent_task_id, country_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**字段语义**：
- `media_product_id` 在父子任务都冗余存：子任务 where 过滤一句搞定，无需 join 父表
- `assignee_id` 不存历史快照表：完成时不再 update 即冻结
- `country_code` 大写 ISO
- `media_item_id` 父任务上为 NULL 是合法初始态，进 `raw_review` 前必须有值
- `last_reason` 复用一个字段承载打回 + 取消原因，节省字段数

### 2.2 父任务状态枚举

```
pending          ← 创建后，等处理人认领；assignee=NULL
raw_in_progress  ← 处理人已认领
raw_review       ← 处理人按"已上传"，等管理员审核
raw_done         ← 审核通过，子任务批量解锁；父继续等所有子 done
all_done         ← 所有子任务都 done（自动写入）
cancelled        ← 管理员取消（终态）
```

打回路径：`raw_review → raw_in_progress`（assignee 不变，`last_reason` 写入）

### 2.3 子任务状态枚举

```
blocked    ← 父任务创建时一并物化；assignee 已设
assigned   ← 父任务 raw_done 时批量解锁
review     ← 翻译员按"提交完成"（readiness gate 通过），等管理员审核
done       ← 审核通过（终态，assignee 此刻冻结）
cancelled  ← 管理员取消（终态）
```

打回路径：`review → assigned`（assignee 不变，`last_reason` 写入）

### 2.4 高层状态 rollup（仅前端，不入库）

- `进行中` = 所有非终态
- `已完成` = `done` / `all_done`
- `终止` = `cancelled`

### 2.5 `task_events` 表

```sql
CREATE TABLE task_events (
  id            BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id       INT NOT NULL,
  event_type    VARCHAR(32) NOT NULL,        -- created / claimed / raw_uploaded / submitted / approved / rejected / cancelled / completed / assignee_changed / unblocked
  actor_user_id INT DEFAULT NULL,            -- 触发用户；系统触发为 NULL
  payload_json  JSON DEFAULT NULL,           -- 打回原因 / 旧→新 assignee / 级联子数 / readiness 缺项 / ...
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_task (task_id, created_at),
  KEY idx_actor (actor_user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 2.6 Migration 文件

`db/migrations/2026_04_26_add_tasks_tables.sql`：含两张表 + `appcore/permissions.py` 默认模板更新（`can_process_raw_video=False` / `can_translate=False`，superadmin / admin 默认 True）。

---

## 3. 状态机与转换规则

### 3.1 父任务状态机

```
            create_parent_task() by admin
                  │
                  ▼
            ┌──────────┐
            │ pending  │
            └────┬─────┘
                 │ claim() by 任意 raw_video_op
                 ▼
        ┌───────────────────┐
        │ raw_in_progress   │
        └────┬──────────────┘
             │ mark_uploaded() by assignee（前置 media_item_id IS NOT NULL）
             ▼
        ┌───────────────┐
        │ raw_review    │
        └───┬───────┬───┘
   approve()│       │reject(reason) by admin
            │       └──────────────► raw_in_progress（同 assignee）
            ▼
        ┌────────────┐
        │ raw_done   │  ← 触发：批量 update children blocked → assigned
        └────┬───────┘
             │ 当所有 children done 时自动
             ▼
        ┌────────────┐
        │ all_done   │
        └────────────┘
```

任意非终态 + admin → `cancelled`（级联取消所有非 done 子任务）。

### 3.2 子任务状态机

```
       创建（与父任务同时）
                  │
                  ▼
            ┌──────────┐
            │ blocked  │
            └────┬─────┘
                 │ 父任务 raw_done 触发
                 ▼
            ┌────────────┐
            │ assigned   │
            └────┬───────┘
                 │ submit() by assignee（前置 compute_readiness 通过）
                 ▼
            ┌────────────┐
            │ review     │
            └───┬─────┬──┘
       approve()│     │reject(reason) by admin
                │     └──────────────► assigned（同 assignee）
                ▼
            ┌────────┐
            │ done   │  ← assignee 冻结
            └────────┘
```

任意非终态 + admin → `cancelled`（父状态不变）。

### 3.3 谁能做什么

| 动作 | 父/子 | 触发者 | 前置 |
|---|---|---|---|
| `create_parent_task` | 父 | admin | 选定 product + countries + translator + (item 可空) |
| `claim` | 父 | 任意 `can_process_raw_video=True` 用户 | status=`pending` |
| `mark_uploaded` | 父 | 父.assignee 自己 | status=`raw_in_progress` 且 `media_item_id IS NOT NULL` |
| `approve` (raw) | 父 | admin | status=`raw_review` |
| `reject` (raw) | 父 | admin（可以是自己 assignee） | status=`raw_review`，reason 必填 ≥10 字符 |
| `cancel` | 父 | admin | status ∈ {pending, raw_in_progress, raw_review, raw_done}，reason 必填 ≥10 字符 |
| `submit` | 子 | 子.assignee 自己 | status=`assigned` 且 `compute_readiness` 通过 |
| `approve` (translation) | 子 | admin | status=`review` |
| `reject` (translation) | 子 | admin | status=`review`，reason 必填 ≥10 字符 |
| `cancel` | 子 | admin | status ∈ {blocked, assigned, review}，reason 必填 ≥10 字符 |

### 3.4 自动触发

| 触发时机 | 动作 |
|---|---|
| 父 `raw_review → raw_done` | UPDATE tasks SET status='assigned', updated_at=NOW() WHERE parent_task_id=? AND status='blocked' |
| 子最后一条 → `done` | UPDATE 父 SET status='all_done', completed_at=NOW() WHERE id=? AND NOT EXISTS (子 status NOT IN ('done','cancelled')) AND EXISTS (子 status='done') |
| 父 `mark_uploaded` 时 `media_item_id IS NULL` | 自动按 `media_product_id + lang='en' + deleted_at IS NULL` 取最新一条回填 |
| `media_products.user_id` 变更（owner cascade） | UPDATE tasks SET assignee_id = new_user_id WHERE media_product_id=? AND parent_task_id IS NOT NULL AND status NOT IN ('done', 'cancelled') |

### 3.5 联动钩子实现

- 应用层钩子，不用 DB trigger
- 在 `appcore/medias.py` 现有"修改产品负责人"的 service 函数末尾调用 `appcore.tasks.on_product_owner_changed(product_id, new_user_id, actor_id)`
- `tasks.py` 函数负责 UPDATE + 写 `task_events(event_type='assignee_changed', payload_json={old, new})`
- **风险**：实施时必须 grep 所有 `UPDATE media_products SET user_id` 语句，每处都补钩子

### 3.6 边界与错误处理

- **并发认领**：用乐观锁——`UPDATE tasks SET assignee_id=?, status='raw_in_progress', claimed_at=NOW() WHERE id=? AND status='pending'`，affected_rows=0 → 返回 409 "已被他人认领"
- **打回循环**：不限次数，每次写 `task_events`
- **审核员 = assignee**：不拦
- **`media_item_id` 一致性**：进 `raw_review` 强制 NOT NULL；进 `assigned`（子任务解锁）也必须有
- **取消的不可逆**：UI 弹 confirm 后不可撤销，cancelled 状态不能再回到 pending

---

## 4. 页面与路由

### 4.1 菜单

`web/templates/layout.html` 左侧栏新增 **"任务中心"** 顶级菜单，所有登录用户可见。

### 4.2 路由表（Blueprint `tasks`，前缀 `/tasks`）

| 方法 | 路径 | 用途 | 权限 |
|---|---|---|---|
| GET  | `/tasks/`                          | 主页 | login_required |
| GET  | `/tasks/api/list`                  | 任务列表 JSON | login_required |
| GET  | `/tasks/api/dispatch_pool`         | "待派单素材"列表 | admin |
| POST | `/tasks/api/parent`                | 创建父任务 + 子任务 | admin |
| POST | `/tasks/api/parent/<id>/claim`     | 处理人认领 | `can_process_raw_video` |
| POST | `/tasks/api/parent/<id>/upload_done` | 标"已上传" | parent.assignee |
| POST | `/tasks/api/parent/<id>/approve`   | 审核通过原始视频 | admin |
| POST | `/tasks/api/parent/<id>/reject`    | 打回原始视频 | admin |
| POST | `/tasks/api/parent/<id>/cancel`    | 取消父任务 | admin |
| POST | `/tasks/api/child/<id>/submit`     | 翻译员提交（gate） | child.assignee |
| POST | `/tasks/api/child/<id>/approve`    | 审核通过翻译 | admin |
| POST | `/tasks/api/child/<id>/reject`     | 打回翻译 | admin |
| POST | `/tasks/api/child/<id>/cancel`     | 取消子任务 | admin |
| GET  | `/tasks/api/<id>/events`           | 审计事件流 | login_required |
| PATCH | `/tasks/api/parent/<id>/bind_item` | 上传后回跳时绑定 media_item_id | parent.assignee |

### 4.3 Tab 结构

```
顶部：标题 + 全局筛选（高层状态 + 关键词）
Tab 切换：[ 我的任务 ]   [ 全部任务 ]   [ 待派单素材 ]
表格区
```

#### Tab 1: `我的任务`（默认 tab，全部用户可见）

后端按当前用户能力位过滤：
- `can_process_raw_video=True` → 显示 `(assignee_id=self) OR (status='pending' AND parent_task_id IS NULL)` 的父任务
- `can_translate=True` → 显示 `(assignee_id=self) AND parent_task_id IS NOT NULL` 的子任务
- 同时拥有两个能力位 → 同时显示，表格用"任务类型"列区分
- admin / superadmin → 此 tab 等价于"我作为 assignee 持有的"，**不**等于全部

列：产品名 / 类型（父/子）/ 国家（子任务才有）/ 高层状态 / 子状态 / 创建时间 / 操作按钮

#### Tab 2: `全部任务`（admin / superadmin）

不带 assignee 过滤，多出"负责人"列；筛选支持 by 产品 / 国家 / 状态 / 负责人。

#### Tab 3: `待派单素材`（admin / superadmin）

数据来源：`media_products` JOIN `media_items` WHERE 该产品**没有任何活跃父任务**（不存在 status NOT IN ('cancelled','all_done') 的 parent task）。

列：产品名 / 主图 / 已有英文 item 数 / [ 创建任务 ] 按钮。

### 4.4 权限装饰器

`appcore/permissions.py` 新增 helper：
- `@require_capability('can_process_raw_video')`
- `@require_capability('can_translate')`

复用现有 `is_admin` / `current_user.permissions` 模型。

---

## 5. 关键交互

### 5.1 创建任务弹窗

```
标题：为「<产品名>」创建任务
只读区：产品主图缩略图 / 链接 / 关联英文素材列表
[选 "原始素材"]：
   该产品的 media_items (lang='en') 列表，单选
   一条都没有 → 提示"请先去素材管理上传英文原片" + 跳转链接
[选 "翻译员"]：
   老品（media_products.user_id 已存在 + 该 user 还在 + 有 can_translate）：
       自动回填 + 锁定，提示"已自动沿用产品负责人 <名字>"
   新品：下拉列所有 can_translate=True 的非管理员 user
[勾 "目标国家"]：
   多选 checkbox，列已启用小语种国家（来源 media_languages 表 lang 列，排除 en）
[备注]（选填）
按钮：[ 取消 ]  [ 创建并分配 ]
```

后端事务：
1. INSERT 父任务（status=pending, assignee_id=NULL, media_item_id=选中那条）
2. INSERT N 条子任务（status=blocked, assignee_id=选中翻译员, country_code=每个勾选国家）
3. INSERT N+1 条 task_events (`created`)
4. 返回新建父任务 id

校验：国家清单 ≥ 1；翻译员必填且必须 `can_translate=True`；原始素材 item 必填。

### 5.2 任务详情抽屉

```
顶部：产品名 + 高层状态徽章
商品维度信息（同步自 media_products）：主图 / 链接 / 标题 / 已分配国家 / 创建人 / 当前负责人
父任务卡片：
   状态 / assignee / 已上传 item 链接（点击跳素材管理详情）
   按钮区（按 §5.5 表）
子任务列表（一国一行）：
   国家旗 / status / assignee / 操作按钮 + 折叠 events
审计流（折叠）：task_events 倒序时间线
```

### 5.3 父任务"已上传"跳转流程

assignee 点 [ 已上传 ]：
1. `tasks.media_item_id` 已存在 → 弹 confirm "确认这条素材是去字幕处理后的成品？" → 转 `raw_review`
2. 为空 → `window.open` 素材管理上传页（带 `?from_task=<id>&product=<pid>&lang=en`）→ 用户上传完跳回 `/tasks/?focus=<task_id>&new_item=<item_id>`，详情抽屉自动调 PATCH `/tasks/api/parent/<id>/bind_item` 回填，再走 confirm

跳转策略：新标签开素材管理；不做 SPA 嵌入。

**风险**：素材管理上传成功 callback 改造可行性。改不动则退化为"用户上传完手动回任务中心，点 [ 我已上传 ]，前端 LEFT JOIN media_items 让用户确认绑定"。

### 5.4 子任务 [ 翻译 ] / [ 翻译任务记录 ] 按钮

复用素材管理详情模态框里同名按钮逻辑。约束方式：跳转 URL 带 query：
- `from_task=<child_task_id>`
- `product=<media_product_id>`
- `item=<media_item_id>`
- `lang=<country_code>`

素材管理那边检测到这些 query 时，对应 select / multi-select 控件**预填 + 禁用**。

**风险**：`_medias_edit_detail_modal.html` 改造代价。改不动则退化为子任务页内嵌最小翻译触发表单（仅支持单素材 + 单语种）。

### 5.5 子任务 [ 提交完成 ] gate

assignee 点 [ 提交完成 ]：
1. 后端找该子任务对应的目标语种 `media_items`（`product_id + lang=country_code + deleted_at IS NULL` 取最新）
2. 找不到 → 422 "还没上传 <country> 的视频文件"
3. 找到 → 调 `appcore.pushes.compute_readiness(item, product)`
4. `is_ready(readiness)` True → status `assigned → review`，写 task_event `submitted`
5. False → 422 + 缺项明细列表，前端弹"还差以下产物：…，请先去素材管理补齐"

### 5.6 按钮可见性表

| 当前状态 | 父任务 | 子任务 |
|---|---|---|
| `pending`（父） | 处理人见 [ 认领 ]；admin 见 [ 取消 ] | — |
| `raw_in_progress`（父） | assignee 见 [ 已上传 ]；admin 见 [ 取消 ] | — |
| `raw_review`（父） | admin 见 [ 通过 ] [ 打回 ] [ 取消 ] | — |
| `raw_done`（父） | admin 见 [ 取消 ] | — |
| `blocked`（子） | — | 无操作 |
| `assigned`（子） | — | assignee 见 [ 翻译 ] [ 翻译任务记录 ] [ 提交完成 ]；admin 见 [ 取消 ] |
| `review`（子） | — | admin 见 [ 通过 ] [ 打回 ] [ 取消 ] |
| 终态 | 无按钮 | 无按钮 |

### 5.7 打回 / 取消 弹窗

- 打回 modal：textarea 必填 reason ≥10 字符
- 取消 modal：textarea 必填 reason ≥10 字符；提交前 confirm "取消后非已完成的子任务也会一起终止，不可撤销，确认？"

---

## 6. 测试策略

### 6.1 单元测试（`tests/test_tasks_*.py`）

| 文件 | 内容 |
|---|---|
| `test_tasks_state_machine.py` | 状态转换合法性；并发认领乐观锁；自动 unblock；自动 all_done；级联 cancel |
| `test_tasks_owner_cascade.py` | 未完成子任务跟换；已 done 不变；已 cancelled 不变；正确写 events |
| `test_tasks_readiness_gate.py` | submit gate：通过放行；不通过返缺项明细；目标语种 item 不存在 |
| `test_tasks_permission.py` | capability 装饰器；403；admin 自审不拦 |

### 6.2 集成测试（`tests/test_tasks_routes.py`，参考 `tests/test_multi_translate_routes.py`）

| 用例 | 路径 |
|---|---|
| HappyPath 1 | admin 创建 → 处理人认领 → 标已上传 → 通过 → 子解锁 → 翻译员逐国 submit → 通过 → all_done |
| HappyPath 2 | 老品创建：自动回填 owner，翻译员锁定 |
| RejectPath 1 | 父打回 → 回 raw_in_progress（同 assignee）→ 重传 → 通过 |
| RejectPath 2 | 子打回 → 回 assigned → 重 submit |
| CancelPath 1 | 父取消 → 非 done 子级联 cancelled，已 done 保留 |
| CancelPath 2 | 单子取消 → 父状态不变 |
| OwnerCascade | 改 owner → 未完成子 assignee 跟换 + 写 event |
| ConcurrencyClaim | 并发 claim 第二次返 409 |
| ReadinessGate | submit 缺项 → 422 + 明细 |
| PermissionDeny | 非 capability 操作 → 403 |

### 6.3 手动验收清单

- [ ] 任务中心菜单出现，所有用户可进
- [ ] 待派单素材 Tab 列出"无活跃父任务"的产品
- [ ] 创建任务弹窗：老品自动回填翻译员且禁用
- [ ] 创建任务弹窗：新品翻译员下拉只显示有 `can_translate` 的非 admin 用户
- [ ] 处理人在"我的任务"看到 pending 池 + 自己已认领的
- [ ] 翻译员在"我的任务"只看到 product owner = 自己且 status ≠ blocked 的子任务
- [ ] 父任务"已上传"按钮跳素材管理上传，回跳后绑定正确
- [ ] 子任务"翻译"按钮跳素材管理，输入控件确实被锁定到该任务范围
- [ ] readiness gate：故意不传封面就 submit，前端弹"差封面"
- [ ] 打回后状态回退正确，reason 显示在审计流
- [ ] 取消父任务后已 done 子任务保留 done，其他变 cancelled
- [ ] 在素材管理改产品负责人 → 任务中心未完成子 assignee 跟变；已 done 不变
- [ ] 同时拥有两个能力位的用户在"我的任务"看到混合（父 + 子）

### 6.4 测试基础设施

`conftest.py` 增加 fixture：
- `make_user(role, can_process_raw_video, can_translate)`
- `make_product(owner_id, en_item=True)`
- `make_parent_task(product, translator, countries=['DE','FR'])`

### 6.5 不在 C 范围

- 性能 / 压力（任务量小）
- Playwright 自动化（手动验收覆盖）
- 跨浏览器测试

### 6.6 CI

复用现有 smoke 风格：`pytest tests/test_tasks_routes.py -q` 必须 < 30s 通过。

---

## 7. 决策日志

12 条 + 1 条修订 + 1 条增量，brainstorming 期间逐条与用户确认：

1. 双层模型（父=素材级 / 子=国家级）
2. 两道审核都要
3. 子任务在父任务创建时一并物化为 `blocked`，原始素材审核通过批量解锁
4. 父任务创建时国家清单 + 翻译员强制必填
5. 一人一品（产品级 owner，子任务全部给同一翻译员）
6. 老品自动沿用 `media_products.user_id`，新品 admin 选
7. 打回 = 原路返回；任务中心不带换人按钮；换人在素材管理做
8. owner 变更级联：未完成跟换，已完成冻结
9. 角色 = `users.permissions` JSON 加 `can_process_raw_video` / `can_translate`
10. 子任务半集成：复用素材管理"翻译"+ "翻译任务记录"按钮，输入受任务范围约束
11. 父任务"上传原始视频"= 跳素材管理上传，回任务中心标 tag
12. 入口 = 任务中心默认页"待派单素材"列表 + 一键创建任务弹窗
- **修订** 11': 原始视频处理人不必填，进认领池
- **增量** §3-增：增加 `cancelled` 终态 + admin cancel 动作；父级联非 done 子，已 done 保留

---

## 8. 后续 spec 接驳点

C 完成后，A / B / D / E / F 子系统接入位：

- **A（选品 → 素材入库）**：增加一个内部接口 `appcore.tasks.create_from_selection(product_id, item_id, countries, translator_id)`，A 子系统调用即可——和 admin 手工调"创建任务"等价路径
- **B（新品 AI 评估）**：B 输出"国家 + 翻译员建议"作为 A 调用 `create_from_selection` 的入参；本 spec 的弹窗 UI 不动
- **D（原始素材任务库强化）**：把 §5.3 的"跳素材管理上传"替换为任务中心内嵌上传 UI；状态机不动
- **E（翻译流深度集成 + 推送桥）**：把 §5.4 的"跳素材管理翻译"替换为子任务页内嵌 super-workbench；§5.5 的 readiness gate 不动
- **F（产能报表）**：直接查 `task_events` 表统计——本 spec 已埋好所有事件类型
