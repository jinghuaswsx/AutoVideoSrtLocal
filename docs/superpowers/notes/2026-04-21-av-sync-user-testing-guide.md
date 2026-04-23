# 视频翻译音画同步（v2）用户实测操作手册

适用分支：`feature/video-translate-av-sync`

当前交付状态：可实测第一版。当前 feature worktree 只有 `.env.example`，没有实际 `.env`，因此本次提交未执行端到端实机冒烟；开始实测前，使用者需要先补齐本地环境变量。

## 1. 启动方式

以仓库根目录为当前目录，按项目现有启动方式执行：

```bash
pip install -r requirements.txt
copy .env.example .env
python db/migrate.py
python db/create_admin.py
python main.py
```

本地默认访问地址：

```text
http://127.0.0.1:5000
```

如果你跑的是现有部署方式，也可以使用文档里已有的 gunicorn 命令：

```bash
gunicorn -w 1 -k eventlet --bind 0.0.0.0:80 --timeout 300 main:app
```

## 2. `.env` 必填项

先复制 `.env.example` 为 `.env`，再按下面分组补齐。若你已经有团队统一环境，保持与现网一致即可。

### 2.1 Web 与数据库

| 变量 | 用途 |
| --- | --- |
| `FLASK_SECRET_KEY` | Flask 登录态与会话签名 |
| `DB_HOST` | MySQL 主机 |
| `DB_PORT` | MySQL 端口 |
| `DB_NAME` | 数据库名 |
| `DB_USER` | 数据库账号 |
| `DB_PASSWORD` | 数据库密码 |

### 2.2 v2 音画同步必需外部服务

| 变量 | 用途 |
| --- | --- |
| `OPENROUTER_API_KEY` | v2 文案本地化 / 重写调用 |
| `ELEVENLABS_API_KEY` | v2 TTS 配音 |
| `VOLC_API_KEY` | 字幕、配音或既有火山侧能力依赖 |
| `TOS_ACCESS_KEY` | TOS 上传下载 |
| `TOS_SECRET_KEY` | TOS 上传下载 |
| `TOS_REGION` | TOS 区域 |
| `TOS_BUCKET` | 项目产物桶 |
| `TOS_MEDIA_BUCKET` | 素材上传桶 |
| `TOS_ENDPOINT` | TOS Endpoint |

### 2.3 Gemini 配置

v2 实测时建议一并配置 Gemini，避免后续画面分析或其他链路缺参。

| 场景 | 变量 |
| --- | --- |
| AI Studio | `GEMINI_BACKEND=aistudio`，并配置 `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` |
| Cloud | `GEMINI_BACKEND=cloud`，并配置 `GEMINI_CLOUD_API_KEY`、`GEMINI_CLOUD_PROJECT`、`GEMINI_CLOUD_LOCATION` |

说明：如果团队沿用仓库根目录 `google_api_key` 文件，也可以由该文件回填 Gemini key，但手工实测时仍建议优先把环境变量配齐。

### 2.4 可选项

| 变量 | 用途 |
| --- | --- |
| `OUTPUT_DIR` | 自定义任务产物目录，默认可不改 |
| `UPLOAD_DIR` | 自定义上传缓存目录，默认可不改 |
| `AV_LOCALIZE_FALLBACK=1` | 回滚到 v1 本地化路径 |

## 3. 从 UI 创建一个 v2 视频翻译任务

### 3.1 进入任务创建页

1. 启动服务并登录。
2. 进入项目首页 `/`。
3. 点击右上角 `+ 新建项目`。
4. 页面实际路由会进入 ` /api/tasks/upload-page `，这是当前视频翻译任务创建页。

### 3.2 上传视频

1. 在创建页上传一个本地视频文件。
2. 当前实现会先走 TOS 直传。
3. 上传完成后，页面会自动跳转到任务详情页 `/projects/<task_id>`。

### 3.3 配置 v2 音画同步参数

进入任务详情页后，沿用现有任务页配置，再额外关注新增的 `音画同步配置` 卡片：

1. 在 `目标语种` 里选择目标语言。
   建议第一次直接选 `English`。
2. 在 `目标市场` 里选择目标市场。
   建议第一次直接选 `US`。
3. 如画面里商品信息不完整，可展开 `带货资料微调`，按需填写：
   - `产品名`
   - `品牌`
   - `卖点`
   - `价格`
   - `目标人群`
   - `补充信息`
