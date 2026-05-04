# 多人声视频翻译方案规划

> 状态：调研 + 落地规划，**不写代码**。等用户对方案点头后再开任务卡。
> 撰写日期：2026-05-05
> 触发场景：本次接入"人声分离 + 响度匹配"功能后，发现某些素材（带货访谈、多人对话、双语播报）原视频里有**多个人声**，但当前流水线对所有
> utterances 用**同一个 voice** 合成 TTS。翻译版主播声"千篇一律"，跟原片
> 多角色对话的质感差距大。

---

## 1 当前实现回顾（基线）

```
extract → asr (Doubao/Scribe) → asr_normalize → separate
       → voice_match（按整段 vocals.wav 跑 embedding，匹配 1 个音色）
       → translate → tts（用 selected_voice_id 生成所有 utterances）
       → loudness_match → subtitle → compose → export
```

**当前**对说话人是 **single-speaker assumption**：

- ASR 输出 utterances（`text / start_time / end_time`），不带 speaker 标签
- voice_match 把整段 vocals.wav 喂给一个 voice embedding 模型，匹配 ElevenLabs 音色库一个 voice
- TTS 用这一个 voice_id 合成所有 utterances

**问题**：

- 多人对话视频：男主播 + 女嘉宾 → 翻译版全是同一个声音，听感失真
- 双语播报：中文主播 + 英文专家 → 翻译版无法保留这个区分
- 多角色情景：剧本类带货 → 角色辨识度全失

---

## 2 现成技术构件盘点

### 2.1 Speaker Diarization（"说话人分离"，区分谁在说话）

| 方案 | 类型 | 输出粒度 | 现状 |
|---|---|---|---|
| **pyannote.audio** | 开源 PyTorch | 0.5s 时间戳 + speaker_id | 行业 SOTA，需要 HuggingFace token；本地 GPU 跑 |
| **WhisperX** | Whisper + pyannote 集成 | utterance 级 + speaker_id | 把 ASR + diarization 一起跑，输出最齐 |
| **ElevenLabs Scribe** | 闭源 API | utterance 级 + speaker_id | 项目里 omni 已经在用，**确认是否带 diarization output** |
| **AWS Transcribe / Azure Speech** | 闭源 API | utterance 级 + speaker_id | 后备 |
| **Doubao SeedASR**（项目当前 zh/en 用） | 闭源 API | utterance 级，**未确认 diarization** | 需查 API doc |

> **关键调研项**（用户决策前要确认）：项目当前 ASR provider（doubao + elevenlabs scribe）原生输出**是否带 speaker_id 字段**。如果 doubao 不带，就要在 ASR step 之后单独跑 pyannote 或 WhisperX 做 diarization。

### 2.2 Multi-voice TTS（多音色合成）

ElevenLabs API 已经支持单 task 内切换 voice_id：每个 utterance 调
`POST /v1/text-to-speech/{voice_id}` 时传不同 voice_id 即可——**项目当前 TTS
代码（`pipeline/tts.py`）已经按 utterance 循环，只是固定取
`selected_voice_id`**。改造成本极低。

### 2.3 多 speaker 的 voice embedding 匹配

当前 `voice_match` 用整段 vocals.wav 跑 embedding，得到一个音色匹配。多 speaker 场景：

- 给每个 speaker 提取 sample（通过 diarization 输出的 timestamp 截短 clip）
- 每个 speaker 单独跑 `embed_audio_file()` + `match_candidates()`
- 给每个 speaker 各推 top-N 候选

`pipeline/voice_match.py` 和 `pipeline/voice_embedding.py` 已经支持
arbitrary clip → embedding → match candidates，复用即可。

### 2.4 BS-Roformer / Demucs 不能解决多人声

**澄清一个常见误解**：人声分离模型（BS-Roformer / Demucs）输出的是
`vocals` vs `accompaniment` 二轨——它**不区分多个人声**，所有人声混在
一条 vocals.wav 里。多 speaker 必须靠 diarization（时间分段），不是
源分离。

---

## 3 方案对比

### 方案 A：ASR 输出带 speaker_id 时直接用（最干净，前提条件）

**适用前提**：当前 ASR provider 原生输出 speaker 标签。

**改造点**：

1. `pipeline/asr.py` / `appcore/asr_router.py` 适配每个 provider 的
   speaker 字段（doubao / scribe / 通用 generic 各不一样）
2. `_step_voice_match` 改：拿到 utterances 的 speaker 集合，按 speaker
   分组各跑 embedding，UI voice_selector 多套候选 + 多套确认
3. `_step_tts` 改：每个 utterance 用 `voice_id_by_speaker[speaker]`
   生成；`selected_voice_id` 从单值 → dict
4. UI `_voice_selector_multi.html` 改：多个 speaker 各一个面板

**工作量估算**：3-5 天（含 UI + 测试）。**不改 ASR 模型**。

**风险**：

- doubao 可能不带 speaker_id（ElevenLabs Scribe 大概率带）
- 不同 provider speaker_id 命名不一致，需统一映射

### 方案 B：ASR 没 speaker_id 时叠加 pyannote diarization

**适用条件**：方案 A 的前提不成立，或者用户希望更精确的 speaker 划分。

**改造点**：

