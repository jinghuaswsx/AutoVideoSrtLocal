# Omni Translate 合并实验版 — 设计文档

- **日期**: 2026-05-07
- **状态**: Draft（待 Codex 实施）
- **关联实施计划**: [`docs/superpowers/plans/2026-05-07-omni-translate-merge.md`](../plans/2026-05-07-omni-translate-merge.md)
- **前置依赖**: `refactor/omni-tts-pluggable` 分支的 9 个 commit（PR1–PR7）已合并到 master

---

## 1. 背景

当前仓库有 4 个视频翻译 runner，但生产实际只有一个在用：

| Runner | DB 任务量（生命周期） | 状态 |
|---|---|---|
| `multi_translate` | **290（7 天 134）** | ✅ 唯一生产真用户 |
| `omni_translate` | 0 | 🔧 用户在开发的"实验版基线" |
| `sentence_translate` (av_sync) | 0 | 🔧 用户在开发的"句级精确"实验路径 |
| `translate_lab` (V2) | 0 | 🔧 用户在开发的"镜头分镜"实验路径 |
| `de_translate` / `fr_translate` / `ja_translate` | 0 | ❌ 已废弃，不在本任务范围 |

3 个实验路径（omni / sentence_translate / translate_lab）各占一个 blueprint + runtime + 模板 + socket events，维护成本高、能力散落。用户希望把它们合并为一个**实验大本营** `/omni-translate/`，里面通过**插件化能力点 + preset 系统**让用户自由组合不同算法做对比测试。

`multi_translate` **保持不动**——它是线上稳定版，不参与本次合并。

---

## 2. 范围

### In scope

- 把 `sentence_translate` 和 `translate_lab` 的特殊能力作为**可配置插件**接入 `/omni-translate/`
- 把 `multi_translate` 的核心算法**复制一份**到 omni 内部（作为 `standard` 翻译选项 + `asr_normalize` ASR 后处理选项 + `asr_realign` 字幕选项 + `five_round_rewrite` TTS 收敛选项）—— **不动 multi 的代码**
- 新建 **Preset 系统**：两层（系统级 admin 维护 + 用户级私有），admin 在 `/settings` 设全站默认 1 个 preset
- 改造 `/omni-translate/` 新建任务弹窗：preset 顶部选 + 能力点同屏可改 + 用户级 preset 就地 CRUD
- task 表加 `plugin_config` JSON 字段（任务创建时把能力点配置展开存）
- 验收：4 个等价系统级 preset 各跑通同一段测试视频

### Not in scope

- ❌ `multi_translate` 模块的**任何改动**（runtime / blueprint / template / web service / DB type / sidebar 入口都不动）
- ❌ `de_translate` / `fr_translate` / `ja_translate` 任何改动（已废弃，留作历史）
- ❌ **物理删除** `sentence_translate` / `translate_lab` 的代码、DB 表、schema_migrations
- ❌ DB schema 进一步归一（如 `task.profile_code` 字段、6 个 blueprint 合并到 `/translate/`）—— 这是后续 PR
- ❌ 进一步抽象 PR1–PR7 之外的 hook（如 separate / loudness_match / compose 加 hook）

### Deprecate（保留代码但用户层面隐藏）

- `/sentence-translate/` 和 `/translate-lab/` 两个 blueprint 的 sidebar 入口隐藏
- 这两个 blueprint 的"新建任务"按钮显示 deprecated 警告并拒绝创建新任务
- 老任务详情页继续可访问（防御性保留，DB 0 任务但 schema 还在）
- runtime / template / static / socket events 代码不动

---

## 3. 能力清单

合并后的 omni 暴露 **8 分组、4 radio + 4 checkbox** 共 12 个独立能力点。每个能力点对应 pipeline 的某个 step 算法或可选增强。

