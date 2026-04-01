# 音色选择、翻译提示词、Pipeline 单线化重构设计

Date: 2026-04-02
Status: Approved

## Overview

三项关联改动：
1. 音色系统从共享 JSON 迁移到数据库，按用户隔离，前端改为列表式选择（参考 ElevenLabs）
2. 翻译提示词从硬编码改为用户级可选可编辑，支持多次翻译对比
3. Pipeline 从翻译往后合并为单线，不再产出两个变体

---

## Part 1：音色系统重构

### 数据库

新建 `user_voices` 表：

```sql
CREATE TABLE user_voices (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    gender ENUM('male','female') NOT NULL,
    elevenlabs_voice_id VARCHAR(50) NOT NULL,
    description TEXT DEFAULT '',
    style_tags JSON DEFAULT NULL,
    preview_url VARCHAR(500) DEFAULT '',
    source VARCHAR(50) DEFAULT 'manual',
    labels JSON DEFAULT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_voice (user_id, elevenlabs_voice_id)
);
```

新用户首次访问 `/api/voices` 时，自动插入 Adam（`pNInz6obpgDQGcFmaJgB`）+ Rachel（`21m00Tcm4TlvDq8ikWAM`）两条默认记录。

### API 变更

| 端点 | 变更 |
|------|------|
| `GET /api/voices` | 加 `user_id` 过滤，只返回当前用户的音色 |
| `POST /api/voices/import` | 导入时绑定 `current_user.id` |
| `POST /api/voices` | 创建时绑定 `current_user.id` |
| `PUT /api/voices/<id>` | 只能改自己的 |
| `DELETE /api/voices/<id>` | 只能删自己的 |

废弃 `voices/voices.json` 和 `pipeline/voice_library.py` 中的 JSON 读写逻辑，`VoiceLibrary` 改为读写数据库。

### 前端音色选择区块

独立为一个 card，放在生成配置（字幕位置+确认模式）**上方**。

布局参考 ElevenLabs：
- 列表式，每行显示：音色名、性别标签、描述（截断）、▶ 试听按钮
- 点击行选中，选中行高亮
- 右上角 **+ 导入** 按钮打开导入弹窗（复用现有 import modal）
- 音色多时支持滚动（max-height 约 300px）
- 试听：点击 ▶ 直接播放 `preview_url`，无需先选中

---

## Part 2：翻译提示词系统

### 数据库

新建 `user_prompts` 表：

```sql
CREATE TABLE user_prompts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    prompt_text TEXT NOT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

新用户首次访问 `/api/prompts` 时，自动插入两条预设：
- 「普通翻译」→ `pipeline/localization.py` 中的 `LOCALIZED_TRANSLATION_SYSTEM_PROMPT`
- 「黄金3秒+CTA」→ `pipeline/localization.py` 中的 `HOOK_CTA_TRANSLATION_SYSTEM_PROMPT`

### API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/prompts` | GET | 返回当前用户的提示词列表（含完整 prompt_text） |
| `/api/prompts` | POST | 新建自定义提示词 |
| `/api/prompts/<id>` | PUT | 更新提示词内容（名称、prompt_text） |
| `/api/prompts/<id>` | DELETE | 删除（系统预设的 `is_default=true` 不可删） |

### 前端交互

在翻译步骤的配置区域展示提示词选择：
- 提示词列表，每项显示名称，选中后展开显示完整提示词内容
- 提示词内容在 textarea 中，可直接编辑
- 编辑后可点「保存」持久化到数据库，或直接点「开始翻译」用当前内容（不保存）
- 翻译完成后，可切换另一个提示词模板，点「重新翻译」
- 多次翻译结果并列展示（最多保留 3 次），每个结果有「选用这个」按钮
- 选定后，后续步骤基于选定结果继续

### Pipeline 变更

- `_step_translate` 接收 `prompt_id` 或 `prompt_text` 参数
- 不再循环 `VARIANT_KEYS`，只翻译一次
- 翻译结果存入 `variants["normal"]`（复用现有 variant 数据结构）

---

## Part 3：Pipeline 单线化

### 步骤变更

从翻译往后，所有步骤只处理 `variants["normal"]`：

| 步骤 | 当前 | 改后 |
|------|------|------|
| translate | 循环 `VARIANT_KEYS` 各翻译一次 | 只翻译一次，存 `variants["normal"]` |
| tts | 循环 `VARIANT_KEYS` 各生成语音 | 只跑 `variants["normal"]` |
| subtitle | 循环 `VARIANT_KEYS` 各生成字幕 | 只跑 `variants["normal"]` |
| compose | 循环 `VARIANT_KEYS` 各合成视频 | 只跑 `variants["normal"]` |
| export | 循环 `VARIANT_KEYS` 各导出 CapCut | 只跑 `variants["normal"]` |

改动方式：`appcore/runtime.py` 中各步骤的 `for variant in VARIANT_KEYS` 循环去掉，固定为 `"normal"`。

### 前端 artifact 渲染

- `variant_compare` 布局不再触发，改为单列展示
- 下载区只展示一套结果（硬字幕视频、软字幕视频、SRT、CapCut 工程包）

### 翻译步骤的重新翻译机制

新增 API：
- `POST /api/tasks/<id>/retranslate` — body: `{prompt_id?, prompt_text?}`，用指定提示词重新翻译，返回新结果
- `PUT /api/tasks/<id>/select-translation` — body: `{index}`，选定第 N 次翻译结果作为后续步骤的输入

前端交互：
- 翻译完成后，翻译预览区显示「换模板重新翻译」按钮
- 点击后展示提示词选择器，选新模板后调用 retranslate API
- 多次翻译结果并列展示，用户点「选用这个」确定
- 选定后触发后续步骤（TTS 等）

---

## 整体页面布局（从上到下）

1. **音色选择**（独立 card）— 列表式，可试听，可导入
2. **生成配置**（card）— 字幕位置 + 确认模式（两列）
3. **开始处理** 按钮
4. **处理进度**（pipeline card）— 8 个步骤
   - 翻译步骤扩展：提示词选择/编辑 → 翻译 → 可换模板重新翻译 → 选定结果继续
5. **生成结果**（下载区）— 单套结果

---

## 涉及文件

### 新建
- `db/migrations/002_user_voices_and_prompts.sql` — 建表 SQL

### 修改
- `pipeline/voice_library.py` — 改为读写数据库
- `pipeline/elevenlabs_voices.py` — import_voice 绑定 user_id
- `web/routes/voice.py` — 所有端点加 user_id 过滤
- `web/routes/task.py` — 新增 retranslate、select-translation 端点
- `appcore/runtime.py` — 各步骤去掉 variant 循环，translate 接收 prompt 参数
- `pipeline/localization.py` — 提取默认提示词文本供初始化使用
- `web/templates/_task_workbench.html` — 音色区块独立、翻译提示词 UI
- `web/templates/_task_workbench_scripts.html` — 音色列表交互、提示词选择/编辑/重新翻译
- `web/templates/_task_workbench_styles.html` — 新区块样式

### 新建路由
- `web/routes/prompt.py` — 提示词 CRUD 蓝图，注册到 `web/app.py`

### 废弃
- `voices/voices.json` — 数据迁移到数据库后不再使用
