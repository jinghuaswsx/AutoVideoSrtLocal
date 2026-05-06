# Omni Translate 合并实验版 — 实施计划

- **日期**: 2026-05-07
- **关联设计文档**: [`docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`](../specs/2026-05-07-omni-translate-merge-design.md)（**必读**）
- **执行方**: Codex（按本计划逐 PR 提交）
- **Owner**: noobird（review + 推 master + 部署）

---

## 0. Codex 必读规则

1. **必先读 spec 文档**（`docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`）—— 本计划只列"步骤 + checklist"，"为什么这么做"在 spec 里
2. **不动 multi 模块**（`appcore/runtime_multi.py` / `web/routes/multi_translate.py` / `web/services/multi_pipeline_runner.py` / `web/templates/multi_translate*.html` / `web/static/multi_translate*.js,css`）—— 任何改动到这些文件前停下问 Owner
3. **不动 ja / de / fr 模块**
4. **不物理删除** sentence_translate / translate_lab 的代码 / DB 表 / migration 文件 —— 只 deprecate UI 入口
5. **每个 PR 独立可上线**：每个 PR 推到 master 后 prod 必须能 sudo restart 不挂；PR 之间不能有"上一个不部署下一个就崩"的依赖
6. **遵守 CLAUDE.md 全局规则**：worktree 隔离开发、文档锚点门禁、commit 必须有 docs anchor、`Co-Authored-By` 行
7. **测试**：每个 PR 必须有对应单测；E2E 测试在 Phase 6 一次性补
8. **本机即生产**：参考 `CLAUDE.md` 的「本机部署到线上的标准流程」；推 master 后需要 Owner 跑 sudo prod pull + restart

---

## Phase 0: 前置（Owner 手动，不要 Codex 做）

**Owner 做完才能让 Codex 开工。**

- [ ] Review 分支 `refactor/omni-tts-pluggable` 上的 9 个 commit（`git log master..HEAD`）
- [ ] 推 master：`git push origin refactor/omni-tts-pluggable:master`（在 worktree `0ubtzq57/pretty-seahorse` 内跑）
- [ ] sudo prod 同步 + restart（按 CLAUDE.md「标准发布流程」§3）
- [ ] 验证 prod `/multi-translate/` `/omni-translate/` `/sentence-translate/` `/translate-lab/` 4 个 blueprint 都能正常打开 + 创建测试任务跑通
- [ ] 给 Codex 开新 worktree（按本仓库约定）+ 拉新分支 `feat/omni-merge-experimental`

---

## Phase 1: Preset 数据模型 + 后端 CRUD API

**目标**: 落 DB 表 + 后端 API + 系统 settings 默认 preset，前端不动。

### Files to add

- `migrations/2026-05-07_omni_translate_presets.sql`
- `appcore/omni_preset_dao.py` — DAO 层（CRUD + 校验）
- `appcore/omni_plugin_config.py` — `validate_plugin_config(cfg: dict) -> dict` 纯函数（依赖/互斥校验 + silent fix）
- `web/routes/omni_preset_api.py` — Flask blueprint，路由全部走 `/api/omni-presets/...`
- `tests/test_omni_preset_dao.py`
- `tests/test_omni_plugin_config.py`
- `tests/test_omni_preset_api.py`

### Files to modify

- `web/app.py` — 注册新 blueprint
- `appcore/system_settings.py`（如已存在）or wherever system_settings DAO 住 —— 加 `omni_translate.default_preset_id` key 的读写

### DB schema

`migrations/2026-05-07_omni_translate_presets.sql`:

