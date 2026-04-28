# AutoVideoSrt — 抖音转 TikTok 视频本土化系统

## 1. 项目定位

将抖音平台的卖货爆款短视频，翻译为可直接在 TikTok 广告系统投放的英文视频。

**核心价值**：中文卖货视频 → 原生感美式英文广告视频（文案 + 语音 + 字幕 + 剪映项目），一键出成品。

**目标用户**：TikTok 跨境电商广告投手，需要把已验证的抖音素材快速本土化投放到美国市场。

---

## 2. 系统架构

### 2.1 整体流程

```
本地视频（预处理过的抖音视频）
    ↓
[自动] 音频提取 (ffmpeg → 16kHz WAV)
    ↓
[自动] ASR 语音识别 (豆包大模型 v3)  →  中文文案 + 时间戳
    ↓
[自动] 镜头检测 + 语义联合分段 (PySceneDetect + ASR)  →  分镜段落
    ↓
★ [人工确认] 查看分段结果，可手动调整段落边界
    ↓
[自动] Claude 翻译本土化  →  TikTok 卖货风格英文文案
    ↓
★ [人工确认] 查看/编辑翻译结果（可逐句修改）
    ↓
★ [人工配置] 选择音色（男/女）、字幕位置
    ↓
[自动] ElevenLabs TTS 生成英文音频（音频保持原速，不拉伸压缩）
    ↓
[自动] 音频 + 视频段落拟合（视频按分镜裁剪匹配 TTS 时长，尾部段可舍弃）
    ↓
[自动] 字幕格式化（两行均衡、首字母大写、不截断单词、长行在前）
    ↓
[自动] 输出成品
    ├── 剪映专业版项目文件（视频/音频/字幕轨道全配好）
    ├── .srt 软字幕文件
    └── 硬字幕烧录版视频
    ↓
★ [人工确认] 预览成品，确认导出
```

### 2.2 技术选型

| 模块 | 技术方案 | 选型理由 |
|------|---------|---------|
| 音频提取 | ffmpeg (16kHz 单声道 WAV) | 通用可靠，豆包 ASR 推荐格式 |
| 语音识别 | 豆包大模型录音文件识别 v3 (`volc.seedasr.auc`) | 中文识别质量优，火山引擎生态统一 |
| 音频中转 | 火山引擎 TOS 对象存储 + 预签名 URL | 豆包 ASR 只接受 URL，TOS 同生态延迟低 |
| 翻译本土化 | Claude (via OpenRouter) | 本土化能力强，TikTok 广告文案风格可控 |
| TTS 语音 | ElevenLabs SDK (`eleven_turbo_v2_5`) | 自然度最高，用户已有 Pro 会员 |
| 分镜检测 | PySceneDetect + ASR 时间戳联合 | 算法检测画面切换，语义确认分段边界 |
| 字幕渲染 | 自研格式化 + ffmpeg 烧录 | 首字母大写/智能断行/两行均衡 |
| 最终输出 | 剪映专业版 `draft_content.json` | 用户有剪映会员字体需求，需在剪映里选字体 |
| Web 界面 | Flask + SocketIO (threading) | 轻量，支持 WebSocket 实时进度 |

### 2.3 项目结构