| 分组 | 选项 | 中文说明 | 选择方式 | 依赖 / 互斥 | 来自原模块 |
|---|---|---|---|---|---|
| **① ASR 后处理** | `asr_clean` | 按源语言原样清洗文本（去口误、补标点），不翻译 | 二选一 radio | — | omni |
| | `asr_normalize` | ASR 文本统一翻成英文，给下游翻译走同一英文基线 | | — | multi（**复制**） |
| **② 镜头分镜** | `shot_decompose` | 用 Gemini 视觉分析视频，切出"一个镜头一段话"的镜头列表 + 时间轴 | checkbox | — | translate_lab |
| **③ 翻译算法** | `standard` | 整段一次性翻译，靠 prompt 控制风格和长度 | 三选一 radio | — | multi（**复制**） + omni |
| | `shot_char_limit` | 每镜头独立翻译，按"镜头时长 × cps"算字符上限，让初译就贴合时长（cps 基准 voice_match 时自动初始化） | | 需 ② | translate_lab |
| | `av_sentence` | 句级翻译，先用 Gemini 给每句打"画面笔记"再逐句翻，贴合画面（shot_notes 内置，不暴露独立勾选） | | — | sentence_translate |
| **④ 翻译 prompt 增强** | `source_anchored` | system prompt 加 INPUT NOTICE，告诉 LLM 输入是 ASR 文本不要捏造原视频之外的内容 | checkbox | 仅对 `standard` / `shot_char_limit` 生效；选 `av_sentence` 时 UI 灰掉 | omni |
| **⑤ TTS 收敛策略** | `five_round_rewrite` | 5 轮 rewrite + 变速短路：每轮按音频实际时长反向重译，直到落进时长窗口 | 二选一 radio | — | multi（**复制**） + omni |
| | `sentence_reconcile` | 句级 reconcile：每句独立 TTS 测时长，逐句调速率或重译，不做整段 rewrite | | — | sentence_translate |
| **⑥ 字幕生成** | `asr_realign` | TTS 后再跑一次 ASR 拿词级时间戳，按词重新对齐字幕，最准 | 二选一 radio | — | multi（**复制**） + omni |
| | `sentence_units` | 直接用句级 TTS 的时间轴出 SRT，跳过二次 ASR | | 需 ⑤ 选 `sentence_reconcile` | sentence_translate |
| **⑦ 人声分离** | `voice_separation` | 用 audio-separator 分离人声和背景音，配音后跟原 BGM 重新混音 | checkbox（默认开） | — | multi/omni 共有 |
| **⑧ 响度匹配** | `loudness_match` | 配音整体响度按 EBU R128 匹配原视频，避免音量突兀 | checkbox（默认开） | 需 ⑦ | multi/omni 共有 |

### 互斥与依赖（后端校验 + 前端禁用）

- ① 必须二选一（缺省: `asr_clean`）
- ③ 必须三选一（缺省: `standard`）
- ⑤ 必须二选一（缺省: `five_round_rewrite`）
- ⑥ 必须二选一（缺省: `asr_realign`）
- `shot_char_limit` 选中时 `shot_decompose` 必须开（前端自动开 + 禁勾掉）
- `sentence_units` 选中时 `sentence_reconcile` 必须开（前端自动开 + 禁勾掉）
- `loudness_match` 选中时 `voice_separation` 必须开（前端自动开 + 禁勾掉）
- `source_anchored` 选 `av_sentence` 时 UI 灰掉、提交时若误传后端忽略

### 4 个等价系统级 Preset（验收基准 + 初始 seed）

| Preset 名 | ① | ② | ③ | ④ | ⑤ | ⑥ | ⑦ | ⑧ |
|---|---|---|---|---|---|---|---|---|
| **multi-like** | `asr_normalize` | — | `standard` | — | `five_round_rewrite` | `asr_realign` | ✓ | ✓ |
| **omni-current** | `asr_clean` | — | `standard` | `source_anchored` | `five_round_rewrite` | `asr_realign` | ✓ | ✓ |
| **av-sync-current** | `asr_normalize` | — | `av_sentence` | — | `sentence_reconcile` | `sentence_units` | ✓ | ✓ |
| **lab-current** | `asr_normalize` | `shot_decompose` | `shot_char_limit` | — | `five_round_rewrite` | `asr_realign` | ✓ | ✓ |

`omni-current` 是建议的**全站默认 preset**（admin 可在 `/settings` 改）。

---

## 4. Preset 系统

### 4.1 两层模型（C 模型）

- **系统级 preset (`scope='system'`)**: admin 在 `/settings` 维护，所有 user 只读可见
- **用户级 preset (`scope='user'`)**: 每个 user 在新建对话框里自己创建/编辑/删除，仅自己可见
- **权限矩阵**:

| 操作 | 系统级 preset | 用户级 preset（自己） | 用户级 preset（别人的） |
|---|---|---|---|
| 看 | ✅ 全员 | ✅ | ❌ |
| 用（创建任务时选中） | ✅ 全员 | ✅ | ❌ |
| 改 | admin only | ✅ | ❌ |
| 删 | admin only | ✅ | ❌ |
| 设全站默认 | admin only | ❌（用户级不能当全站默认） | ❌ |

### 4.2 全站默认 preset（B 模型）