```sql
CREATE TABLE IF NOT EXISTS omni_translate_presets (
  id              BIGINT PRIMARY KEY AUTO_INCREMENT,
  scope           ENUM('system','user') NOT NULL,
  user_id         INT NULL,
  name            VARCHAR(64) NOT NULL,
  description     VARCHAR(255) NULL,
  plugin_config   JSON NOT NULL,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_scope_user (scope, user_id),
  CONSTRAINT chk_user_scope CHECK (
    (scope = 'system' AND user_id IS NULL) OR
    (scope = 'user' AND user_id IS NOT NULL)
  )
);

-- Seed 4 个等价系统级 preset（详细 plugin_config 见 spec §3 表）
INSERT INTO omni_translate_presets (scope, user_id, name, description, plugin_config) VALUES
  ('system', NULL, 'multi-like',       '复刻 multi_translate 行为',
    '{"asr_post":"asr_normalize","shot_decompose":false,"translate_algo":"standard","source_anchored":false,"tts_strategy":"five_round_rewrite","subtitle":"asr_realign","voice_separation":true,"loudness_match":true}'),
  ('system', NULL, 'omni-current',     'omni 当前默认（source-anchored prompt + asr_clean）',
    '{"asr_post":"asr_clean","shot_decompose":false,"translate_algo":"standard","source_anchored":true,"tts_strategy":"five_round_rewrite","subtitle":"asr_realign","voice_separation":true,"loudness_match":true}'),
  ('system', NULL, 'av-sync-current',  '复刻 sentence_translate 句级精确流程',
    '{"asr_post":"asr_normalize","shot_decompose":false,"translate_algo":"av_sentence","source_anchored":false,"tts_strategy":"sentence_reconcile","subtitle":"sentence_units","voice_separation":true,"loudness_match":true}'),
  ('system', NULL, 'lab-current',      '复刻 translate_lab 镜头分镜流程',
    '{"asr_post":"asr_normalize","shot_decompose":true,"translate_algo":"shot_char_limit","source_anchored":false,"tts_strategy":"five_round_rewrite","subtitle":"asr_realign","voice_separation":true,"loudness_match":true}');

-- 默认全站 preset 指向 omni-current（取上面 INSERT 的最后一个 id-2 假设连续，落地时按实际 id 写）
-- 实际 seed 走代码：第一次启动时如果 system_settings 里没 omni_translate.default_preset_id，自动设为 omni-current 的 id
```

### Validator 规则（`appcore/omni_plugin_config.py`）

```python
def validate_plugin_config(cfg: dict) -> dict:
    """校验 + silent fix；非法配置 raise ValueError 中文消息。返回 fix 后的 cfg。"""
    # 1. 4 个 radio 字段非空 + 取值合法
    # 2. shot_char_limit → shot_decompose 必须 True
    # 3. sentence_units → tts_strategy 必须 sentence_reconcile
    # 4. loudness_match → voice_separation 必须 True
    # 5. silent fix: av_sentence + source_anchored=True → 自动 source_anchored=False
    # 6. 字段缺失 → 用对应分组默认值补全
```

### API 端点（`web/routes/omni_preset_api.py`）

| Method | Path | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/omni-presets` | login | 列出当前用户可见 preset（系统级 + 自己的用户级），系统级在前 |
| POST | `/api/omni-presets` | login | 创建用户级 preset；body: `{name, description, plugin_config}` |
| PUT | `/api/omni-presets/<id>` | 校验权限：admin 可改系统级、user 只改自己的 | 改 name/description/plugin_config |
| DELETE | `/api/omni-presets/<id>` | 同上 | 系统级 preset 是当前全站默认时拒绝 |
| GET | `/api/omni-presets/default` | login | 返回当前全站默认 preset 完整数据 |
| POST | `/api/omni-presets/<id>/set-as-default` | admin only | 把 id 设为全站默认；id 必须是系统级 |

所有 POST/PUT 都跑 validator；失败 400。所有路由必加 `@login_required`；admin only 路由加 `@admin_required`（按 CLAUDE.md「路由守卫规范」）。

### Tests

- `tests/test_omni_plugin_config.py`：4 radio 缺失、依赖/互斥校验、silent fix、合法配置返回原样
- `tests/test_omni_preset_dao.py`：CRUD + scope 隔离 + 全站默认设置
- `tests/test_omni_preset_api.py`：6 个端点 + 权限校验 + CSRF（POST/PUT/DELETE 必须带 X-CSRFToken，参考 memory `project_csrf_guarded_blueprints`）

### Verification

```bash
pytest tests/test_omni_preset_dao.py tests/test_omni_plugin_config.py tests/test_omni_preset_api.py -q
```

### Commit message template

```
feat(omni-preset): DB schema + DAO + CRUD API for plugin preset system (Phase 1)