1. 新增 step `diarize`（在 asr 之后、separate 之前）
2. 用 `pyannote.audio` 跑分离 vocals.wav（或原音频），输出
   `[(start, end, speaker_id)]` 时间段
3. 把这个标签 join 到 ASR utterances 上（按时间重叠）
4. 后续 voice_match / tts 同方案 A

**工作量估算**：5-8 天。**新增依赖**：

- pyannote.audio（PyTorch + HuggingFace token）
- 部署：本机 GPU（GT 1030 显存不够，需要 3060 那台机器；或者塞到 nomadkaraoke 那台开发电脑同步跑）
- API 化（跟 audio-separator 一样做成内网 HTTP），生产服务器 HTTP 调用

**风险**：

- pyannote 模型权重需要 HuggingFace token（注册 + accept license）
- 短视频（<30s）diarization 不稳定，可能误划
- GPU 排队（跟 separate 共用 RTX 3060，吞吐量减半）

### 方案 C：用户手动标 speaker（半自动兜底）

**适用条件**：A 和 B 都不可行 / 不上时。

**改造点**：

1. UI 在 alignment review 阶段让用户给每条 utterance 标 speaker
   （下拉选 A/B/C 等）
2. 后续 voice_match / tts 按用户标签分组

**工作量估算**：2-3 天。**纯 UI 改造**。

**优点**：100% 准确（用户视觉判定）；不依赖外部模型
**缺点**：用户操作负担重，长视频 100+ utterances 难标完

---

## 4 推荐路径

### 4.1 第一步：调研当前 ASR 是否带 speaker（1 天）

调研动作（不写产品代码）：

1. 用真实多人对话视频跑一次 doubao SeedASR + ElevenLabs Scribe，
   dump 完整 response 看是否有 `speaker_label` / `speaker_id` 字段
2. 如果带：方案 A
3. 如果不带：方案 B（或先做 C 兜底）

### 4.2 第二步：按调研结果落 MVP（3-5 天）

**方案 A 路径（如果 ASR 带 speaker）**：

```
ASR utterances（带 speaker_id）
    ↓ _step_voice_match：按 speaker 分组，每组截 sample → embedding → 候选
    ↓ voice_selector_multi.html 改成多面板，每个 speaker 一栏候选
    ↓ confirm-voice 写 voice_id_by_speaker = {"A": vid_a, "B": vid_b}
    ↓ _step_tts：每个 utterance 用 voice_id_by_speaker[speaker] 生成
```

**方案 B 路径（如果需 diarization）**：

```
asr → 新 step diarize（pyannote 或 WhisperX）
    → 给每条 utterance 加 speaker_id 标签
    → 后续 same as 方案 A
```

### 4.3 第三步：响度匹配的扩展（评估期）

当前响度匹配是单 TTS 单 vocals_lufs。多 speaker 后：

- 每个 speaker 的 TTS 单独归一化到对应原 speaker 的 LUFS？
- 还是整段 TTS 拼起来再归一化到整段 vocals_lufs？

**初步意见**：保持现行做法（整段拼起来归一化），多 speaker 场景下用户
对"主播音量是否一致"敏感度比"每人音量精确还原"高。这个等 MVP 跑通后
真实视频测一下再优化。

---

## 5 风险与决策点

| 风险 | 缓解 |
|---|---|
| ASR provider 不带 speaker_id | 方案 B（pyannote）或方案 C（手动） |
| pyannote 部署需 GPU + HuggingFace token | 跟 audio-separator 同机器（RTX 3060） |
| 多 speaker UI 复杂（用户混淆 A/B 谁是谁） | voice_selector 加 sample 试听按钮，用户能听到每个 speaker 的原音确认 |
| 短视频 diarization 不稳 | 视频时长 <15s 直接 fallback 到 single voice（保持现状） |
| TTS 切 voice 频繁导致 ElevenLabs API 限流 | 当前 TTS 已有 batch 机制，按 voice_id 分组批量调用 |

---

## 6 不在本次范围

- **声音克隆**（用 IVC clone 原视频说话人音色）：太重，且 ElevenLabs 原生支持 IVC，需要每个 speaker 单独 clone 训练，跟用户当前"在 ElevenLabs 音色库选预设音色" 工作流不一致。后续单独项目。
- **情感/韵律迁移**：保留原说话人语调起伏。当前 TTS 不支持，需要切换 model（如 ElevenLabs Multilingual V2 + voice_settings）。后续。
- **分性别自动选音色**：性别检测可以从 voice embedding 得到，但要做分组匹配。可以做但优先级低。

---

## 7 接下来的开槽

**跟用户对齐这个文档后**，开任务卡：

1. T1（半天）：调研当前 ASR provider 是否带 speaker_id，决定 A/B 路径
2. T2（1 天）：跑一次真实多人视频走当前流水线，listen 一下"全用同一音色"的实际听感差距，决定优先级
3. T3（3-5 天 / 5-8 天）：按 A 或 B 路径落 MVP
4. T4（1 天）：真实视频端到端测试 + 文档

---

文档维护人：Claude（基于本次"人声分离 + 响度匹配"集成实现的认知）
预计 review 时长：用户 30 分钟读完后讨论决策点 4.1