- admin 在 `/settings` 选 1 个**系统级** preset 作为全站默认
- 任何 user 新建任务，弹窗初始 preset = 当前全站默认
- user **不**持久保存"我的默认"——每次开新建对话框都是全站默认

### 4.3 数据模型

新表 `omni_translate_presets`:

```sql
CREATE TABLE omni_translate_presets (
  id              BIGINT PRIMARY KEY AUTO_INCREMENT,
  scope           ENUM('system','user') NOT NULL,
  user_id         INT NULL,                          -- system: NULL；user: 创建者 id
  name            VARCHAR(64) NOT NULL,              -- 用户填的名字
  description     VARCHAR(255) NULL,                 -- 用户填的说明（可空）
  plugin_config   JSON NOT NULL,                     -- 能力点配置快照（见下文 schema）
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_scope_user (scope, user_id),
  CONSTRAINT chk_user_scope CHECK (
    (scope = 'system' AND user_id IS NULL) OR
    (scope = 'user' AND user_id IS NOT NULL)
  )
);
```

全站默认 preset id 存在 `system_settings` 表里 key=`omni_translate.default_preset_id`（int）。

### 4.4 plugin_config JSON Schema

```json
{
  "asr_post":         "asr_clean | asr_normalize",
  "shot_decompose":   true | false,
  "translate_algo":   "standard | shot_char_limit | av_sentence",
  "source_anchored":  true | false,
  "tts_strategy":     "five_round_rewrite | sentence_reconcile",
  "subtitle":         "asr_realign | sentence_units",
  "voice_separation": true | false,
  "loudness_match":   true | false
}
```

后端 validator 必须执行：
- 4 个 radio 字段非空 + 取值合法
- 依赖关系：`shot_char_limit→shot_decompose`、`sentence_units→sentence_reconcile`、`loudness_match→voice_separation` 必须成立
- `source_anchored=true` 但 `translate_algo=av_sentence` 时**自动 silently 改成 false**（不报错，便于 preset 切换）

### 4.5 Task 表存储

- omni 任务的 task 行新增字段 `plugin_config JSON NOT NULL`
- 创建 task 时把当前生效配置（preset 加载 + 用户微调结果）展开存入
- resume / 重跑 / 详情页全部读这个字段，**不**回查 preset
- preset 改了**不**影响已有任务

---

## 5. UI 设计

### 5.1 新建任务弹窗（`/omni-translate/` → 「+ 新建任务」）

```
┌────────────────────────────────────────────────────────┐
│ 新建实验任务                                  [X]      │
├────────────────────────────────────────────────────────┤
│ 视频文件: [选择文件]                                   │
│ 源语言: [自动检测 ▾]   目标语言: [英语 ▾]              │
│                                                        │
│ ── 处理流程预设 ───────────────────────────────────── │
│ 流程: [omni-current 🌐 ▾]                              │
│   系统级 preset 由管理员在 /settings 统一维护          │
│                                                        │
├────────────────────────────────────────────────────────┤
│                          [取消]  [创建任务]            │
└────────────────────────────────────────────────────────┘
```

行为规则：
1. **打开弹窗**：preset 默认选中**全站默认**（admin 在 `/settings` 设的）。
2. **创建项目时只能选择系统级 preset**：新建任务弹窗不展示 8 组能力点表单，不允许在创建项目时临时点选各步骤，也不提供「另存为」「新建流程」「删除」入口。
3. **普通用户不看到流程细节**：下拉只显示系统级 preset 名称；具体步骤组合由管理员在 `/settings?tab=omni_preset` 统一维护。
4. **「创建任务」**：提交选中系统级 preset 的 `plugin_config` 快照；已有任务仍不回查 preset，preset 后续变更不影响已创建任务。

### 5.2 admin 设置（`/settings` → 加 tab `Omni Preset`）

仅 admin 可见的 tab，内容：

```
┌─ Omni Preset 管理 ──────────────────────────────────┐
│                                                     │
│ 全站默认 preset:                                    │
│ [omni-current ▾]   (改后立即生效，影响所有人)       │
│                                                     │
│ ── 系统级 preset 列表 ──────────────────────────── │
│  名称          说明              操作               │
│  multi-like    复刻 multi 行为   [编辑] [删除]      │
│  omni-current  omni 当前默认     [编辑] [删除] ⭐   │
│  av-sync-current 句级实验        [编辑] [删除]      │
│  lab-current   镜头分镜实验      [编辑] [删除]      │
│  [+ 新建系统级 preset]                              │
│                                                     │
└─────────────────────────────────────────────────────┘
```

⭐ 标记当前的全站默认。删除全站默认 preset 前必须先选另一个为默认。

