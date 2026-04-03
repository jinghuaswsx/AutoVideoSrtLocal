# AutoVideoSrt

抖音卖货视频 → TikTok 英文广告视频，一键本土化。

自动完成语音识别、翻译、配音、字幕、剪映工程导出的全流程。

## 功能流程

```
上传视频 → 音频提取 → ASR 识别 → 镜头+语义分段 → [人工确认]
→ Claude 翻译 → [人工确认] → ElevenLabs TTS → 音视频拟合
→ 字幕格式化 → 输出剪映项目 / SRT / 硬字幕视频
```

## 技术栈

| 模块 | 方案 |
|------|------|
| ASR | 豆包大模型 v3 (火山引擎) |
| 翻译 | Claude via OpenRouter |
| TTS | ElevenLabs |
| 存储 | 火山引擎 TOS |
| 分镜检测 | PySceneDetect |
| 视频处理 | ffmpeg |
| 剪映导出 | pyJianYingDraft |
| Web | Flask + SocketIO |
| 数据库 | MySQL |

## 快速开始

### 环境要求

- Python 3.10+
- ffmpeg (需在 PATH 中)
- MySQL

### 安装

```bash
pip install -r requirements.txt
cp .env.example .env  # 填入服务凭证
```

### 环境变量

```env
# 豆包 ASR
VOLC_API_KEY=
VOLC_RESOURCE_ID=volc.seedasr.auc

# 火山引擎 TOS
TOS_ACCESS_KEY=
TOS_SECRET_KEY=
TOS_BUCKET=auto-video-srt

# Claude 翻译 (OpenRouter)
OPENROUTER_API_KEY=
CLAUDE_MODEL=anthropic/claude-sonnet-4-5

# ElevenLabs TTS
ELEVENLABS_API_KEY=

# MySQL
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=auto_video
DB_USER=root
DB_PASSWORD=

# 可选
OUTPUT_DIR=output
UPLOAD_DIR=uploads
JIANYING_PROJECT_DIR=          # 留空自动探测
```

### 启动

```bash
python main.py
# 访问 http://localhost:5000
```

### 测试

```bash
pytest tests -q
```

## 项目结构

```
├── main.py                  # 启动入口
├── config.py                # 配置
├── pipeline/                # 核心流水线
│   ├── extract.py           # 音频提取
│   ├── asr.py               # 语音识别
│   ├── storage.py           # TOS 存储
│   ├── translate.py         # Claude 翻译
│   ├── tts.py               # ElevenLabs TTS
│   ├── subtitle.py          # 字幕格式化
│   └── compose.py           # 视频合成
├── web/                     # Web 层
│   ├── app.py               # Flask 工厂
│   ├── extensions.py        # SocketIO 单例
│   ├── store.py             # 任务状态存储
│   ├── routes/              # API 路由
│   ├── services/            # 流水线编排
│   └── templates/           # Web UI
├── voices/voices.json       # 音色库
├── output/{task_id}/        # 任务产出
└── uploads/                 # 上传视频
```

## 部署

```bash
# systemctl 管理
sudo systemctl restart autovideosrt
```

## License

Private
