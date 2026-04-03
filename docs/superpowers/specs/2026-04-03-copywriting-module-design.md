# 文案创作模块设计文档

> 日期：2026-04-03
> 状态：已确认

## 概述

为 AutoVideoSrt 新增独立的"文案创作"功能模块。核心场景：用户拥有仅有背景音乐、没有口播的短视频素材，需要系统根据视频画面内容 + 商品信息，自动生成面向美国 TikTok 市场的短视频卖货文案。生成的文案可就地接入 TTS → 合成流程，输出带口播的成品视频。

## 目标用户场景

1. 用户有一段 BGM 素材视频（无口播）
2. 上传视频，系统自动抽取关键帧
3. 用户填写商品信息（标题、主图、价格、卖点、目标人群）
4. 系统调用大模型，结合视频画面和商品信息生成结构化文案
5. 用户审阅、编辑、逐段微调文案
6. 可选：一键生成 TTS 语音并合成到视频

## 方案选型

**独立管线方案**——文案创作作为一条全新的独立管线，有自己的步骤序列、独立的项目类型和独立的页面入口。复用底层模块（TTS、compose、EventBus），但 UI 和流程编排独立于现有视频翻译管线。

理由：文案创作和视频翻译是两条本质不同的流程，独立管线解耦清晰，互不影响，各自演进。

---

## 数据模型

### projects 表扩展

```sql
ALTER TABLE projects ADD COLUMN type VARCHAR(20) DEFAULT 'translation' NOT NULL;
-- 'translation' = 现有视频翻译项目
-- 'copywriting' = 文案创作项目
```

### 新增 copywriting_inputs 表

```sql
CREATE TABLE copywriting_inputs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    project_id VARCHAR(36) NOT NULL,
    product_title VARCHAR(255),
    product_image_url TEXT,
    price VARCHAR(50),
    selling_points TEXT,         -- JSON 数组 ["卖点1", "卖点2"]
    target_audience VARCHAR(255),
    extra_info TEXT,
    language VARCHAR(10) DEFAULT 'en',  -- 'en' 或 'zh'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
```

### state_json 中的文案数据结构

```json
{
  "keyframes": ["frame_001.jpg", "frame_002.jpg", "frame_003.jpg"],
  "copy": {
    "segments": [
      {"index": 0, "label": "Hook", "text": "Wait, this actually works?", "duration_hint": 3.0},
      {"index": 1, "label": "Problem", "text": "Tired of messy smoothies?", "duration_hint": 5.0},
      {"index": 2, "label": "Product", "text": "This portable blender changes everything.", "duration_hint": 7.0},
      {"index": 3, "label": "Demo", "text": "Watch — 30 seconds, perfectly blended.", "duration_hint": 5.0},
      {"index": 4, "label": "CTA", "text": "Link in bio before it sells out!", "duration_hint": 3.0}
    ],
    "full_text": "Wait, this actually works? Tired of messy smoothies? ...",
    "tone": "Upbeat, conversational",
    "target_duration": 23
  }
}
```

### user_prompts 表扩展

```sql
ALTER TABLE user_prompts ADD COLUMN type VARCHAR(20) DEFAULT 'translation' NOT NULL;
-- 'translation' = 翻译提示词（现有）
-- 'copywriting' = 文案创作提示词（新增）
```

---

## 管线步骤

```
① upload → ② keyframe → ③ copywrite → ④ tts → ⑤ compose
```

| 步骤 | 模块 | 说明 | 触发方式 |
|------|------|------|----------|
| upload | 新增 | 上传视频 + 填写商品信息表单 + 上传商品主图 | 用户手动 |
| keyframe | 新增 `pipeline/keyframe.py` | ffmpeg + scenedetect 抽取关键帧 | 上传后自动 |
| copywrite | 新增 `pipeline/copywriting.py` | 关键帧 + 商品信息 → LLM 生成分段文案 | 自动，完成后暂停等人工确认 |
| tts | 复用 `pipeline/tts.py` | 文案 → ElevenLabs 语音 | 用户点击"生成语音视频"触发 |
| compose | 复用 `pipeline/compose.py` | TTS 音频 + 原视频 → 成品视频 | 自动跟随 TTS |

### 步骤间流转

- 步骤 ①② 连续自动执行——上传完视频立即抽帧
- 步骤 ③ 完成后暂停，等用户审阅/编辑文案
- 步骤 ④⑤ 是可选的——用户确认文案后可以选择"仅导出文案"或"继续生成语音视频"
- 单段重写——在步骤 ③ 的编辑界面，用户选中某一段，点击"AI 重写"，只对该段调用 LLM

---

## LLM 调用设计

### 模型选择

复用现有 LLM 选择机制——用户在设置里选的哪个就用哪个（Claude / 豆包）。