### 5.3 Sidebar 改动

- ✅ 保留：`/omni-translate/` 入口（这是合并后的实验大本营）
- ❌ 隐藏：`/sentence-translate/` 入口
- ❌ 隐藏：`/translate-lab/` 入口（"视频翻译（测试）"）

老任务直链 `/sentence-translate/<id>` / `/translate-lab/<id>` 详情页保留可访问（防御）。

---

## 6. Omni Runner 改造

### 6.1 整体路径

omni runner 的 `_get_pipeline_steps` 不再走 `_build_steps_from_profile`（那是 PR2 加的固定 step builder），而是改成基于 `task["plugin_config"]` **动态生成 step list**。

伪代码：

```python
def _get_pipeline_steps(self, task_id, video_path, task_dir):
    cfg = task_state.get(task_id)["plugin_config"]
    steps = [
        ("extract", lambda: self._step_extract(...)),
        ("asr",     lambda: self._step_asr(...)),
    ]
    if cfg["voice_separation"]:
        steps.append(("separate", lambda: self._step_separate(...)))
    if cfg["shot_decompose"]:
        steps.append(("shot_decompose", lambda: self._step_shot_decompose(...)))
    # ① post_asr：按 cfg["asr_post"] 选 _step_asr_clean 或 _step_asr_normalize
    steps.append((cfg["asr_post"], lambda: self._dispatch_post_asr(cfg, ...)))
    steps.append(("voice_match", lambda: self._step_voice_match(...)))
    if cfg["translate_algo"] != "av_sentence":
        steps.append(("alignment", lambda: self._step_alignment(...)))
    # ③ translate：按 cfg["translate_algo"] 选 standard / shot_char_limit / av_sentence
    steps.append(("translate", lambda: self._dispatch_translate(cfg, ...)))
    # ⑤ tts：按 cfg["tts_strategy"] 选 five_round_rewrite / sentence_reconcile
    steps.append(("tts", lambda: self._dispatch_tts(cfg, ...)))
    if cfg["loudness_match"]:
        steps.append(("loudness_match", lambda: self._step_loudness_match(...)))
    # ⑥ subtitle：按 cfg["subtitle"] 选 asr_realign / sentence_units
    steps.append(("subtitle", lambda: self._dispatch_subtitle(cfg, ...)))
    steps.append(("compose", lambda: self._step_compose(...)))
    steps.append(("export", lambda: self._step_export(...)))
    return steps
```

### 6.2 算法实现归属

每个能力点的算法体住在 omni runner 内部（不 import `multi_translate` 模块）：

| 能力点 | 实现位置 | 来源 |
|---|---|---|
| `asr_clean` | `OmniTranslateRunner._step_asr_clean`（PR4c 已有） | omni（已有） |
| `asr_normalize` | `OmniTranslateRunner._step_asr_normalize`（**新加，从 multi 复制**） | multi 复制 |
| `shot_decompose` | `OmniTranslateRunner._step_shot_decompose`（**新加，从 V2 搬**） | V2 搬运 |
| `standard` translate | `OmniTranslateRunner._step_translate_standard`（**新加，从 multi 复制 + omni 原 INPUT NOTICE 逻辑剥成 mixin**） | multi 复制 + omni |
| `shot_char_limit` translate | `OmniTranslateRunner._step_translate_shot_limit`（**新加，从 V2 搬**） | V2 搬运 |
| `av_sentence` translate | `OmniTranslateRunner._step_translate_av_sentence`（**新加，从 av_sync 搬**） | av_sync 搬运 |
| `source_anchored` prompt | translate 内 if-branch | omni（已有） |
| `five_round_rewrite` tts | base PipelineRunner._run_default_tts_loop（PR6 已有） | base（已有） |
| `sentence_reconcile` tts | `OmniTranslateRunner._step_tts_sentence_reconcile`（**新加，从 av_sync 搬**） | av_sync 搬运 |
| `asr_realign` subtitle | `OmniTranslateRunner._step_subtitle_asr_realign`（**新加，从 multi 复制**） | multi 复制 |
| `sentence_units` subtitle | `OmniTranslateRunner._step_subtitle_sentence_units`（**新加，从 av_sync 搬**） | av_sync 搬运 |
| `voice_separation` | base `_step_separate`（PR2 已有） | base（已有） |
| `loudness_match` | base `_step_loudness_match`（PR2 已有） | base（已有） |

**重要**: 从 multi 复制时，把代码物理复制到 omni runner 内（独立维护），**不**让 omni 通过 `from appcore.runtime_multi import ...` 引用 multi —— 否则 multi 改动会污染 omni。

