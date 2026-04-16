# 声音仓库（Voice Library）设计

**日期**：2026-04-17
**状态**：设计，待实现
**触达模块**：`web/`（新增 blueprint + 页面）、`appcore/`（新增 service）、`admin_settings.html`（追加同步区块）

---

## 1. 目标与非目标

### 目标

1. 新增独立菜单"**声音仓库**"，让用户一站式**浏览、筛选、试听**系统里所有已同步的 ElevenLabs 共享音色。
2. 提供"**视频匹配**"能力：上传一个视频，后端从用户选定的小语种音色库里按声纹相似度推荐 Top-3 候选，用户试听自行取舍。
3. 管理员可在后台**按语种触发 ElevenLabs 共享音色同步**（含 embedding 回写），完全摆脱命令行。
4. 在"匹配到底能做到多像"这个问题上 **不过度承诺**：只交付"粗特征相似"级别（性别 / 年龄段 / 嗓音粗特征接近），不是声音克隆。

### 非目标

- 不做声音克隆、语音转换（Voice Conversion）、跨语种 TTS 风格迁移。
- 不保留匹配历史（当前任务结束即作废，Top-3 用户关页面就没了）。
- 不做"一键把匹配结果套到某个翻译项目"的闭环（后续集成各业务模块时再做）。
- 不触碰用户级个人音色（`user_voices`）的现有 CRUD 入口。

---

## 2. 名词与数据源

| 名词 | 含义 | 数据来源 |
|---|---|---|
| 启用小语种 | 系统级配置的目标语言集合 | `media_languages` 表 `enabled=1` 的行 |
| 全局声音库 | ElevenLabs 共享音色的本地镜像（含 embedding） | `elevenlabs_voices` 表 |
| 个人音色 | 用户翻译任务里用的收藏音色（本次不涉及） | `user_voices` 表 |
| 声纹向量 | resemblyzer 256 维 speaker embedding | `elevenlabs_voices.audio_embedding` BLOB |

"启用的 6 个小语种"= `media_languages.enabled=1` 当前集合，本方案不写死语种清单，后台加/停语种，声音仓库自动跟随。

---

## 3. 总架构

```
Browser
  └── /voice-library (layout.html 新菜单项)
        ├── #browse      ── GET  /voice-library/api/filters
        │                    GET  /voice-library/api/list
        │
        └── #match       ── POST /voice-library/api/match/upload-url
                             POST /voice-library/api/match/start
                             GET  /voice-library/api/match/status/<task_id>

Browser (admin only)
  └── /admin/settings (admin_settings.html 新区块 "声音库同步")
        POST /admin/voice-library/sync/<language>
        GET  /admin/voice-library/sync-status
        SocketIO: voice_library.sync.progress

Backend
  web/routes/voice_library.py (新 blueprint)
  web/routes/admin.py       (追加同步接口)
  appcore/voice_library_browse.py  (查询/筛选 service)
  appcore/voice_match_tasks.py     (匹配任务内存管理器)
  pipeline/voice_match.py          (复用)
  pipeline/voice_embedding.py      (复用)
  pipeline/voice_library_sync.py   (复用)
  pipeline/elevenlabs_voices.py    (复用)
  appcore/tos_clients.py           (复用)

DB
  elevenlabs_voices (全局，已存在，新增一条复合索引)
  media_languages   (读，已存在)
```

---

## 4. 前端

### 4.1 菜单与路由

- `web/templates/layout.html` 顶部导航新增一项 **"声音仓库"**，位置在"文案翻译 / 图片翻译"附近，`href="/voice-library"`。所有登录用户可见。
- 路由 `GET /voice-library` → `voice_library.html`。
- Tab 切换用 URL hash：`#browse`（默认）、`#match`。进入页面时读 `location.hash` 切 Tab；点击 Tab 时用 `history.replaceState` 写回 hash。

### 4.2 Tab 1 · 浏览试听

**布局**：左筛选抽屉（`width: 260px`）+ 右卡片网格。

**筛选器（7 项，从上到下）**：

1. **语言**（必选，pill 列表，来自 `/api/filters`，默认选第一个）
2. **性别**（全部 / 男 / 女，pill）
3. **用途 `use_case`**（下拉多选，来自 `labels.use_case` 唯一值）
4. **口音 `accent`**（下拉多选）
5. **年龄 `age`**（下拉多选）
6. **音色描述 `descriptive`**（下拉多选）
7. **搜索框**（输入后按回车/防抖 300ms，模糊匹配 `name` 和 `description`）

筛选面板底部："**重置筛选**"按钮。

