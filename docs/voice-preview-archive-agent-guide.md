# Voice Preview Archive Agent Guide

最后更新：2026-05-20

## 用途

声音仓库的 ElevenLabs `preview_url` 已经落成本地归档。归档内容包含：

- 预览音频本地文件
- 音频时长 `duration_seconds`
- ASR 整段文案 `transcript_text`
- ASR 分段/词级结果 `utterances_json`
- 当前远端预览链接 hash `preview_url_hash`

其他 Codex agent 处理声音预览、TTS 音色选择、语速分析、素材审核时，应优先使用这套归档，缺失时再回退到远端 `preview_url`。

## 关键位置

- 数据表：`voice_preview_archives`
- 服务模块：`appcore/voice_preview_archive.py`
- 回填脚本：`scripts/archive_voice_previews.py`
- 本地音频根目录：`<UPLOAD_DIR>/voice_preview_archive/<language>/`
- 登录保护播放接口：`/voice-library/api/preview/<language>/<voice_id>?hash=<preview_url_hash>`

生产环境默认路径：

```text
/opt/autovideosrt/uploads/voice_preview_archive/<language>/*.mp3
```

## 数据表字段

`voice_preview_archives` 按 `(voice_id, language, preview_url_hash)` 唯一定位一条归档。

常用字段：

- `voice_id`：ElevenLabs 音色 ID
- `language`：归档语言，如 `en`、`de`、`fr`
- `preview_url_hash`：当前远端 `preview_url` 的 SHA-256
- `preview_url`：原始远端预览地址
- `local_path`：服务器上的本地音频绝对路径
- `duration_seconds`：预览音频时长，单位秒
- `transcript_text`：ASR 文案，适合列表/审核展示
- `utterances_json`：ASR 原始分段结果，适合二次分析
- `asr_source`：`preview_asr:doubao_asr` 或 `preview_asr:elevenlabs_scribe`
- `status`：`ready` 可用，`failed` 表示下载或 ASR 失败
- `error`：失败原因

不要只按 `voice_id + language` 取归档；必须带 `preview_url_hash`，因为远端预览音频可能会变化。

## 后端使用方式

获取声音列表时，优先走已有服务：

```python
from appcore.voice_library_browse import list_voices

payload = list_voices(language="de", page=1, page_size=30)
```

返回的每个 item 可能包含：

- `preview_local_url`：本地播放接口，存在时优先用
- `preview_duration_seconds`：归档音频时长
- `preview_transcript_text`：归档 ASR 文案
- `preview_url`：远端兜底地址
- `preview_url_hash`：当前 preview URL hash

如果你手上已经有 voice item 列表，可补齐本地归档字段：

```python
from appcore.voice_preview_archive import attach_local_preview_urls

items = attach_local_preview_urls(items, language="de")
```

需要直接拿本地文件路径时：

```python
from appcore.voice_preview_archive import resolve_local_preview_path

path = resolve_local_preview_path(
    language="de",
    voice_id="voice_x",
    preview_url_hash="...",
)
```

返回 `None` 表示没有可用本地文件，应回退远端 `preview_url` 或跳过。

## 前端使用方式

播放音色预览时使用：

```js
const preview = safeMediaSrc(v.preview_local_url || v.preview_url || "");
```

不要直接拼 `local_path` 给浏览器。浏览器只能使用 `preview_local_url`，这个路由会校验登录态和文件路径安全。

已接入本地优先播放的页面/组件：

- 声音仓库 `web/static/voice_library.js`
- 翻译模块共享 TTS 音色选择 `web/static/voice_selector_multi.js`
- Translate Lab 音色预览 `web/static/translate_lab.js`

## 全量回填

服务器上执行：

```bash
cd /opt/autovideosrt
python scripts/archive_voice_previews.py
```

只看缺失数量：

```bash
python scripts/archive_voice_previews.py --dry-run
```

按语言回填：

```bash
python scripts/archive_voice_previews.py --language de
```

低并发全量回填：

```bash
python scripts/archive_voice_previews.py --workers 2
```

脚本只处理当前 preview URL hash 缺失 ready 归档的记录。远端 `preview_url` 改变后会自动生成新的 hash，并重新归档。

## Agent 注意事项

- 不要连接 Windows 本机 MySQL，也不要用本机 MySQL 验证归档数据。
- 数据确认以服务器环境为准，生产目录是 `/opt/autovideosrt`。
- 不要把 `local_path` 暴露给前端；前端只用 `preview_local_url`。
- 不要把 preview ASR 写进 `voice_speech_rate`；语速先验继续写 `voice_preview_speech_rate`。
- 如果 `status='failed'`，可以查看 `error`，修复后重新跑回填脚本。
- 预览音频只是试听素材，不等同于最终生成的 TTS 音频。
