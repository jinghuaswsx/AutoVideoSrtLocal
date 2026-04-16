# 视频翻译 V2 — 分镜驱动翻译系统设计

> 日期：2026-04-16  
> 状态：设计确认，待实现  
> 入口菜单：视频翻译（测试）  
> 数据库类型标识：`translate_lab`

---

## 1. 目标

解决现有视频翻译模块的核心问题：生成的翻译文案长度不一（过长或过短），且没有调节音频速度，导致视频画面与音频描述内容经常对不上，出现错位。

**设计目标**：
- 用户输入视频后全流程自动化
- 每个分镜的配音时长与原视频分镜对齐（容忍度 ≤ 分镜时长的 10%）
- 支持中→英、中→小语种、英→小语种三个翻译方向
- 独立于现有三个翻译模块，不影响其正常使用

---

## 2. 整体架构

### 方案选择：独立 PipelineRunner V2

新建完全独立的流水线，不修改现有 `runtime.py` 和三个翻译模块的任何代码。可以复用现有底层工具函数（`extract_audio`、`compose_video` 等）。

### 文件结构

```
pipeline/
├── shot_decompose.py      # Gemini 分镜拆解
├── voice_match.py         # 音色匹配（余弦相似度）
├── voice_library_sync.py  # ElevenLabs 全量音色库同步
├── speech_rate_model.py   # 语速模型（学习 + 预估）
├── translate_v2.py        # 分镜级翻译（带时长约束）
├── tts_v2.py              # 分镜级 TTS + 时长校验循环
├── subtitle_v2.py         # 字幕生成（统一字号、≤2行）

appcore/
├── runtime_v2.py          # 新流水线编排器

web/
├── routes/translate_lab.py            # 路由
├── templates/translate_lab_list.html  # 列表页
├── templates/translate_lab_detail.html # 详情页
├── services/translate_lab_runner.py   # Socket.IO 适配
```

### 新流水线 7 步流程

| 步骤 | 名称 | 职责 |
|------|------|------|
| 1 | extract | 提取音频（复用现有） |
| 2 | shot_decompose | Gemini 分析视频，输出分镜列表 |
| 3 | voice_match | 分析原始音频特征 → 匹配 ElevenLabs 音色 |
| 4 | translate | 逐分镜翻译，语速模型约束文案长度 |
| 5 | tts_verify | 逐分镜 TTS → 时长校验 → 超限则微调文案 → 迭代 |
| 6 | subtitle | 基于最终 TTS 时长生成字幕（统一字号、≤2行） |
| 7 | compose | 合成最终视频（复用现有） |

数据库中用 `type = 'translate_lab'` 区分。

---

## 3. 分镜拆解模块

**文件**：`pipeline/shot_decompose.py`

**输入**：视频文件路径 + ASR 转写结果（带时间戳的文本）

**处理流程**：
1. 将视频上传到 Gemini Files API
2. Prompt 指导 Gemini Pro 分析视频画面，输出镜头切换点
3. 将 ASR 文本按分镜时间区间归类

**输出数据结构**：

```json
{
  "shots": [
    {
      "index": 1,
      "start": 0.0,
      "end": 5.2,
      "duration": 5.2,
      "description": "女主角走进咖啡厅，环顾四周",
      "source_text": "她推开门，走进那家她常去的咖啡馆",
      "source_language": "zh"
    }
  ]
}
```

**关键规则**：
- `description` 是 Gemini 对画面的描述，供翻译步骤参考上下文语境
- ASR 文本与分镜的归类基于时间戳交叉匹配，一段 ASR 可能跨两个分镜，需按边界拆分
- 无对白的纯画面分镜标记为 `silent`，跳过翻译和 TTS

---

## 4. ElevenLabs 全量音色库与自动匹配

### 4.1 音色库同步

**文件**：`pipeline/voice_library_sync.py`

- 调用 `GET /v1/shared-voices`，分页爬取全量音色（每页 100 条，10,000+ 条）
- 存储到数据库表 `elevenlabs_voices`：
  - `voice_id`、`name`、`gender`、`age`、`language`、`accent`、`category`
  - `preview_url`（预览音频地址）
  - `audio_embedding`（从预览音频提取的声学特征向量）
  - `synced_at`（同步时间）
- 提供管理入口：手动触发全量同步 / 增量同步
- 首次同步后，批量下载预览音频 → 提取特征向量 → 回写 `audio_embedding`

### 4.2 自动音色匹配

**文件**：`pipeline/voice_match.py`

流程：
1. 从原始视频音频中提取一段人声片段（取中间稳定段）
2. 用 resemblyzer 生成 256 维 speaker embedding 向量
3. 按目标语言过滤音色库，计算余弦相似度，取 top 3
4. 根据配置：
   - **自动模式** → 直接选 top 1
   - **人工确认模式** → 返回 top 3 候选，前端展示预览播放器，用户选定后继续

### 4.3 特征提取

使用 resemblyzer 库，从音频中提取 256 维 speaker embedding，轻量级，不需要 GPU。

---

## 5. 语速模型与翻译适配

### 5.1 语速模型

**文件**：`pipeline/speech_rate_model.py`

核心概念：每个音色在不同语言下有不同的「字符/秒」速率。