**卡片网格**：CSS Grid，`repeat(auto-fill, minmax(280px, 1fr))`，gap `--space-4`。

**每张卡片**：
- 顶部：`name`（`--text-md` 粗） + 右上 `gender` chip（male=Ocean Blue，female=Cyan）
- 中部：`accent` / `age` / `descriptive` / `use_case` 的 chip 串（存在才渲染）
- 底部：`description` 前 80 字
- 右下角：试听按钮（播放 → 暂停 / 停止图标切换）

**试听**：页面单一 `HTMLAudioElement` 实例。点另一张卡片自动 `pause()` 并 `src=''` 释放。同一张卡再点等于暂停。

**分页**：每页 48 条。底部 `上一页 / 下一页 / 第 X / Y 页`。翻页时平滑滚到列表顶部。

**空态**：
- 当前语种的 `elevenlabs_voices` 行数 = 0：显示"**该语种声音库尚未同步**"，管理员额外显示"去同步"链接到 `/admin/settings#voice-library-sync`。
- 筛选后为 0 条：显示"**没有匹配当前筛选的音色**"，带"重置筛选"按钮。

### 4.3 Tab 2 · 视频匹配

**布局**：垂直单列，最大宽 `--container-max`。

**Step 1**：**选择目标语言**（pill 列表，同 Tab 1 的语言筛选）+ **选择性别**（男 / 女，pill，必须二选一）。

**Step 2**：**上传视频**
- 组件：拖拽区 + "选择文件"按钮。
- 接受：`video/*`，前端大小上限沿用现有项目翻译模块（如 500MB，从 `config.py` 取）。
- 点击 / 拖入 → `POST /voice-library/api/match/upload-url` 拿 TOS 预签 URL → 浏览器 PUT 直传 → `POST /voice-library/api/match/start` 通知后端。
- 进度条：0-100%（上传阶段）。

**Step 3**：**匹配进行中**
- 状态条文字：上传完成 → 采样音频 → 计算声纹 → 匹配中 → 完成。
- 前端轮询 `GET /voice-library/api/match/status/<task_id>`，间隔 1.5s。

**Step 4**：**结果**
- 顶部：10 秒**源视频采样片段**（后端已切好并返回签名地址），可试听。
- 下方：**Top-3 候选卡片**横向一排，和 Tab 1 的卡片样式完全一致，额外多一条"**相似度 87.3%**"字段（右下角）。
- 底部："**重新上传一个视频**"按钮（重置本次任务回到 Step 2）。

**不持久化**：用户关闭页面，本次任务即作废。后端 TTL 30min 自动清理。

### 4.4 样式

沿用项目 Ocean Blue 规范（`CLAUDE.md` 里的 token），新增少量私有样式放 `_voice_library_styles.html`。

---

## 5. 后端

### 5.1 新文件

| 路径 | 职责 |
|---|---|
| `web/routes/voice_library.py` | Blueprint `/voice-library`。只做 HTTP，逻辑委托 service |
| `web/templates/voice_library.html` | 页面模板，内嵌两个 Tab 骨架 |
| `web/templates/_voice_library_styles.html` | 私有样式 |
| `web/templates/_voice_library_scripts.html` | 页面 JS 引入点 |
| `web/static/voice_library.js` | 前端逻辑（筛选 / 分页 / 试听 / 上传 / 轮询 / 渲染） |
| `appcore/voice_library_browse.py` | `list_voices(filters, page, page_size)` + `list_filter_options(language)` |
| `appcore/voice_match_tasks.py` | 匹配任务内存字典 + TTL + 线程池 |

### 5.2 修改文件

| 路径 | 改动 |
|---|---|
| `web/templates/layout.html` | 顶部导航追加"声音仓库"菜单项 |
| `web/app.py` | 注册 `voice_library` blueprint + 注册 SocketIO 事件 |
| `web/templates/admin_settings.html` | 追加"声音库同步"区块 |
| `web/routes/admin.py` | 追加同步接口 + 状态接口 |
| `web/static/admin_settings.js` | 同步按钮交互 + SocketIO 监听 |
| `db/schema.sql` | 无新表；追加 `(language, gender)` 复合索引迁移脚本 |
| `db/migrations/2026_04_17_voice_library_indexes.sql` | 新增迁移 |

### 5.3 HTTP 接口契约

#### 浏览列表筛选选项

```
GET /voice-library/api/filters?language=de
→ 200
{
  "languages": [
    {"code": "de", "name_zh": "德语"},
    {"code": "fr", "name_zh": "法语"}
  ],
  "genders": ["male", "female"],
  "use_cases": ["narrative", "advertisement", ...],
  "accents": ["american", "british", ...],
  "ages": ["young", "middle_aged", "old"],
  "descriptives": ["deep", "warm", "raspy", ...]
}
```

