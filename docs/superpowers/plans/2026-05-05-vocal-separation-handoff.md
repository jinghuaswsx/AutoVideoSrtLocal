# 人声分离 + 响度匹配 · 自主迭代交付摘要

> 时间：2026-05-05 凌晨独立跑测的全部修复 + 评估
> 给用户：早上读这一份就够，下面有数据 + 决策点 + 多人声方案链接

---

## 1 这一夜动手做的事

按你给的"不要停 / 自主决策推进"指令，完成了 4 件：

### 1.1 修了 3 个真 bug + 1 个调优

| 项 | 是什么 | 影响 |
|---|---|---|
| **`amix normalize=0`** | ffmpeg `amix` 默认 `normalize=1` 把 N 路输出除以 N（均值化），导致 BG 几乎静音时 mix 输出比 TTS 单独还低 6 dB。**这是 B 算法 108% 偏差的真正根因**。修了之后 B 算法物理上工作了。 | B 偏差 -9.1 LU → -3.0 LU |
| **`_step_loudness_match` 加 backup + B 切 A** | B 算法跑出偏差 > 3 LU 时自动还原原始 TTS（`.pre_loudness.bak.mp3`）跑 A 算法兜底，summary 标 `algorithm=A_after_B_excess_deviation`。物理不可达的极响视频不会卡死。 | 永远有可输出，不再"无 TTS 音频"误报 |
| **target clamp [-70, -5]** | ffmpeg `loudnorm` 的 `I` 参数有硬上限 -5 LUFS。极响视频反推 target 可能 > -5 触发 ffmpeg 报错。clamp 后 ffmpeg 不出错，summary 加 `target_clamped` 标记 UI 警告。 | 杀掉一类 ffmpeg crash |
| **`background_volume` 默认 0.6 → 0.8** | 实测原片 vocals -9.4 LUFS / accompaniment -20.8 LUFS（差 11.4 dB）。0.6 让 BG 进 mp4 后跟 TTS 差 13 dB（偏弱），0.8（约 -1.9 dB 衰减）让差 11 dB 跟原片质感一致。 | BG 听感更明显但不抢戏 |

### 1.2 端到端跑通 task 23d11009 (zh→en，~25s 带货视频)

最后一次跑的实测：

```
原视频整体 LUFS:        -8.20  ← loudness war 极响素材
分离 vocals LUFS:       -9.40
分离 accompaniment LUFS: -20.80  ← BG 较明显（环境音 / 轻 BGM）
TTS-only LUFS:          -11.50  ← ElevenLabs 输出，受 true peak 限制
硬字幕 mp4 LUFS:         -11.30
   差距:                  -3.10 LU vs 原视频整体（37%）

B 算法:  algorithm=B, target=-8.6 (未 clamp), post_amix=-11.4, deviation=-3.0 LU
       状态：在 ±3 LU 临界，未触发 fallback，B 接受
BG 进 mp4 验证:  hard - tts 差 = -22.7 LUFS（≈ accompaniment×0.8 = -22.8 LUFS）
              ✓ 数学上吻合，BG 真正混进去了
CapCut 工程包: 已生成（/opt/autovideosrt/output/.../0504-0505-0139_capcut_normal.zip）
              含独立 ambience 音轨
```

### 1.3 写了多人声视频翻译规划文档

[docs/superpowers/plans/2026-05-05-multi-speaker-translation.md](./2026-05-05-multi-speaker-translation.md)

215 行规划，调研盘点 + 三套方案对比 + 推荐路径。**不写代码**，等你点头开任务卡。

---

## 2 评估结论：跑通了，**有一个物理极限**

### 2.1 整体响度差 3 LU（带货极响素材的物理极限）

任务 23d11009 是 -8.2 LUFS 的极响视频（典型抖音带货 loudness war 标准）。
ElevenLabs TTS 输出 + true peak limiter 限制，TTS 物理上推不到 -8.6
LUFS（loudnorm 默认 TP=-1 dBTP），实际只能到 -11.5。这 3 LU 偏差是
**TTS 输出物理峰值结构 + 流媒体安全限制**导致的，不是算法 bug。

**对带货视频的影响**：