```
AutoVideoSrt/
├── main.py                         # 启动入口
├── config.py                       # 配置 (API keys, 路径, 参数)
├── requirements.txt
├── PLAN.md                         # 本文档
│
├── pipeline/                       # 核心处理模块（每个文件单一职责）
│   ├── extract.py                  # ffmpeg 音频提取
│   ├── asr.py                      # 豆包 ASR v3 (提交→轮询→解析)
│   ├── storage.py                  # TOS 对象存储 (上传/预签名/删除)
│   ├── translate.py                # Claude 翻译 (TikTok 卖货 prompt)
│   ├── tts.py                      # ElevenLabs TTS (SDK + 音色库)
│   ├── subtitle.py                 # 字幕格式化 (断行/大写/SRT)
│   └── compose.py                  # ffmpeg 视频合成 (软硬字幕)
│
├── web/                            # Web 层 (Flask, 职责分层)
│   ├── app.py                      # 工厂函数 (~30行, 只注册蓝图)
│   ├── extensions.py               # socketio 单例
│   ├── store.py                    # 任务状态内存存储 (可替换 Redis)
│   ├── routes/
│   │   ├── task.py                 # 任务 CRUD + 下载 API
│   │   └── voice.py                # 音色库查询 API
│   ├── services/
│   │   └── pipeline_runner.py      # 流水线编排 + 进度推送
│   └── templates/
│       └── index.html              # 单页 Web UI
│
├── voices/
│   └── voices.json                 # 音色库定义
│
├── uploads/                        # 上传的原始视频
└── output/                         # 每个任务的完整产出
    └── {task_id}/
        ├── asr_result.json         # ① ASR 识别原文 + 时间戳
        ├── translate_result.json   # ② Claude 翻译结果
        ├── translate_confirmed.json# ③ 用户确认后最终译文
        ├── tts_result.json         # ④ TTS 每段时长信息
        ├── tts_segments/           #    TTS 分段音频文件
        ├── tts_full.mp3            # ⑤ 拼接后完整英文音频
        ├── subtitle.srt            # ⑥ 字幕文件
        ├── *_soft.mp4              # ⑦ 软字幕版视频
        └── *_hard.mp4              # ⑧ 硬字幕版视频
```

---

## 3. 核心模块设计

### 3.1 ASR 语音识别

**接口**: 豆包大模型录音文件识别 v3
- 提交: `POST /api/v3/auc/bigmodel/submit`
- 查询: `POST /api/v3/auc/bigmodel/query`
- 认证: `x-api-key` header
- 资源: `volc.seedasr.auc` (模型 2.0)
- 音频传输: TOS 预签名 URL (解决私有 bucket 权限)

**流程**: 本地 WAV → TOS 上传 → 提交任务 → 轮询(3s间隔) → 解析 utterances → 删除 TOS 临时文件

**输出**: `[{text, start_time, end_time}, ...]` (毫秒转秒)

### 3.2 翻译本土化

**接口**: Claude via OpenRouter (`anthropic/claude-sonnet-4-5`)

**Prompt 核心要求**:
- 完全原生感：由美国创作者撰写，不是翻译
- TikTok 卖货广告场景，用于广告系统投放
- 美式口语：缩写、直接称呼、简单词汇(8年级阅读水平)
- 保持原文句数和节奏（音视频同步需要）
- 每段译文长度与原文成比例（短句不拉长，长句不缩短）
- 根据原视频内容动态调整风格（不刻意用网络用语）
- Scroll-stopping hook、社交证明、紧迫感自然融入

**输出格式**: JSON 数组，每段对应原文 index

### 3.3 TTS 语音生成

**SDK**: ElevenLabs Python SDK
**模型**: `eleven_turbo_v2_5` (性价比最优)
**音色策略**:
- 第一版: 固定男声(Adam) / 女声(Rachel) 二选一，默认男声
- 音色库 (`voices/voices.json`): 记录 voice_id、性别、风格标签、适用场景描述
- 后续: 建立音色库管理 UI，根据视频内容+标签动态匹配

**时间对齐策略**: 音频保持原速不做任何拉伸压缩，视频画面根据分镜段落裁剪来匹配 TTS 时长，尾部段落可舍弃。

### 3.4 字幕规则

| 规则 | 具体要求 |
|------|---------|
| 行数 | 最多 2 行 |
| 每行字符 | ≤ 42 字符 (TikTok 竖屏适配) |
| 断词 | 不截断单词，在词间断行 |
| 行均衡 | 两行尽量等长，较长行放第一行 |
| 大小写 | 首字母大写 |
| 超长处理 | 超出两行容量的文本，顺序填满两行，截断 |
| 位置 | 可配置 (底部/中部/顶部)，默认底部，人为可调 |
| 样式 | 白字黑边 (Arial Bold, Outline=2) |

### 3.5 音色库