字段来源：
- `languages` 来自 `media_languages.enabled=1`
- `genders` 静态 `["male","female"]`（`elevenlabs_voices.gender` 是顶级列，ElevenLabs 语义本就只有两值）
- `use_cases` / `accents` / `ages` / `descriptives` 来自 `elevenlabs_voices.labels_json` 跨行聚合（去重、去空）

枚举按**当前选中的语种**收敛（即选德语时只展示德语库里出现过的 `use_case`），避免无意义筛选项。前端每次切换语言都重新拉 `/api/filters?language=<code>`。

#### 列出声音

```
GET /voice-library/api/list?language=de&gender=male&use_case=narrative&accent=&age=&descriptive=&q=&page=1&page_size=48
→ 200
{
  "total": 123,
  "page": 1,
  "page_size": 48,
  "items": [
    {
      "voice_id": "xxx",
      "name": "Marcus",
      "gender": "male",
      "language": "de",
      "accent": "german",
      "age": "middle_aged",
      "descriptive": "deep",
      "use_case": "narrative",
      "category": "professional",
      "description": "...",
      "preview_url": "https://..."
    }, ...
  ]
}
```

- `language` 必填，缺失返回 400。
- 多选字段（use_case / accent / age / descriptive）在 URL 里用 `,` 分隔。
- 排序：`category = 'professional'` 优先，其余按 `synced_at DESC`。

#### 匹配：拿上传 URL

```
POST /voice-library/api/match/upload-url
Body: {"filename": "demo.mp4", "content_type": "video/mp4"}
→ 200
{
  "upload_url": "https://tos.../signed-put",
  "object_key": "voice_match/<user_id>/<uuid>/demo.mp4",
  "expires_in": 600
}
```

#### 匹配：启动

```
POST /voice-library/api/match/start
Body: {
  "object_key": "voice_match/1/uuid/demo.mp4",
  "language": "de",
  "gender": "male"
}
→ 202
{ "task_id": "vm_<uuid>" }
```

校验：
- `object_key` 前缀必须是 `voice_match/<current_user.id>/`
- `language` 必须在 `media_languages.enabled=1` 中
- `gender` 必须是 `male` / `female`

#### 匹配：状态

```
GET /voice-library/api/match/status/<task_id>
→ 200
{
  "task_id": "vm_xxx",
  "status": "pending" | "sampling" | "embedding" | "matching" | "done" | "failed",
  "progress": 0-100,
  "error": null | "...",
  "result": null | {
    "sample_audio_url": "https://...signed.wav",
    "candidates": [
      { ...voice fields..., "similarity": 0.873 },
      { ...voice fields..., "similarity": 0.841 },
      { ...voice fields..., "similarity": 0.812 }
    ]
  }
}
```

task 字典 key 带 `user_id`，其他用户用对方 `task_id` 请求返回 404。

### 5.4 匹配任务执行

`appcore/voice_match_tasks.py` 职责：

- 全局单例：`_TASKS: dict[task_id, TaskState]`，进程内字典。
- 创建任务时入一个 `ThreadPoolExecutor(max_workers=2)`。
- 任务步骤：
  1. `sampling`：TOS 下载到 `uploads/voice_match/<task_id>/src.mp4` → `extract_sample_clip()` → 得 `src_clip.wav`。
  2. `embedding`：`embed_audio_file(src_clip.wav)` → 256 维向量。
  3. `matching`：调 `voice_match.match_candidates(vec, language, gender, top_k=3)`。
  4. `done`：把 `src_clip.wav` 上传回 TOS，生成签名 URL（1h），塞进 `result.sample_audio_url`；候选里的 `preview_url` 直接用 ElevenLabs 的 public GCS 链接（已是公开）。
- 失败任何一步 → `status=failed`，记 `error`。
- 完成后 30min 自动从字典里移除（后台起一个守护线程每 60s 扫一遍过期）；清理时**一并删除**：
  - TOS 上的源 `voice_match/<user_id>/<uuid>/demo.mp4`
  - TOS 上的 `voice_match/<user_id>/<uuid>/src_clip.wav`
  - 本地 `uploads/voice_match/<task_id>/` 目录
- 进程重启即丢（和设计一致——本就不持久化）；上次进程遗留的 TOS 前缀 `voice_match/*` 由现有 `appcore/cleanup.py` 的孤儿清理周期性兜底（追加一条清理规则）。

并发：同一用户允许多个匹配任务并行（线程池限制 2）；同步任务全局串行（见 § 6）。