**建模流程**：
1. **初始化基准**：选定音色后，用一段标准文本（约 50-80 词）生成 TTS，测量实际时长，算出初始 `chars_per_second`
2. **任务迭代修正**：每次真实任务完成后，用实际数据增量更新模型
3. **存储**：数据库表 `voice_speech_rate`：
   - `voice_id`、`language`、`chars_per_second`
   - `sample_count`（样本数）
   - `updated_at`

**预估公式**：
```
预估时长 = 文案字符数 / chars_per_second
允许时长 = 分镜时长 × 0.9（留 10% 余量）
目标字符数上限 = 允许时长 × chars_per_second
```

### 5.2 分镜级翻译

**文件**：`pipeline/translate_v2.py`

逐分镜调用 LLM 翻译，Prompt 包含：
- 当前分镜的原始文案 + 画面描述（来自 Gemini）
- 目标字符数上限（来自语速模型）
- 前后分镜的译文（保持上下文连贯）
- 目标语言 + 本土化风格要求

LLM 输出：
```json
{
  "shot_index": 1,
  "translated_text": "She pushed open the door and stepped into her favorite café.",
  "char_count": 58
}
```

如果 LLM 返回的文案超过目标字符数上限，立即要求缩写，最多重试 2 次。

---

## 6. TTS 生成与时长校验循环

**文件**：`pipeline/tts_v2.py`

### 逐分镜处理流程

```
翻译文案 → TTS 生成 → 测量实际时长 → 校验 → 通过/微调
```

### 校验逻辑

```
实际时长 ≤ 分镜时长           → 通过（尾部留白，自然过渡）
实际时长 > 分镜时长 × 1.10    → 超限，需要微调文案
```

### 微调循环

1. 计算超出比例，将当前文案 + 超出信息发回 LLM 缩写
2. 缩写后重新 TTS → 再次校验
3. 最多迭代 3 轮。3 轮后仍超限则记录警告，继续下一个分镜

### LLM 微调 Prompt 包含

- 当前文案及其 TTS 实际时长
- 目标时长上限
- 需要削减的大致字符数
- 要求保留核心语义，只删减修饰性内容

### 语速模型更新

每个分镜 TTS 完成后，将「文案字符数 / 实际音频时长」作为新样本，增量更新该音色的 `chars_per_second`。

### 积分消耗

每次 TTS 生成消耗积分，微调循环最坏情况单个分镜消耗 4 次（初始 + 3 次重试）。语速模型越准，实际重试率越低。

---

## 7. 字幕生成

**文件**：`pipeline/subtitle_v2.py`

**输入**：每个分镜的最终译文 + TTS 音频时长

### 字幕拆分逻辑

1. 每个分镜的译文作为一个字幕块
2. 文本过长时按语义断句拆成子块（标点、连词、从句边界）
3. 每个子块最多 2 行

### 统一字号计算

1. 遍历所有字幕块，找出最长的那条
2. 在「最长字幕 ≤ 2 行且不超出安全区域宽度」约束下反推全局字号
3. 字号范围：16px ~ 42px
4. 所有字幕统一使用该字号

### 字幕位置

- 默认底部居中（安全区域内，距底边约 10% 画面高度）
- 沿用现有字幕位置配置

### 输出

标准 SRT 文件 + 字幕元数据（字号、位置、每条字幕行数）

---

## 8. 前端交互

### 侧栏菜单

在 `layout.html` 视频翻译区域下方新增「视频翻译（测试）」菜单项，路由 `/translate-lab`。

### 列表页

- 沿用现有翻译模块的卡片列表风格
- 显示任务状态、源语言 → 目标语言、创建时间
- 右上角「新建任务」按钮

### 新建任务

1. 上传视频（或选择已有素材）
2. 选择源语言（中文/英文）和目标语言（英文/小语种）
3. 音色匹配模式切换：自动 / 人工确认
4. 点击开始

### 详情页

- 顶部进度条（7 步）
- 各步骤结果依次展开：
  - **分镜拆解**：时间轴列表，每个分镜显示时间区间 + 画面描述 + 原始文案
  - **音色匹配**：人工模式显示 3 个候选音色卡片 + 预览播放按钮
  - **翻译 + TTS**：逐分镜原文 → 译文 → 音频播放器 → 时长对比
  - **字幕预览**：字幕文件下载
  - **合成视频**：播放器预览最终成品

### 实时推送

复用现有 Socket.IO 机制，事件名加 `lab_` 前缀区分。

---

## 9. 新增数据库表

| 表名 | 用途 |
|------|------|
| `elevenlabs_voices` | 全量音色库（voice_id, name, gender, age, language, accent, category, preview_url, audio_embedding, synced_at） |
| `voice_speech_rate` | 语速模型（voice_id, language, chars_per_second, sample_count, updated_at） |

现有 `projects` 表新增 `type = 'translate_lab'` 值，其余字段复用。

---

## 10. 依赖新增

| 库 | 用途 |
|----|------|
| `resemblyzer` | 音频 speaker embedding 提取（256 维向量） |
| `google-genai` | Gemini Pro API（分镜分析） |

---

## 11. 不在本期范围

- 替换现有三个翻译模块（验证通过后再决定）
- 视频去字幕（已在独立模块开发中）
- 多人声场景（单音色覆盖全片）
- 背景音乐分离与保留