Adds omni_translate_presets table (scope/user/system) + validator
+ Flask blueprint /api/omni-presets/*. Seeds 4 baseline system presets
matching current omni / sentence_translate / translate_lab / multi-like
behaviors. Default global preset stored in system_settings as
omni_translate.default_preset_id.

Docs-anchor: docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md#4-preset-系统

Co-Authored-By: Codex <noreply@openai.com>
```

---

## Phase 2: Omni Runner 改造接 plugin_config

**目标**: omni runner 内部按 task.plugin_config 动态选算法分支，所有需要的算法体物理住进 omni。task 表加字段。

### Files to add

- 无（算法体住进现有 `appcore/runtime_omni.py`，可能拆分成几个 module 如 `appcore/runtime_omni_steps.py` 由 Codex 自决）

### Files to modify

- `appcore/runtime_omni.py` — 加 `_get_pipeline_steps` 动态版本（按 §6.1 伪代码）+ 复制/搬运算法体（按 spec §6.2 表）
- `appcore/task_state.py` — 创建 omni 任务时支持 `plugin_config` 字段
- `migrations/2026-05-07_projects_plugin_config.sql` — projects 表加 `plugin_config JSON NULL` 字段

### 算法体来源对照（按 spec §6.2 表执行）

每个搬运/复制必须**物理拷贝代码到 omni 内部**，**不要**用 `from appcore.runtime_multi import ...`。这样 multi 任何改动都不会污染 omni。

- `_step_asr_normalize`: 从 `appcore/translate_profiles/default_profile.py` 的 `DefaultProfile.post_asr` 复制
- `_step_translate_standard`: 从 `appcore/translate_profiles/default_profile.py` 的 `DefaultProfile.translate` 复制；保留对 `source_anchored` 的判断（基于 cfg 决定 prompt 加不加 INPUT NOTICE）
- `_step_translate_shot_limit`: 从 `appcore/runtime_v2.py` 的 `PipelineRunnerV2._step_translate` 搬
- `_step_translate_av_sentence`: 从 `appcore/translate_profiles/av_sync_profile.py` 的 `AvSyncProfile.translate` 搬（含内部 shot_notes 调用）
- `_step_shot_decompose`: 从 `appcore/runtime_v2.py` 的 `PipelineRunnerV2._step_shot_decompose` 搬
- `_step_tts_sentence_reconcile`: 从 `appcore/tts_strategies/sentence_reconcile.py` 的 `SentenceReconcileStrategy.run` body 搬（这个本来就在 strategy 里，omni 可以**直接复用**——既然已经 PR6 抽象好了，omni runner 不需要再复制一份，profile.tts 调 strategy 即可）
- `_step_subtitle_asr_realign`: 从 `appcore/translate_profiles/default_profile.py` 的 `DefaultProfile.subtitle` 复制
- `_step_subtitle_sentence_units`: 从 `appcore/translate_profiles/av_sync_profile.py` 的 `AvSyncProfile.subtitle` 搬
- `_step_voice_match`: 复用 base PipelineRunner 上的（PR7 已经在 base 上了；omni 继承即可，不改）
- `_step_separate` / `_step_loudness_match` / `_step_compose` / `_step_export` / `_step_extract` / `_step_asr` / `_step_alignment`: 全部复用 base，omni 不动

### OmniProfile 改造

`appcore/translate_profiles/omni_profile.py` 的 4 个 hook 改成读 `task["plugin_config"]` dispatch：

```python
def post_asr(self, runner, task_id):
    cfg = self._cfg(runner, task_id)
    if cfg["asr_post"] == "asr_clean":
        runner._step_asr_clean(task_id)  # 已有
    else:
        runner._step_asr_normalize(task_id)  # 新加（从 multi 复制过来）

def translate(self, runner, task_id):
    cfg = self._cfg(runner, task_id)
    algo = cfg["translate_algo"]
    if algo == "standard":
        runner._step_translate_standard(task_id, source_anchored=cfg["source_anchored"])
    elif algo == "shot_char_limit":
        runner._step_translate_shot_limit(task_id)
    elif algo == "av_sentence":
        runner._step_translate_av_sentence(task_id)

def tts(self, runner, task_id, task_dir):
    cfg = self._cfg(runner, task_id)
    if cfg["tts_strategy"] == "five_round_rewrite":
        runner._step_tts(task_id, task_dir)  # base 5 轮 loop
    else:
        runner._step_tts_sentence_reconcile(task_id, task_dir)

def subtitle(self, runner, task_id, task_dir):
    cfg = self._cfg(runner, task_id)
    if cfg["subtitle"] == "asr_realign":
        runner._step_subtitle_asr_realign(task_id, task_dir)
    else:
        runner._step_subtitle_sentence_units(task_id, task_dir)

def _cfg(self, runner, task_id):
    """读 task.plugin_config，缺失时回退到当前全站默认 preset 的配置。"""
```

### Task 表 schema 改动

```sql
ALTER TABLE projects ADD COLUMN plugin_config JSON NULL COMMENT 'Omni 任务的能力点配置快照';
```

不加 NOT NULL，因为老 omni / multi 任务都没这个字段。omni runner 读不到时按当前全站默认 preset 兜底。

### Tests

- `tests/test_runtime_omni_dispatch.py`：mock plugin_config 各种组合，验证 `_get_pipeline_steps` 输出 step 列表正确
- `tests/test_runtime_omni_translate_standard.py`：source_anchored 开 / 关，prompt 内容差异
- `tests/test_runtime_omni_shot_decompose.py`：shot_decompose 开关下 step 顺序
- `tests/test_runtime_omni_tts_dispatch.py`：tts_strategy 切换 dispatch 正确

注意 mock `task_state.get` 返回带 `plugin_config` 的 task。

### Commit message template

```
feat(omni-runner): plugin_config-driven step dispatch + algo bodies copied in (Phase 2)

OmniTranslateRunner._get_pipeline_steps now reads task.plugin_config to
dynamically build the step list. Algorithm bodies for asr_normalize /
shot_decompose / shot_char_limit translate / av_sentence translate /
sentence_units subtitle are physically copied into runtime_omni.py
(no import from runtime_multi / runtime_v2 / runtime_sentence_translate
to keep multi stable and prepare deprecating those modules).

projects table gets a new nullable plugin_config JSON column. Omni
tasks read it; if absent, fall back to the current global default
preset.

Docs-anchor: docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md#6-omni-runner-改造

Co-Authored-By: Codex <noreply@openai.com>
```

---

## Phase 3: 创建 omni 任务时持久化 plugin_config

**目标**: omni 创建任务的 web 路由接收 plugin_config + 写入 DB；resume 时读 DB 字段。

### Files to modify

- `web/routes/omni_translate.py` — 新建任务 POST 接收 plugin_config（或 preset_id，后端展开）+ 校验 + 写入 task_state
- `web/services/omni_pipeline_runner.py` — runner.start() 时不变（task 已经带 plugin_config 了）
- `appcore/task_state.py` — 任务持久化时把 plugin_config 字段写 DB

### 接口约定

POST `/api/omni-translate/tasks` body:

```json
{
  "video_file": "...",
  "source_language": "auto",
  "target_language": "en",
  "plugin_config": {  
    "asr_post": "asr_clean",
    "shot_decompose": false,
    "translate_algo": "standard",
    "source_anchored": true,
    "tts_strategy": "five_round_rewrite",
    "subtitle": "asr_realign",
    "voice_separation": true,
    "loudness_match": true
  }
}
```

后端流程：
1. 跑 `validate_plugin_config(plugin_config)` → 拿到 fixed cfg（或抛 400）
2. 创建 task，把 fixed cfg 写入 `task.plugin_config`
3. 启动 runner

### Tests

- `tests/test_omni_translate_create_with_plugin_config.py`：
  - 合法 cfg 创建成功
  - 缺字段 / 互斥冲突 → 400
  - silent fix 生效（如 av_sentence + source_anchored=true 写入后变 false）
  - 老接口（不带 plugin_config）回退到全站默认 preset

### Commit message template

```
feat(omni-route): accept plugin_config on task creation + persist (Phase 3)

POST /api/omni-translate/tasks now accepts plugin_config in the body,
runs validate_plugin_config (silent fix + dependency check), writes
the fixed config to projects.plugin_config column and starts the
runner. Resume reads from the same column. Old API calls without
plugin_config fall back to the current global default preset.

Docs-anchor: docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md#65-task-表存储

Co-Authored-By: Codex <noreply@openai.com>
```

---

## Phase 4: 新建任务弹窗 UI 改造

**目标**: `/omni-translate/` 列表页的「+ 新建任务」弹窗按 spec §5.1 改造。

### Files to modify

- `web/templates/omni_translate_list.html` — 新建任务弹窗 HTML 结构（preset 选择器 + 8 分组能力点表单）
- `web/static/omni_translate_list.js`（or 新建 omni_create_modal.js）— preset 加载、切换、互斥/依赖前端联动、"另存为新 preset"、用户级 preset CRUD（hover ✏️/🗑）
- `web/static/omni_translate.css`（or新建）— 弹窗样式

### 行为细节（按 spec §5.1）

实现要点：
- 弹窗打开时调 `GET /api/omni-presets` + `GET /api/omni-presets/default` 拿 preset 列表 + 默认 id
- preset 选择器 dropdown：系统级在前 + 分隔线 + 用户级；用户级右侧 hover 显示 ✏️/🗑（系统级 admin 看 admin 面板编辑，普通 user 不显示这俩按钮）
- 切换 preset：触发 `setFormState(preset.plugin_config)` 重置所有 radio/checkbox
- 任意 radio/checkbox 改动 → 在 preset 名旁加灰色「(已修改)」+「+ 另存为新 preset」按钮高亮
- 互斥/依赖前端联动（参考 spec §3 「互斥与依赖」）：
  - 选 `shot_char_limit` → 自动 check `shot_decompose` + disable
  - 选 `av_sentence` → `source_anchored` 自动 uncheck + disable + tooltip
  - 选 `sentence_units` → `sentence_reconcile` 自动 check + disable
  - uncheck `voice_separation` → `loudness_match` 自动 uncheck + disable
- 「+ 另存为新 preset」点击 → 弹小输入框 → POST `/api/omni-presets`
- 「创建任务」：把当前 form state 收集成 plugin_config JSON，POST `/api/omni-translate/tasks`（带 X-CSRFToken）

### 视觉规范

按 `CLAUDE.md` 的 Frontend Design System（Ocean Blue Admin）：
- 弹窗用 `--bg`、`border` 用 `--border`、`--radius-lg`
- radio / checkbox 用 `--accent` focus ring
- 分组标题：`--text-md` + `--fg-muted`
- 互斥灰掉的选项：opacity 0.5 + cursor disabled
- 「(已修改)」灰字：`--fg-subtle`
- 按钮：「+ 另存为新 preset」用 `--accent-subtle` 底；「创建任务」用主 accent
- **零紫色硬约束**——所有 hue 在 200-240，参考其他弹窗的样式

### Tests

- `tests/test_web_routes_omni_create_modal.py` — Jinja 渲染断言：弹窗 HTML 含所有 8 分组、4 radio + 4 checkbox 的 input、preset dropdown、CSRF token
- 手动 / Playwright（如有 webapp-testing skill）：
  - 弹窗加载 preset 默认勾上正确选项
  - 切 preset 表单刷新
  - 改任意选项「已修改」出现
  - 互斥联动正确
  - 「另存为」流程

### Commit message template

```
feat(omni-ui): new-task modal with preset selector + 8-group capability form (Phase 4)

Rebuilds /omni-translate/ "新建任务" modal per design doc §5.1:
- top preset dropdown (system + user level, with hover edit/delete on user-level)
- 8-group capability form (4 radio + 4 checkbox) with mutex/dependency live-binding
- "另存为新 preset" inline create
- form state submits as plugin_config to POST /api/omni-translate/tasks

Frontend follows the Ocean Blue Admin design system (no purple, hue 200-240).

Docs-anchor: docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md#51-新建任务弹窗

Co-Authored-By: Codex <noreply@openai.com>
```

---

## Phase 5: `/settings` Admin Tab — Omni Preset 管理

**目标**: admin 在 `/settings` 加 tab 管理系统级 preset + 设全站默认。

### Files to modify

- `web/templates/settings.html`（or 对应 tab 模板）— 加 tab "Omni Preset"，仅 admin 可见
- `web/static/settings_omni_preset.js`（新建或并入 settings.js）— tab 内 CRUD 交互
- `web/routes/settings.py`（或对应路由）— admin 校验

### UI 细节（spec §5.2）

- Tab 顶部：「全站默认 preset」dropdown（只列系统级 preset）；改后立即调 POST `/api/omni-presets/<id>/set-as-default`
- 中间：系统级 preset 列表（表格：名称 / 说明 / 创建时间 / [编辑] / [删除]）；当前默认行高亮 + ⭐
- 底部：「+ 新建系统级 preset」按钮 → 弹窗（同新建任务弹窗的能力点表单 + name/description）
- 「我的用户级 preset」section：admin 自己创建的 user-level preset 也列出来（参考新建对话框的 hover CRUD）

权限：
- 只有 admin 看到这个 tab（layout.html 或 settings.html 在 server-side 渲染时判断 `current_user.is_admin`）
- 所有 admin only API（PUT 系统级、DELETE 系统级、set-default）必须后端再校验一次（不能只靠前端隐藏）

### Tests

- `tests/test_settings_omni_preset_tab.py`：admin 能访问、普通 user 404 / 403
- 手动：admin 改全站默认 → 重新打开新建任务弹窗 → 默认 preset 切换

### Commit message template

```
feat(settings): omni preset admin tab + global default selector (Phase 5)

Adds an "Omni Preset" tab to /settings (admin only). Admin can:
- pick the global default preset (POST /api/omni-presets/<id>/set-as-default)
- CRUD system-level presets
- see/manage their own user-level presets

Non-admin users get 403 if they try to hit admin-only endpoints
directly (server-side guard, not just UI hide).

Docs-anchor: docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md#52-admin-设置

Co-Authored-By: Codex <noreply@openai.com>
```

---

## Phase 6: Deprecate sentence_translate / translate_lab UI 入口

**目标**: 隐藏 sidebar 入口 + 列表页加 deprecated banner + 新建按钮 disable；**不动 runtime / DB / template 删除**。

### Files to modify

- `web/templates/layout.html` 或 sidebar partial — 隐藏 `/sentence-translate/` 和 `/translate-lab/` 两条入口（hardcode 删除菜单项即可，不要加 feature flag——按 CLAUDE.md「Don't use feature flags... when you can just change the code」）
- `web/templates/sentence_translate_list.html`（or wherever）— 列表页顶部加 banner：「本模块已 deprecated，请使用 `/omni-translate/` 并选择 av-sync-current preset」；「+ 新建任务」按钮加 `disabled` + tooltip
- `web/templates/translate_lab_list.html` — 同上，banner 文案改成「本模块已 deprecated，请使用 `/omni-translate/` 并选择 lab-current preset」
- `web/routes/sentence_translate.py` / `web/routes/translate_lab.py` — 创建任务 POST 端点返回 410 Gone + 提示用 omni（防御层）
- 详情页路由 / 模板：**保留**，老任务（即使 DB 0 任务，万一未来用 archive 工具加进来）能查看

### Tests

- `tests/test_sentence_translate_deprecated_ui.py`：列表页响应包含 banner + 创建按钮 disabled；创建 API POST 返回 410
- `tests/test_translate_lab_deprecated_ui.py`：同
- `tests/test_sidebar_no_deprecated_links.py`：layout.html 渲染不含 `/sentence-translate/` 或 `/translate-lab/` href

### Commit message template

```
chore(deprecate): hide sentence_translate / translate_lab UI entries (Phase 6)

Per design doc §5.3 + §8: these two modules are already 0-task in DB.
Hide sidebar entries, add deprecated banner on list pages, disable
"new task" buttons, return 410 Gone on POST create. Detail pages stay
accessible. Runtime / DB tables / migrations untouched (defensive
preservation, may revive in future).

Docs-anchor: docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md#2-范围

Co-Authored-By: Codex <noreply@openai.com>
```

---

## Phase 7: 4 套等价 preset 端到端验收

**目标**: 用同一段测试视频跑完 4 个等价系统级 preset，从 extract 到 export 全部成功；产物结构对齐原模块。

### Setup

- 测试视频：用 `testuser.md` 里指定的标准测试视频
- 测试账号：admin（见 testuser.md）
- 跑环境：本机 dev server 端口（如 5090）+ 真 LLM/ASR/TTS API（不 mock）

### 验收 checklist

对每个 preset（multi-like / omni-current / av-sync-current / lab-current）跑一个任务：

- [ ] 任务创建成功，task.plugin_config 写入 DB 正确
- [ ] step 顺序符合预期（例如 av-sync-current 没有 alignment step；lab-current 有 shot_decompose step 在 asr 后）
- [ ] 各 step 跑通到 done 状态
- [ ] 最终下载到合成视频文件（mp4）
- [ ] 字幕 SRT 文件存在且非空
- [ ] 对照原模块（用 `/sentence-translate/` 跑同一视频）结果功能等价：相同语种、相同段数（±10%）、TTS 总时长跟视频时长 ±2s

### 自动化 E2E（可选但推荐）

- `tests/test_omni_preset_e2e_smoke.py`：
  - mock LLM/ASR/TTS（用 stub 返回固定结果）
  - 4 个 preset 各跑一次完整 pipeline
  - 断言 step 顺序、artifact 文件名、关键 task_state 字段

### 手动验收完成后

- 文档落地：在 `docs/superpowers/handoff/` 或 `docs/` 加一份 handoff 文档，记录验收结果（哪个 preset 跑了哪个视频、最终视频在哪、有什么 quirks）

### Commit message template

```
test(omni-merge): e2e smoke for 4 baseline presets + handoff notes (Phase 7)

Adds tests/test_omni_preset_e2e_smoke.py covering all 4 system-level
presets (multi-like / omni-current / av-sync-current / lab-current).
Mocks LLM/ASR/TTS to keep the test deterministic. Real-API verification
results documented in docs/.../omni-merge-handoff.md.

Docs-anchor: docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md#7-验收标准

Co-Authored-By: Codex <noreply@openai.com>
```

---

## Phase 8（可选）: CHANGELOG + 用户文档

**目标**: 给最终用户/运维同学的可读说明。

### Files to add / modify

- `CHANGELOG.md` — 加一条 entry（如本仓库有 CHANGELOG）
- `docs/omni-translate-experimental.md` — 用户面向的使用文档：怎么选 preset、4 个 baseline preset 各自是什么意思、怎么自定义保存

### Commit message

```
docs: omni-translate experimental usage guide + changelog (Phase 8)

Documents the 4 baseline presets, the new modal flow, and
the deprecation of /sentence-translate/ and /translate-lab/ for
end-user reference.

Docs-anchor: docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md
```

---

## 部署节奏（每个 Phase 推 master 后 Owner 同步 prod）

| Phase | 推 master 后部署影响 | 是否要 sudo prod restart |
|---|---|---|
| 1 | DB schema 变化（新增 omni_translate_presets 表 + seed）；API 新增不影响老路径 | ✅ 必须 restart 让 systemd 跑 schema migration |
| 2 | DB schema 变化（projects 加 plugin_config 字段）；老 omni 任务 resume 行为：plugin_config 为 NULL → 回退全站默认；行为变化 | ✅ 必须 restart |
| 3 | 仅 omni 创建任务接口扩展，老接口兼容 | ✅ restart |
| 4 | 仅 omni UI；不影响其他模块 | ✅ restart |
| 5 | settings 加 tab；admin only；不影响其他人 | ✅ restart |
| 6 | sentence_translate / translate_lab 入口隐藏；老任务详情页保留 | ✅ restart |
| 7 | 验收，仅测试代码 | ❌ 跑测试即可，不影响 prod 行为 |
| 8 | 文档 only | ❌ |

---

## 进度追踪

Codex 完成每个 Phase 后向 Owner 报告：commit hash + 跑过的测试列表 + 部署 checklist。Owner 每个 Phase 推 master + sudo prod 部署 + 验证后批准下一个 Phase。

任何阶段发现 spec 有缺失或冲突，停下问 Owner 而不是自行决策。

---

## Related

- 设计文档（必读）: [`docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`](../specs/2026-05-07-omni-translate-merge-design.md)
- 前置 PR1–PR7（在 `refactor/omni-tts-pluggable` 分支，Phase 0 先合并到 master）
- CLAUDE.md「文档驱动代码」「分支与开发隔离规则」「本机部署到线上的标准流程」「路由守卫规范」
- 已 deprecated 但保留代码: `appcore/runtime_sentence_translate.py`、`appcore/runtime_v2.py`、对应 blueprint 和 template