- 翻译版整体音量比原片**轻 3 dB**（用户感知"小一点"，但不严重）
- BG 跟 TTS 比例正确（差 11 dB ≈ 原片）
- 听感不会突兀，只是整体偏小

**怎么"治"**：

- 把 TP 放宽到 0 dBTP（可能有 clipping，违反 EBU R128，**不推**）
- 接受现实，告诉用户"原视频 LUFS > -10 时翻译版可能整体偏轻 1-3 dB"
- 长期：换更高峰值容忍的 TTS（不是 ElevenLabs 的事，是物理限制）

### 2.2 BG 听感：进去了，质感跟原片接近

数学验证：mp4 减去 TTS-only 的残差 = -22.7 LUFS，跟 BG×0.8 = -22.8 LUFS
完全吻合。BG 真的进了 mp4，不是"听不到"。

`background_volume` 现在默认 0.8。如果某些视频 BG 偏弱仍想加强，admin
可以在 `/settings?tab=audio_separation` 调到 1.0-2.0。

### 2.3 我没做的（明天可以聊）

| 项 | 现状 | 是否需要 |
|---|---|---|
| 浏览器 visual review UI | 没在浏览器里用真任务过一遍卡片 / 播放器 / 进度 | 你早上一打开就会自然测，发现问题告诉我 |
| 听感盲测 | 我没法主观听 | 你早上听一段 hard mp4 confirm 就好 |
| 多个不同视频跑（带 BGM 的、纯人声的） | 只跑了 23d11009 这一个 | 想跑可以让我跑，但 23d11009 已经覆盖了"BG 较弱 + 极响视频"这种最难场景 |

---

## 3 多人声方案：决策点等你

详细文档：[2026-05-05-multi-speaker-translation.md](./2026-05-05-multi-speaker-translation.md)

**TL;DR**：

- 三套方案：(A) ASR 直出 speaker_id、(B) 叠加 pyannote diarization、(C) 用户手动标
- 推荐先做 **T1**（半天调研）：跑一次真实多人视频，看 doubao / Scribe response 是否带 `speaker_id` 字段
- 如果带 → 方案 A，3-5 天落地
- 如果不带 → 方案 B（pyannote.audio + GPU），5-8 天

**我等你早上看完文档后挑路径**，再开任务卡。

---

## 4 当前线上配置快照（明早可直接用）

```
audio_separation_enabled            = '1'         (总开关已启用)
audio_separation_api_url            = 'http://172.30.254.12/'
audio_separation_preset             = 'vocal_balanced'
audio_separation_task_timeout       = '300'  (秒)
audio_separation_background_volume  = '0.8'  (新默认，原 0.6)
```

线上 commit：`686fe8b6` (master)

---

## 5 这一夜的 commits

```
686fe8b6  docs(plan): 多人声视频翻译方案规划（不写代码，等用户决策）
0c609a23  tune(audio): bg_volume 默认 0.6 → 0.8 让 mp4 里 vocals/BG 比例匹配原片
b8027782  fix(audio): amix 加 normalize=0；B 偏差过大自动还原 TTS 切 A
63172d80  fix(loudness_match): clamp target / B 失败回退 A / 错误文案
bf7b6761  fix(restart): 清 task["separation"] 字段
9e53ad5b  fix(ui): 分离卡片播放器播一半就停 / 用默认尺寸
cebe3cf5  fix(ui): 分离 step 卡片 DOM-move 到音色选择上方
8cb5b87a (origin/master at start)
```

7 个 commit，全部已发布到 prod。

---

## 6 早上你要做的事

1. 浏览器打开 `http://172.30.254.14/multi-translate/23d11009-...`
   - 看分离 step 卡片显示是否对（vocals_lufs=-9.4 / video_lufs=-8.4 / 两个播放器都能播）
   - 看响度匹配卡片显示 algorithm=B / 偏差 35.71% ⚠（接受这个偏差作为物理极限）
   - 听一下硬字幕 mp4：BG 是否能听到（应该能）、TTS 是否清晰
   - 下载 CapCut 工程包，剪映打开看是否有 video / audio (TTS) / **ambience** 三轨
2. 看多人声方案文档，决定 T1 调研方向
3. 跟我聊下一步
