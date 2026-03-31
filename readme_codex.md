# AutoVideoSrt Codex 协作说明

## 启动与验证

- 安装依赖：`pip install -r requirements.txt`
- 启动应用：`python main.py`
- 运行测试：`pytest tests -q`
- 首次启动前先复制 `.env.example` 为 `.env`，并填入你自己的服务凭证。
- 剪映项目导出优先使用 `pyJianYingDraft`；如果当前环境不可用，再回退到模板 scaffold。

## 运行时环境变量

- `VOLC_API_KEY` / `VOLC_RESOURCE_ID`：豆包 ASR
- `TOS_ACCESS_KEY` / `TOS_SECRET_KEY` / `TOS_BUCKET`：火山引擎 TOS
- `OPENROUTER_API_KEY` / `CLAUDE_MODEL`：Claude 翻译
- `ELEVENLABS_API_KEY`：TTS
- `OUTPUT_DIR` / `UPLOAD_DIR`：任务输出和上传目录
- `VOICES_FILE`：音色库 JSON 文件
- `CAPCUT_TEMPLATE_DIR`：CapCut 模板目录

## 目录职责

- `pipeline/`：核心流水线模块
- `web/`：Flask 路由、内存状态、Web UI
- `voices/voices.json`：音色库主数据
- `output/{task_id}/`：任务中间产物和最终结果
- `capcut_example/`：CapCut 工程模板样本

## 当前任务数据结构

- `utterances`：ASR 原始识别结果，保留 `words` 字级时间
- `scene_cuts`：镜头切换时间点
- `alignment.break_after`：utterance 之间的断段布尔数组
- `script_segments`：用户确认后的可翻译段落
- `timeline_manifest.json`：TTS 时长、视频裁剪区间、最终时间线的唯一真相源
- `exports.capcut_archive`：CapCut 导出压缩包路径

## 关键产物

- `asr_result.json`
- `scene_cuts.json`
- `alignment_draft.json`
- `translate_result.json`
- `translate_confirmed.json`
- `tts_result.json`
- `timeline_manifest.json`
- `subtitle.srt`
- `*_soft.mp4`
- `*_hard.mp4`
- `capcut_project/`

## 已知实现约束

- 现在的 CapCut / 剪映导出优先走 `pyJianYingDraft` 真草稿生成，并附带 `codex_export_manifest.json` 记录实际 backend。
- `draft_content.json` / `draft_meta_info.json` 仍然是模板原始格式，不要手工改写为占位符文本。
- `timeline_manifest.json` 是 Phase 1 和 Phase 2 的统一中间层；字幕、视频和导出都应优先读取它。
- 如果仓库仍处于“大量未跟踪文件”的状态，不要直接创建新的 git worktree，否则会丢失当前项目文件。

## 协作规则

- 不要把真实 key 写回配置默认值。
- 新增行为优先先写测试，再写实现。
- 手动确认流程必须保持可用：先分段确认，再翻译确认，再继续后续生成。
- 如果要继续增强 CapCut 导出，优先补测试和可读的 sidecar manifest，而不是直接改模板二进制内容。
