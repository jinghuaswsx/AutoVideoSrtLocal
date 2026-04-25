# 多源语言治本版（Multi-source Language Treatment）

**Branch**: `feature/multi-source-treatment`
**Started**: 2026-04-25
**Status**: ✅ Phase 1-5 完成，待端到端验证 + 部署

---

## 背景

方案 A（pivot to English）已上线止血——西语视频 ASR 后 pivot 到英文再走原流水线。
但**底层"假设源是中/英文"的固化点仍在**：
- LID 是 zh/en 二分类，西语会被错判
- 各 prompt 写死"Translate the Chinese source"
- 字符/字数预算不接收源语言上下文
- ASR 入口只对接豆包（不支持西语）

治本版的目标：把 `source_language` 升为一等参数贯穿全链路，让 zh/en/es 三种源
和 en/de/fr/ja/es/pt/it/nl/sv/fi 任意目标语种都能直翻、不绕英文中转。

---

## Phase 总览

| Phase | 内容 | Commit | 状态 |
|---|---|---|---|
| 1.1 | LID 升级 zh/en 二分 → zh/en/es 三分 | `5a25770` | ✅ |
| 1.2 | 共享 lang_labels.py + 主翻译 prompt 参数化 | `5a25770` | ✅ |
| 1.3 | de/fr 翻译模块同步参数化 | `805c872` | ✅ |
| 1.4 | generate_localized_translation 加 source_language 入参 | `805c872` | ✅ |
| 1.5 | runtime 各调用方下传 source_language | `8d51864` | ✅ |
| 1.6 | rewrite messages 改用共享 lang_label() | `8d51864` | ✅ |
| 1.7 | 路由层 ("zh","en") → ("zh","en","es") | `6bfdf80` | ✅ |
| 2 | 收敛容差/上限按目标语动态查表 | `034e92e` | ✅ |
| 3.1 | ElevenLabs Scribe ASR 适配器 | `034e92e` | ✅ |
| 3.2 | _step_asr 按 source_language 分发 | `217b475` | ✅ |
| 4a | 初始翻译 prompt 加目标字数提示 | `217b475` | ✅ |
| 5 阶段 1 | _step_asr 分发集成测试 | _next_ | ✅ |
| 5 阶段 2 | 西语样本视频端到端验证 | TODO | ⏳ |
| 6 | 部署 + 监控 | TODO | ⏳ |

---

## 关键改造文件

### 新增

- `pipeline/lang_labels.py` — 共享 lang code → 中/英文标签映射，单一来源
- `pipeline/asr_scribe.py` — ElevenLabs Scribe API 适配器，输出对齐豆包结构
- `tests/test_lang_labels.py` — 5 项断言
- `tests/test_language_detect.py` — 19 项断言（zh/en/es 三分类）
- `tests/test_asr_scribe.py` — 7 项 _parse_scribe_response 单元测试
- `tests/test_asr_router.py` — 7 项路由器单元测试
- `tests/test_step_asr_dispatch.py` — 4 项 _step_asr 集成测试

### 修改

- `pipeline/language_detect.py` — 三分类启发式（CJK + 西语特征字符 + 高频词）
- `pipeline/asr.py` — 内联 `transcribe_local_audio_for_source` 路由器函数
- `pipeline/localization.py`
  - `LOCALIZED_TRANSLATION_SYSTEM_PROMPT` 等 4 个 prompt 把硬编码 "Chinese"
    参数化为 `{source_language_label}` / `{source_language_label_zh}`
  - `build_localized_translation_messages` 加 `source_language: str = "zh"` 入参
  - `build_localized_rewrite_messages` 同步用 `lang_label()` 查表
- `pipeline/localization_de.py` / `pipeline/localization_fr.py`
  - 共用 lang_labels.lang_label() 替代硬编码字典
  - prompt 里 "from Chinese or English" 改成 `{source_language_label}`
  - `build_localized_translation_messages` 加 `target_words` / `video_duration`
    keyword-only，user content 末尾追加 "Aim for ~N words on Xs video"
- `pipeline/translate.py` — `generate_localized_translation` 加
  `source_language: str = "zh"` keyword-only，转给 builder
- `appcore/runtime.py`
  - `_step_asr` 按 source_language 分发（zh/en→豆包+TOS；其他→Scribe 本地）
  - ai_billing.log_request 的 provider/model 改为动态字符串
  - 新增 `_WORD_TOLERANCE_BY_TARGET` / `_MAX_REWRITE_ATTEMPTS_BY_TARGET` 常量表
  - 收敛循环硬编码 5/0.10 替换为查表调用
- `appcore/runtime_de.py` / `appcore/runtime_fr.py` / `appcore/runtime_multi.py`
  - generate_localized_translation 调用加 `source_language=source_language`
  - runtime_multi 顶部加 `from pipeline.lang_labels import lang_label`
- `web/routes/multi_translate.py` / `web/routes/de_translate.py` /
  `web/routes/fr_translate.py`
  - `("zh", "en")` 校验改 `("zh", "en", "es")`，错误文案同步
  - 共 6 处校验放开

---

## 测试矩阵（已通过）