### Vision 能力兜底

| 用户选择的 LLM | Vision 支持 | 处理方式 |
|---------------|------------|---------|
| Claude (OpenRouter) | 支持 | 直接发送关键帧图片 + 商品主图 |
| 豆包 LLM | 不支持 | 降级：不发图片，仅发文本信息，UI 提示"当前模型不支持图片输入，文案仅基于文字信息生成" |

### Prompt 结构

```
系统提示词（文案创作专家角色设定）
    ↓
用户消息：
  - 图片：关键帧截图（多张） + 商品主图
  - 文本：商品标题、价格、卖点、目标人群、补充信息
  - 指令：输出语言、风格要求
    ↓
结构化输出：JSON（分段文案 + 每段时长建议 + 完整文案 + 语气描述）
```

### 单段重写

对某一段调用 LLM 时，上下文包含：
- 原始商品信息（精简版）
- 完整文案（让 LLM 知道上下文）
- 标记要重写的段落
- 用户的修改要求（可选，如"更口语化"、"加入 CTA"）

### 默认系统提示词

系统预置中英文两版默认提示词，用户可切换，默认选中英文版。用户也可以自定义提示词。

#### 英文版（默认）

```
You are an expert TikTok short-video copywriter specializing in US e-commerce ads.

**Your task:** Based on the video keyframes, product information, and product images provided, write a compelling short-video sales script for the US market. The script must match the video's visual content and the product being sold.

**Video understanding:** Carefully analyze each keyframe to understand the video's scenes, actions, mood, and pacing. Your script must align with what's happening on screen — each segment should correspond to the visual flow.

**Script structure (follow TikTok best practices):**
1. **Hook (0-3s):** An attention-grabbing opening that stops the scroll. Use curiosity, shock, relatability, or a bold claim. Must connect to what's shown in the first frames.
2. **Problem/Scene (3-8s):** Identify a pain point or set a relatable scene that the target audience experiences. Match the video's visual context.
3. **Product Reveal (8-15s):** Introduce the product naturally as the solution. Highlight key selling points that are visible in the video. Be specific — mention features shown on screen.
4. **Social Proof / Demo (15-22s):** Reinforce credibility — results, transformations, or demonstrations visible in the video. Use sensory language.
5. **CTA (last 3-5s):** Clear call-to-action. Create urgency. Direct viewers to take action.

**Style guidelines:**
- Conversational, authentic tone — sounds like a real person, not an ad
- Short punchy sentences, easy to speak aloud
- Use power words: "obsessed", "game-changer", "finally", "you need this"
- Match the energy/mood of the video (upbeat, calm, dramatic, etc.)
- Aim for 15-45 seconds total speaking time depending on video length

**Output format:** Return a JSON object:
{
  "segments": [
    {"label": "Hook", "text": "...", "duration_hint": 3.0},
    {"label": "Problem", "text": "...", "duration_hint": 5.0},
    {"label": "Product", "text": "...", "duration_hint": 7.0},
    {"label": "Demo", "text": "...", "duration_hint": 5.0},
    {"label": "CTA", "text": "...", "duration_hint": 3.0}
  ],
  "full_text": "Complete script as one paragraph",
  "tone": "Description of the tone used",
  "target_duration": 23
}
```

#### 中文版

```
你是一位专业的短视频带货文案专家，擅长为美国 TikTok 市场创作电商广告脚本。

**你的任务：** 根据提供的视频关键帧、商品信息和商品图片，撰写一段面向美国市场的短视频带货口播文案。文案必须与视频画面内容和所售商品高度匹配。

**视频理解：** 仔细分析每一帧关键画面，理解视频的场景、动作、氛围和节奏。你的文案必须与画面同步——每一段都要对应视频的视觉流程。

**文案结构（遵循 TikTok 最佳实践）：**
1. **Hook 开头（0-3秒）：** 抓眼球的开场，让用户停止滑动。用好奇心、冲击感、共鸣或大胆主张。必须关联开头几帧画面。
2. **痛点/场景（3-8秒）：** 点出目标用户的痛点或建立一个有共鸣的场景，匹配视频画面。
3. **产品展示（8-15秒）：** 自然引入产品作为解决方案。突出视频中可见的核心卖点，要具体——提及画面中展示的功能特点。
4. **信任背书/演示（15-22秒）：** 强化可信度——视频中可见的效果、变化或演示。使用感官化语言。
5. **CTA 行动号召（最后3-5秒）：** 清晰的行动指令，制造紧迫感，引导用户下单。

**风格要求：**
- 口语化、真实自然的语气——听起来像真人分享，不像广告
- 短句为主，朗朗上口，适合口播
- 善用有感染力的词汇
- 匹配视频的情绪和节奏（活力、舒缓、震撼等）
- 根据视频时长，口播总时长控制在 15-45 秒

**输出格式：** 返回 JSON 对象（结构同英文版）
```