### 5.5 列表 / 筛选查询

`appcore/voice_library_browse.py`：

```python
def list_voices(
    *,
    language: str,
    gender: Optional[str] = None,
    use_cases: list[str] | None = None,
    accents: list[str] | None = None,
    ages: list[str] | None = None,
    descriptives: list[str] | None = None,
    q: str | None = None,
    page: int = 1,
    page_size: int = 48,
) -> dict:
    ...
```

实现：
- 主表 `elevenlabs_voices`，过滤 `language`（必）、`gender`（可）。
- 多选字段的值是 `labels_json` 里的标量字符串（例如 `labels.use_case = "narrative"`），匹配用 `JSON_UNQUOTE(JSON_EXTRACT(labels_json, '$.use_case')) IN (%s, %s, ...)`（MySQL 5.7+ 原生支持）。索引不能完全覆盖，但受限语种后数据量 < 几百条，全表扫描 < 50ms。
- `q` 模糊匹配 `name` / `description`（`LIKE '%q%'`）。
- `category = 'professional'` 优先排序。

### 5.6 DB 迁移

`db/migrations/2026_04_17_voice_library_indexes.sql`：

```sql
ALTER TABLE elevenlabs_voices
  ADD INDEX idx_lang_gender (language, gender);
```

---

## 6. 管理员同步入口

### 6.1 UI

`admin_settings.html` 在"素材语种配置"下方追加 `<section id="voice-library-sync">`：

- 标题："声音库同步（ElevenLabs）"
- 描述："为每个启用的小语种同步 ElevenLabs 共享音色库（含声纹向量生成）。"
- 表格式列表（每行一个启用语种）：

| 语种 | 本地条目数 | 声纹覆盖率 | 最后同步时间 | 操作 |
|---|---|---|---|---|
| 德语 (de) | 187 | 182 / 187 (97.3%) | 2026-04-17 11:23 | [同步] |
| 法语 (fr) | 156 | 0 / 156 (0%) | 未同步 | [同步] |

- 某一行正在同步时：按钮变"同步中…"，行下方展开进度条，文字"拉取音色 23 / 87" 或 "生成声纹 45 / 156"。
- 全局只允许 1 个同步任务：其他行按钮变"排队中"（点了也不生效，前端 disabled）。

### 6.2 接口

```
POST /admin/voice-library/sync/<language>
  Body: {}
  → 202 {"sync_id": "sync_<uuid>"}
  → 409 {"error": "another sync is running"}  (全局已有任务)

GET /admin/voice-library/sync-status
  → 200 {
       "current": { "sync_id":..., "language":"de", "phase":"embed", "done":45, "total":156 } | null,
       "summary": [
         {"language":"de","total_rows":187,"embedded_rows":182,"last_synced_at":"..."},
         ...
       ]
     }
```

权限：`@admin_required`。

### 6.3 任务实现

`appcore/voice_match_tasks.py` 复用同一个模块放不进，单开 `appcore/voice_library_sync_task.py`：

- 全局单例 `_CURRENT: dict | None`，带锁。
- 两阶段：
  1. `pull_metadata`：`voice_library_sync.sync_all_shared_voices(language=lang)`，过程中每页回调更新 `done/total`。
  2. `embed`：`voice_library_sync.embed_missing_voices(cache_dir, limit=None)`，改造为支持进度回调（每条完成 +1，当前是 print，改为 callback）。
- 进度通过 SocketIO 广播事件 `voice_library.sync.progress`：

```json
{
  "sync_id": "...",
  "language": "de",
  "phase": "pull_metadata" | "embed",
  "done": 23,
  "total": 87,
  "status": "running" | "done" | "failed",
  "error": null
}
```

- 事件只推给**管理员房间**（复用现有 SocketIO 权限模型，登录时 admin 自动加入 `admin` room）。
- 任务开始/结束时也触发一条 `voice_library.sync.summary` 事件，带最新的总览数据。

### 6.4 需要的 `voice_library_sync.py` 小改

- `embed_missing_voices` 当前 `print(...)` 容错日志，改为 `logger.warning` + 可选 `on_progress(done, total, voice_id, ok)` 回调。
- `sync_all_shared_voices` 加 `on_page(page_index, page_total, voices_in_page)` 回调。

---

## 7. 鉴权与多用户隔离

- `/voice-library/*`：登录即可。全局库读取不按 user 过滤。
- `/voice-library/api/match/*`：task 创建时记录 `user_id`；status 接口用 `current_user.id` 校验 ownership，不匹配返回 404。
- TOS object_key 前缀强制 `voice_match/<user_id>/`，防止用户互相偷签 URL。
- `/admin/voice-library/*`：`@admin_required`。