```bash
python -m pytest \
  tests/test_lang_labels.py \
  tests/test_language_detect.py \
  tests/test_asr_scribe.py \
  tests/test_asr_router.py \
  tests/test_step_asr_dispatch.py \
  tests/test_localization.py \
  tests/test_localized_rewrite_prompts.py \
  tests/test_translate_detail_protocol.py \
  tests/test_multi_translate_routes.py \
  tests/test_translate_detail_shell_templates.py \
  tests/test_tts_duration_loop.py \
  -q
```

历次回归：53 / 130 / 117 项全过（不同子集）。

**注意**：`tests/test_runtime_multi_translate.py` 中 3 项测试在大集合并跑时
出现 ConnectionRefusedError（连真实 MySQL），单独跑或 pairing 跑全过——属
pre-existing test ordering 问题，与本次治本改动无关。

---

## ASR 引擎对照

| Source language | Engine | 接入方式 | 备注 |
|---|---|---|---|
| zh / en | 豆包 SeedASR `volc.seedasr.auc` + `bigmodel` | URL（先 upload TOS） | 中文识别行业头部，英文也稳定 |
| es / pt / de / fr / 其他 | ElevenLabs Scribe `scribe_v2` | 本地 multipart 上传 | 99 语种，word-level 时间戳 |

**API key 复用**：Scribe 直接用现有 `ELEVENLABS_API_KEY`（同账号，TTS/Scribe
共享），不需要新申请。

**计费**：ai_billing 区分记录两条独立的 provider/model，可在用量看板看到豆包
vs Scribe 各自消耗。

---

## 关键设计抉择

### 为什么不全切 Scribe？

豆包对**中文**识别明显优于 Scribe，且单价更便宜（按秒计费）。Scribe 西语虽好
但中文 WER 不如豆包。所以分发：zh/en 留豆包，其他走 Scribe 是质量+成本最优解。

### 为什么 _WORD_TOLERANCE 按目标语而非源语？

收敛 rewrite 是把目标语言文本压到 ±N% 字数窗口。LLM 对**目标语**的字数控制能力
是瓶颈：德语复合词长（一个词当多个用）、日语全角字符密度高，LLM 经常漂出窗口。
源语言只影响初始翻译长度，已被 Phase 4a 的 target_words 提示部分弥补。

### 为什么不引入 fasttext lid.176？

当前 zh/en/es 三分类启发式准确率 19/19，覆盖业务限定的 3 种源足够。引入 fasttext
要加 ~125MB 二进制模型 + 维护 license。等业务扩到 5+ 种源时再换。

---

## Phase 5 阶段 2：端到端验证（待人工执行）

样本：`C:\Users\admin\Desktop\德国法国测试\西班牙语视频.mp4` （35.4s, h264+aac）

**步骤**：

1. UI 上创建 multi_translate 任务，选**源语言=西班牙语**，目标语言=德语
2. 提交后观察：
   - ASR step 状态文案应显示"正在识别西班牙语语音（ElevenLabs Scribe）..."
   - `task.json` 中 `asr_provider` 字段应为 `"elevenlabs_scribe"`
   - `localized_translate_messages.json` 的 system prompt 里 source_language_label
     应该已被替换为 "Spanish"
   - `tts_duration_rounds[*].max_rewrite_attempts` 应为 7（target=de），不是 5
   - `tts_duration_rounds[*].word_tolerance` 应为 0.15（target=de），不是 0.10
3. **关键观察**：duration loop 是否 ≤ 3 轮收敛（而非旧版的 5×5=25 跑满）
4. 最终 mp4 听感验收

**对照实验**（可选）：同主题英语视频跑一遍同样路径，确认 zh/en 路径不退化。

---

## Phase 6：部署清单

1. 合并 `feature/multi-source-treatment` → `master`（建议 squash 或 merge commit）
2. 服务器 pull + 重启
3. 数据库**无需迁移**（所有改动都是参数化和路由，schema 不变）
4. 前端**无需重新构建**（路由放开是后端事；前端下拉补西语选项另起 worktree）
5. 监控指标：
   - ai_billing 中 `provider="elevenlabs_scribe"` 的请求数（新维度）
   - tts_duration_rounds 的平均轮数（应下降）
   - duration loop "未收敛"事件率（应下降）

---

## 后续工作（不阻塞本次合并）

- 前端模板下拉加西语选项（multi_translate_detail.html / de_translate_detail.html /
  fr_translate_detail.html）
- 变量名清理：`source_full_text_zh` → `source_full_text` 全仓 grep 替换（约 20 处）
- 收敛 prompt 加源-目标 pair 双知情：让 rewrite 模型知道"这是 Spanish→German
  的本地化"，避免回译漂移
- 长度预算 source-target ratio 表：初始 target_words 估算引入语对系数（zh→en≈3.5,
  es→en≈0.85, en→de≈1.3, ...）
- LID 升级到 fasttext lid.176 多分类（覆盖 ≥10 语种）

---

## 已知限制

- ja_translate 路由未放开 es 源（独立路径，使用场景极少，留给后续迭代）
- av_translate（视听本地化独立 pipeline）的 SYSTEM_PROMPT_TEMPLATE 仍硬编码
  目标语言上下文，不影响主线 multi_translate / de / fr
- LID 只覆盖 zh/en/es，pt/it/de 作为源被错判为 en（治本版后续扩 fasttext 再补）