```json
{
  "voices": [
    {
      "id": "male_default",
      "name": "Adam",
      "gender": "male",
      "elevenlabs_voice_id": "pNInz6obpgDQGcFmaJgB",
      "description": "美式男声，自然有力，适合卖货展示类视频",
      "style_tags": ["energetic", "trustworthy", "casual"],
      "is_default_male": true
    },
    {
      "id": "female_default",
      "name": "Rachel",
      "gender": "female",
      "elevenlabs_voice_id": "21m00Tcm4TlvDq8ikWAM",
      "description": "美式女声，亲切自然，适合美妆护肤生活类视频",
      "style_tags": ["warm", "friendly", "expressive"],
      "is_default_female": true
    }
  ]
}
```

---

## 4. Web UI 设计

### 4.1 单页流程

```
┌─────────────────────────────────────┐
│  上传区域 (拖拽/点击上传)            │
├─────────────────────────────────────┤
│  配置面板                            │
│    音色: [男声▾]  字幕位置: [底部▾]   │
│    [开始处理]                        │
├─────────────────────────────────────┤
│  处理进度 (6步, 实时WebSocket推送)    │
│    ① 音频提取      ✅               │
│    ② 语音识别      ✅               │
│    ③ 翻译本土化    ⏳ 等待确认       │
│    ④ 语音生成      ○ 等待中         │
│    ⑤ 字幕生成      ○ 等待中         │
│    ⑥ 视频合成      ○ 等待中         │
├─────────────────────────────────────┤
│  翻译确认面板 (可逐句编辑)           │
│    [0:00-2:50] 中文原文              │
│    [翻译结果 textarea 可编辑]        │
│    ...                              │
│    [确认并继续]                      │
├─────────────────────────────────────┤
│  成品下载                            │
│    [📥 硬字幕版] [📥 软字幕版] [📄 SRT] │
└─────────────────────────────────────┘
```

### 4.2 API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 上传视频，创建任务 |
| GET | `/api/tasks/{id}` | 查询任务状态 |
| POST | `/api/tasks/{id}/start` | 启动流水线 |
| PUT | `/api/tasks/{id}/segments` | 确认/编辑翻译结果 |
| GET | `/api/tasks/{id}/download/{type}` | 下载成品 (soft/hard/srt) |
| GET | `/api/voices` | 查询音色列表 |

### 4.3 WebSocket 事件

| 事件 | 方向 | 数据 |
|------|------|------|
| `join_task` | 客户端→服务端 | `{task_id}` |
| `step_update` | 服务端→客户端 | `{step, status, message}` |
| `asr_result` | 服务端→客户端 | `{segments}` |
| `translate_result` | 服务端→客户端 | `{segments}` |
| `subtitle_preview` | 服务端→客户端 | `{srt}` |
| `pipeline_done` | 服务端→客户端 | `{task_id, downloads}` |
| `pipeline_error` | 服务端→客户端 | `{error}` |

---

## 5. 开发路线图

### Phase 0: MVP ✅ 已完成

**目标**: 跑通核心主干流程

- [x] 项目骨架搭建 (Flask 分层架构)
- [x] 音频提取 (ffmpeg)
- [x] ASR (豆包 v3, TOS 预签名 URL)
- [x] 翻译 (Claude via OpenRouter, 卖货 prompt)
- [x] TTS (ElevenLabs SDK, 男/女声)
- [x] 字幕生成 (首字母大写, 智能断行)
- [x] 视频合成 (软字幕 + 硬字幕)
- [x] Web UI (上传/配置/进度/翻译确认/下载)
- [x] 中间结果 JSON 持久化

**已解决关键问题**:
| 问题 | 解决方案 |
|------|---------|
| 豆包 v3 认证格式 | `x-api-key` header |
| TOS 私有 bucket 访问 | 预签名 URL (`HttpMethodType.Http_Method_Get`) |
| eventlet + Python 3.14 不兼容 | `async_mode` 改为 `threading` |
| Flask send_file 相对路径 500 | `os.path.abspath()` |
| ElevenLabs 配额 API key | 更新 key 并调整配额上限 |

### Phase 1: 分镜检测 + 音视频精准对齐

**目标**: 视频按分镜裁剪匹配 TTS 时长，尾部可截断