---

## 8. 错误处理

| 情况 | 行为 |
|---|---|
| Tab 1 无数据（语种未同步） | 前端空态 + 管理员可见"去同步"链接 |
| Tab 1 筛选无结果 | 前端空态 + "重置筛选"按钮 |
| 上传 TOS 失败 | 前端显示错误 + "重试上传"按钮 |
| 源视频无音轨 / codec 损坏 | task 状态 `failed`，`error="无法从视频提取音频"` |
| 目标语种 embedding 覆盖率 = 0 | task 状态 `failed`，`error="该语种声音库尚未同步，请联系管理员"` |
| 同步 ElevenLabs API 401 | sync 状态 `failed`，`error="ElevenLabs API Key 无效"` |
| 同步并发冲突 | HTTP 409 + 前端 toast |
| 任意后端异常 | 统一 500 + JSON `{"error":"..."}` |

---

## 9. 性能

- 列表查询：`language` 必选 + 复合索引 `(language, gender)`，分页返回 48 条。每语种 < 几百行，查询 < 50ms。
- 匹配：目标语种 embedding 一次性拉进内存（数百 × 1KB），numpy 向量化计算 cosine 全部 < 100ms。主要耗时在 ffmpeg 采样（~1-3s）+ resemblyzer 编码（~3-8s）。
- 同步：后台线程，不占 HTTP worker；同时一个任务。Embedding 下载可并发但当前先串行实现（`requests` 同步调用），后续优化再议。
- 前端试听：单音频实例，不会累积播放器。

---

## 10. 测试计划

新增：

| 测试文件 | 覆盖 |
|---|---|
| `tests/test_voice_library_browse.py` | `list_voices` 筛选 / 分页 / JSON_EXTRACT 路径 / 空态 / 排序 |
| `tests/test_voice_library_routes.py` | `/api/filters` / `/api/list` HTTP 层、未登录 401、非法 language 400 |
| `tests/test_voice_match_api.py` | `/match/upload-url` / `/match/start` / `/match/status` 全流程，mock `embed_audio_file` / `extract_sample_clip` / TOS |
| `tests/test_voice_match_tasks.py` | 任务状态机、TTL 清理、并发多任务、错误捕获 |
| `tests/test_voice_library_sync_admin.py` | `/admin/voice-library/sync/<lang>` 启动、409 并发拒绝、`sync-status` 返回结构 |
| `tests/test_voice_library_sync_task.py` | 两阶段进度回调、SocketIO 事件负载结构（mock socketio） |

修改：

- `tests/test_web_routes.py`：追加 layout 菜单项存在性断言。

前端无自动化测试（项目惯例），手测清单：

1. 不同语种筛选 → 卡片正确、分页正常、空态
2. 7 项筛选组合 → SQL 命中正确
3. 试听多张卡 → 只播一张
4. Tab 2 上传小/大视频 → 进度显示、结果 Top-3 展示
5. 视频无音轨 → 错误提示
6. 未同步语种 → 明确提示
7. 管理员同步：进度条、并发拒绝、完成后计数刷新
8. 普通用户访问 `/admin/voice-library/*` → 403

---

## 11. 实施顺序（writing-plans 会展开）

大致阶段：

1. DB 迁移 + `appcore/voice_library_browse.py` + 对应测试
2. `web/routes/voice_library.py` 的 `/api/filters` + `/api/list` + 对应测试
3. `voice_library.html` + `voice_library.js` 的 Tab 1（浏览试听）
4. `appcore/voice_match_tasks.py` + `/api/match/*` 接口 + 对应测试
5. `voice_library.js` 的 Tab 2（匹配）
6. `appcore/voice_library_sync_task.py` + 修改 `pipeline/voice_library_sync.py` 加回调
7. `admin_settings.html` 的同步区块 + `/admin/voice-library/*` 接口 + SocketIO 事件
8. `layout.html` 菜单项 + 最终手测 checklist

---

## 12. 验收

- 后台能在 `/admin/settings#voice-library-sync` 点按钮同步一个语种，看到进度、完成、覆盖率刷新。
- `/voice-library#browse` 切语种能看到卡片，筛选/分页/试听都能用。
- `/voice-library#match` 上传视频后能看到 Top-3 候选并试听。
- 匹配结果的"粗特征相似"级别符合预期（用一两个真实视频跑几轮，主观判断）；如果主观判断明显不达标，该功能可以下线不上线，但仓库浏览/试听部分保留。
- 测试全部通过：`pytest tests -q`。
