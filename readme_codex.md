# AutoVideoSrt Codex 协作说明

## 启动与验证

- 安装依赖：`pip install -r requirements.txt`
- 启动应用：`python main.py`
- 运行测试：`pytest tests -q`
- 首次启动前先复制 `.env.example` 为 `.env`，并填入服务凭证。
- 剪映工程导出优先使用 `pyJianYingDraft`；如果运行环境不可用，再回退到模板 scaffold。
- 导出阶段默认只在任务输出目录生成草稿；复制到本机剪映目录改为页面按钮手动触发。

## 运行时环境变量

- `VOLC_API_KEY` / `VOLC_RESOURCE_ID`：豆包 ASR
- `TOS_ACCESS_KEY` / `TOS_SECRET_KEY` / `TOS_BUCKET`：火山引擎 TOS
- `OPENROUTER_API_KEY` / `CLAUDE_MODEL`：Claude 翻译与文案编排
- `ELEVENLABS_API_KEY`：ElevenLabs TTS
- `OUTPUT_DIR` / `UPLOAD_DIR`：任务输出和上传目录
- `VOICES_FILE`：音色库 JSON 文件
- `CAPCUT_TEMPLATE_DIR`：CapCut 模板目录
- `JIANYING_PROJECT_DIR`：可选，剪映草稿目录；为空时会在 Windows 下自动探测默认 `com.lveditor.draft`

## 目录职责

- `pipeline/`：核心流水线模块
- `web/`：Flask 路由、任务状态、Web UI
- `voices/voices.json`：音色库主数据
- `output/{task_id}/`：任务中间产物和最终结果
- `capcut_example/`：CapCut 工程模板样本

## 当前任务数据结构

- `utterances`：ASR 原始识别结果，保留 `words` 级时间
- `scene_cuts`：镜头切换时间点
- `alignment.break_after`：utterance 之间的断段布尔数组
- `script_segments`：确认后的中文语义段
- `source_full_text_zh`：整段中文拼接文本
- `localized_translation`：兼容字段，默认映射 `variants.normal.localized_translation`
- `tts_script`：兼容字段，默认映射 `variants.normal.tts_script`
- `timeline_manifest`：兼容字段，默认映射 `variants.normal.timeline_manifest`
- `exports.capcut_archive`：兼容字段，默认映射 `variants.normal.exports.capcut_archive`

## Variant Outputs

- `variants.normal`：基线英文版本，不强加黄金 3 秒 hook 和 CTA 约束
- `variants.hook_cta`：实验英文版本，要求首句承担 TikTok US 前 3 秒 hook，并且全文包含 1 次自然 CTA
- 中文上游继续共用：
- `utterances`
- `scene_cuts`
- `alignment`
- `script_segments`
- `source_full_text_zh`
- 从 `localized_translation` 开始分叉，以下产物都按 variant 独立输出：
- `localized_translation`
- `tts_script`
- `tts_result`
- `english_asr_result`
- `corrected_subtitle`
- `timeline_manifest`
- `result`
- `exports`
- 典型文件命名：
- `localized_translation.normal.json`
- `localized_translation.hook_cta.json`
- `tts_script.normal.json`
- `tts_script.hook_cta.json`
- `tts_result.normal.json`
- `tts_result.hook_cta.json`
- `timeline_manifest.normal.json`
- `timeline_manifest.hook_cta.json`
- `subtitle.normal.srt`
- `subtitle.hook_cta.srt`
- `*_soft.normal.mp4`
- `*_soft.hook_cta.mp4`
- `*_hard.normal.mp4`
- `*_hard.hook_cta.mp4`

## 关键产物

- `asr_result.json`
- `scene_cuts.json`
- `alignment_draft.json`
- `localized_translation.normal.json`
- `localized_translation.hook_cta.json`
- `tts_script.normal.json`
- `tts_script.hook_cta.json`
- `english_asr_result.normal.json`
- `english_asr_result.hook_cta.json`
- `corrected_subtitle.normal.json`
- `corrected_subtitle.hook_cta.json`
- `subtitle.normal.srt`
- `subtitle.hook_cta.srt`
- `timeline_manifest.normal.json`
- `timeline_manifest.hook_cta.json`
- `*_soft.normal.mp4`
- `*_soft.hook_cta.mp4`
- `*_hard.normal.mp4`
- `*_hard.hook_cta.mp4`
- `capcut_normal/`
- `capcut_hook_cta/`

## 已知实现约束

- CapCut / 剪映导出优先走 `pyJianYingDraft` 真草稿生成，并附带 `codex_export_manifest.json` 记录实际 backend
- `timeline_manifest` 仍然是字幕、视频和导出的统一时间线真相源
- 兼容字段继续保留给现有页面和下载入口使用，但新增逻辑优先读 `variants.*`
- 不要把真实 key 写回配置默认值

## 协作规则

- 新增行为优先先写测试，再写实现
- 默认流程优先自动继续，人工确认作为可选能力保留
- 如需继续增强 CapCut 导出，优先补测试和 sidecar manifest，而不是直接改模板二进制内容