### 6.3 OmniProfile / OmniLocalizationAdapter 的命运

PR1–PR4c 把 omni 的算法搬进了 `OmniProfile`。本次合并后 OmniProfile 可以**保留**作为占位（profile_code = "omni"），但其 4 个 hook（post_asr / translate / tts / subtitle）改成读 `task["plugin_config"]` 后 dispatch 到 runner 上对应的算法方法。这样 PR1–PR7 的 profile / engine / strategy 抽象不被破坏，只是 omni 这一个 profile 内部多了一层 dispatch。

`OmniLocalizationAdapter` 仍由 omni runner 内部 `_get_localization_module` 提供，给 base TTS duration loop 用。

### 6.4 Plugin_config 校验

提交任务前后端 validator 跑一遍（同 §4.4 的依赖规则）。校验失败 HTTP 400 + 中文错误信息。

---

## 7. 验收标准

### 7.1 4 套等价 preset 端到端跑通

用同一段测试视频（`testuser.md` 里的标准测试视频），分别用 4 个系统级 preset 创建 4 个任务，必须从 extract 一路跑到 export 成功，最终能下载合成视频。每个 preset 的中间产物（asr / translate / tts / subtitle）跟原始模块（omni / sentence_translate / translate_lab）的同步骤产物**功能上等价**（不要求字节一致，因为 LLM 输出有随机性，但产物结构 + step 顺序 + 关键 artifact 文件名要对齐）。

### 7.2 Preset CRUD 单元测试

- 系统级 preset：admin 能 CRUD；普通 user 只能读和选择
- 新建任务弹窗不提供用户级 preset 创建、编辑、删除入口
- 全站默认：admin 设置后所有 user 看到的弹窗初始 preset 切换
- 删除当前全站默认：拒绝，必须先选另一个

### 7.3 Plugin_config 校验单元测试

- 4 个 radio 缺失任一 → 400
- 依赖关系不满足（如 `shot_char_limit` 但没开 `shot_decompose`）→ 400
- 互斥关系冲突 → 后端自动 silent fix（如 `av_sentence + source_anchored` 自动 uncheck source_anchored）

### 7.4 UI smoke

- 新建弹窗：默认 preset 加载、切换系统级 preset、提交选中 preset 的 plugin_config 快照；不显示能力点表单、不显示「另存为 / 新建流程 / 删除」
- `/settings` → `Omni Preset` tab：admin 可见、普通 user 不可见；admin 能 CRUD 系统级 preset + 设全站默认
- sidebar：`/sentence-translate/` 和 `/translate-lab/` 入口隐藏；老任务直链仍能访问详情页

### 7.5 Deprecate 行为

- `/sentence-translate/` 列表页打开：显示 banner "本模块已 deprecated，请使用 `/omni-translate/` 并选择 av-sync-current preset"，新建按钮 disabled
- `/translate-lab/` 同
- runtime / 后端 API 不动（防御性保留）

---

## 8. 不在范围内（明确不做）

- multi 模块的任何改动（包括 sidebar 入口、template、runtime、blueprint、DB type、web service）
- ja / de / fr 模块的任何改动
- 物理删除 sentence_translate / translate_lab 的代码 / DB 表 / migration 文件
- DB schema 进一步归一（task.profile_code 字段、6 个 blueprint 合并到 `/translate/` 等都是后续 PR）
- 跨 user 的 preset 共享（用户级 preset = 严格私有）
- 用户级"我的默认 preset"持久化（按 §4.2 不做）
- preset 版本历史 / undo（preset 改了就改了）
- 实时跟随：preset 改了不影响已有任务（按 §4.5 task 存快照）
- multi 任务自动迁移到 omni（multi 用户继续用 multi，omni 是独立实验入口）

---

## 9. Related

- 实施步骤：[`docs/superpowers/plans/2026-05-07-omni-translate-merge.md`](../plans/2026-05-07-omni-translate-merge.md)
- 前置 PR1–PR7（在 `refactor/omni-tts-pluggable` 分支上，合并到 master 后本任务才能开工）
- 现有 omni runner: `appcore/runtime_omni.py`
- 现有 sentence_translate runner: `appcore/runtime_sentence_translate.py`
- 现有 translate_lab runner: `appcore/runtime_v2.py`
- TtsEngine ABC: `appcore/tts_engines/`（PR5）
- TtsConvergenceStrategy ABC: `appcore/tts_strategies/`（PR6）
- TranslateProfile ABC: `appcore/translate_profiles/`（PR1–PR4c）