---

## 前端设计

### 入口

导航栏新增"文案创作"菜单项，与现有"项目"菜单平级。

### 文案项目列表页

复用现有 `projects.html` 的布局风格，通过 `project.type = 'copywriting'` 过滤：
- 卡片网格 / 列表切换
- 每个卡片：视频缩略图、商品标题、创建时间、状态（草稿/已生成/已合成）
- 右上角"新建文案"按钮

### 文案创作工作页 — 混合式布局

#### 上方区域：素材 & 商品信息（可折叠）

- 左侧：视频播放器（小尺寸）
- 中间：关键帧缩略图横排展示（抽帧完成后自动填充）
- 右侧：商品信息摘要（标题、价格、卖点、主图缩略图）
- 首次进入时展开（填写表单），文案生成后自动折叠为摘要模式
- 可随时展开查看/修改商品信息

#### 下方区域：文案编辑（全宽）

- 顶部工具栏：
  - 提示词选择器（中文版/英文版/自定义），默认英文版
  - 语言选择（输出中文/英文）
  - LLM 模型显示（跟随全局设置）
  - "生成文案"按钮
- 文案分段展示：
  - 每段一行卡片：`[标签] 文案内容 ... [🔄 重写] [✏️ 编辑]`
  - 点击"重写"：弹出小输入框可填写修改要求，调用 LLM 单段重写
  - 点击"编辑"：该段变为可编辑文本框，手动修改
  - 段落可拖拽排序
- 底部操作栏：
  - "全部重新生成"按钮
  - "复制文案"按钮
  - "生成语音视频"按钮（触发 TTS → 合成）
  - "导出文案"按钮（纯文本下载）

#### TTS & 合成区域

点击"生成语音视频"后，在文案编辑区下方展开：
- TTS 进度条
- 生成完成后：音频预览播放器 + 视频预览播放器
- 下载按钮（视频 / 音频 / SRT）

---

## 路由设计

### 页面路由

| 路由 | 说明 |
|------|------|
| `GET /copywriting` | 文案项目列表页 |
| `GET /copywriting/<project_id>` | 文案创作工作页 |

### API 路由

| 路由 | 说明 |
|------|------|
| `POST /api/copywriting/upload` | 上传视频 + 商品信息，创建项目并自动抽帧 |
| `PUT /api/copywriting/<id>/inputs` | 更新商品信息 |
| `POST /api/copywriting/<id>/generate` | 触发文案生成（调用 LLM） |
| `POST /api/copywriting/<id>/rewrite-segment` | 单段重写 |
| `PUT /api/copywriting/<id>/segments` | 保存用户编辑后的文案 |
| `POST /api/copywriting/<id>/tts` | 触发 TTS + 合成 |
| `GET /api/copywriting/<id>/download/<artifact>` | 下载产物（视频/音频/SRT/文案文本） |

### SocketIO 事件

复用现有 EventBus 模式：
- `join_copywriting_task` — 订阅文案任务更新
- `copywriting_update` — 步骤状态变化（抽帧中/生成中/TTS中/完成）
- `copywriting_artifact_ready` — 产物就绪（关键帧/文案/音频/视频）

---

## 新增文件

| 文件 | 说明 |
|------|------|
| `pipeline/keyframe.py` | 关键帧抽取（ffmpeg + scenedetect） |
| `pipeline/copywriting.py` | 文案生成 LLM 调用 + 单段重写 |
| `appcore/copywriting_runtime.py` | 文案管线编排（类似 runtime.py） |
| `web/routes/copywriting.py` | Flask 蓝图：页面路由 + API |
| `web/templates/copywriting_list.html` | 文案项目列表页 |
| `web/templates/copywriting_detail.html` | 文案创作工作页 |
| `web/templates/_copywriting_scripts.html` | 工作页 JavaScript |
| `web/templates/_copywriting_styles.html` | 工作页 CSS |
| `db/migrations/add_copywriting.sql` | 数据库迁移脚本 |

---

## 复用的现有模块

| 模块 | 复用方式 |
|------|----------|
| `pipeline/tts.py` | 直接调用，传入文案文本 |
| `pipeline/compose.py` | 直接调用，合成 TTS 音频 + 原视频 |
| `pipeline/subtitle.py` | 生成 SRT 字幕 |
| `appcore/events.py` | EventBus 发布状态事件 |
| `appcore/tos_clients.py` | 上传产物到 TOS |
| `appcore/db.py` | 数据库连接 |
| `web/extensions.py` | SocketIO 实例 |
| `web/auth.py` | 登录验证 |