- [ ] 集成 PySceneDetect 检测镜头切换点
- [ ] ASR 语义分段 + 镜头切换联合确认分段边界
- [ ] Web UI 分段结果预览 + 人工手动调整
- [ ] 视频按段裁剪，每段匹配对应 TTS 音频时长
- [ ] 尾部多余段落舍弃对齐

### Phase 2: 剪映专业版项目文件输出

**目标**: 输出剪映可直接打开的项目，所有轨道配好

- [ ] 逆向分析剪映专业版 `draft_content.json` 格式
- [ ] 生成视频轨道 (分段视频按时间排列)
- [ ] 生成音频轨道 (TTS 分段音频按时间排列)
- [ ] 生成字幕轨道 (文本 + 时间戳 + 位置)
- [ ] 字幕位置参数可配置 (流程中动态设置, 默认通用位置)
- [ ] 字体信息预留 (用户在剪映里选会员字体)

### Phase 3: 音色库管理 + 智能匹配

**目标**: 音色可管理，新视频自动推荐匹配

- [ ] 音色库管理 Web UI (增删改音色, 说明/标签)
- [ ] Claude 分析 ASR 原文内容 → 推荐最佳匹配音色
- [ ] 结合视频内容类型 + 音色风格标签做匹配

---

## 6. 关键设计决策

| # | 决策 | 选择 | 原因 | 确认时间 |
|---|------|------|------|---------|
| 1 | 视频来源 | 本地上传预处理好的视频 | 用户手动下载并预处理 | 需求确认 |
| 2 | TTS 服务 | ElevenLabs (Pro 会员) | 自然度最高 | 需求确认 |
| 3 | ASR 服务 | 豆包大模型 v3 | 中文识别好, 火山生态 | 需求确认 |
| 4 | 翻译引擎 | Claude via OpenRouter | 本土化文案能力强 | 需求确认 |
| 5 | 翻译风格 | 根据内容动态调整, 整体卖货+广告导向 | TikTok 广告投放场景 | 需求确认 |
| 6 | 音频对齐 | 音频保持原速, 视频画面裁剪匹配 | 音质最自然 | 需求确认 |
| 7 | 尾部处理 | 可舍弃尾部段落来和音频拟合 | 用户确认可接受 | 需求确认 |
| 8 | 字幕输出 | 软字幕 + 硬字幕双版本 | 兼顾灵活性和即用性 | 需求确认 |
| 9 | 最终合成 | 剪映专业版项目文件 | 用户有会员字体需求 | 需求确认 |
| 10 | 音色方案 | 第一版男/女二选一, 后续音色库+智能匹配 | 渐进迭代 | 需求确认 |
| 11 | 分镜检测 | PySceneDetect + ASR 语义联合 | 画面+语义双重确认 | 需求确认 |
| 12 | 界面形态 | Web UI, 流程进度可管理可调整 | 非命令行 | 需求确认 |

---

## 7. 外部依赖和凭证

| 服务 | 用途 | 凭证 | 配置位置 |
|------|------|------|---------|
| 火山引擎 TOS | 音频/素材对象存储 | `TOS_ACCESS_KEY` + `TOS_SECRET_KEY` | `.env` / `config.py` |
| 火山引擎 VOD | 视频点播/字幕擦除上传 | `VOD_ACCESS_KEY` + `VOD_SECRET_KEY` | `.env` / `config.py` |
| 豆包 ASR / LLM / Seedream / Seedance | 语音识别、文本、图片、视频模型 | `api_key` / `base_url` / `model_id` | `llm_provider_configs`，admin 在 `/settings` 配置 |
| OpenRouter / Gemini / APIMART | 文本/图片模型 | `api_key` / `base_url` / `model_id` | `llm_provider_configs`，admin 在 `/settings` 配置 |
| ElevenLabs | TTS / 共享音色库 | `api_key` / `base_url` | `llm_provider_configs.elevenlabs_tts` |
| OpenAPI / 字幕移除备用通道 | 外部素材接口、第三方字幕擦除 | `api_key` / `base_url` / `extra_config` | `llm_provider_configs` |

**TOS Bucket**: `auto-video-srt` (上海区域)
**TOS 用途**: 临时存放 ASR 音频文件，识别完成后自动删除