4. 其他原有配置如音色、字幕样式、确认模式，按现有流程正常填写即可。
5. 点击页面底部 `开始处理`。

## 4. 建议的第一轮测试方式

为了先验证链路是否通，建议第一轮按下面参数跑：

| 项目 | 建议值 |
| --- | --- |
| 视频长度 | `15-30s` |
| 源语言 | 中文 |
| 目标语言 | 英文 |
| 目标市场 | `US` |
| 素材类型 | 单人讲解、口播清晰、商品信息明确 |

不建议第一轮就用：

- 超过 60 秒的视频
- 多人轮流说话的视频
- 背景音乐过大、语音识别困难的视频
- 商品信息严重缺失、必须靠人工补全的视频

## 5. 跑完后去哪里看产物

### 5.1 页面内直接查看

任务详情页会直接展示 v2 关键结果：

- `画面笔记预览`
- `时长警告列表`
- `手动重写译文`

如果时长告警里有超时或偏短句子，可以直接在详情页触发重写并重新生成该句配音。

### 5.2 文件系统内查看

任务产物目录默认在：

```text
OUTPUT_DIR/<task_id>/
```

v2 首轮重点看这些文件：

| 文件 | 说明 |
| --- | --- |
| `shot_notes.json` | 画面笔记原始 JSON |
| `localized_translation.av.json` | v2 音画同步译文 |
| `tts_full.av.mp3` | v2 全量配音 |
| `subtitle.av.srt` | v2 字幕文件 |
| `tts_segments/av/` | 分句音频与重写后的片段 |

如果页面里能看到预览，但你想做更细的排查，优先直接打开 `shot_notes.json` 和 `localized_translation.av.json`。

## 6. 当前 worktree 的端到端冒烟状态

本次提交未执行端到端实机冒烟，原因如下：

- 当前 worktree 中不存在 `.env`
- 仅存在 `.env.example`
- 按交付规则，缺少 `.env` 时跳过冒烟，不伪造运行结果

因此，实际使用者开始手测前，需要先自行补齐 `.env`。

## 7. 出错时去哪里查日志

### 7.1 本地开发方式

如果你用的是：

```bash
python main.py
```

日志会直接输出在当前终端窗口，因为 `main.py` 启动时已经把 logging 配到了标准错误输出。

### 7.2 gunicorn / 部署方式

如果你用的是：

```bash
gunicorn -w 1 -k eventlet --bind 0.0.0.0:80 --timeout 300 main:app
```

优先看 gunicorn 进程的标准输出和标准错误；如果外面还包了 systemd、supervisor 或容器，再去对应进程日志里查。

## 8. 常见错误排查

| 现象 | 优先检查 |
| --- | --- |
| 启动时报“先复制 `.env.example` 到 `.env`” | `.env` 是否创建、是否放在仓库根目录 |
| 页面上传失败 / 创建任务失败 | `TOS_ACCESS_KEY`、`TOS_SECRET_KEY`、`TOS_REGION`、`TOS_BUCKET`、`TOS_MEDIA_BUCKET`、`TOS_ENDPOINT` |
| 登录后页面空白或 500 | `FLASK_SECRET_KEY`、数据库连接、迁移是否执行 |
| 任务进入翻译或重写时报模型错误 | `OPENROUTER_API_KEY`、Gemini 配置是否有效 |
| 配音步骤失败 | `ELEVENLABS_API_KEY`、音色是否存在、网络是否可访问 ElevenLabs |
| 老链路能力报错 | `VOLC_API_KEY` 是否缺失 |
| 任务卡在处理中 | 先看运行终端日志，再看对应 `OUTPUT_DIR/<task_id>/` 里已落盘到哪一步 |

补充建议：

- 第一次实测尽量用短视频，避免把“环境没配好”和“时长闭环没跑顺”混在一起。
- 如果只想先确认页面和任务状态流转是否正常，可以先用最保守的中文转英文短视频。

## 9. 如何回滚到 v1

如果 v2 实测过程中需要临时回退：

1. 在 `.env` 里设置：

```bash
AV_LOCALIZE_FALLBACK=1
```

2. 重启 Web 服务。
3. 之后新任务的本地化阶段会回到 v1 路径，保留老管线行为。

建议只把它当作临时回滚开关使用。回到 v2 时，把该变量删掉或改回 `0`，再重启服务即可。
