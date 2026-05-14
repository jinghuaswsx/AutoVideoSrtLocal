# Omni Loudness Background Boost Design

- 日期：2026-05-14
- 模块：Omni translate `loudness_match` 任务详情页与响度匹配运行时
- 状态：设计待实现

## 锚点

- `docs/superpowers/plans/2026-05-05-vocal-separation-handoff.md`：现有 `background_volume=0.8` 的来源，BG 与人声约差 10-12 LU 的经验基准，以及 B 算法在 accompaniment 太弱时切 A 的既有行为。
- `docs/superpowers/specs/2026-05-07-omni-dynamic-resume-and-prompt-display-fix.md`：`loudness_match` 是 Omni 动态步骤，必须能按真实 step list 从该步骤恢复。
- `docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md`：Omni 的 `voice_separation` 与 `loudness_match` 是可组合能力点，`loudness_match` 依赖人声分离。
- `web/templates/CLAUDE.md`：翻译详情页模板追加内容必须留在既有 shell/block 内，前端 mutating 请求要带 CSRF。
- `web/static/CLAUDE.md`：前端视觉和交互遵循 Ocean Blue token、按钮和响应式约束。

## 问题

用户反馈 `/omni-translate/<task_id>` 输出视频里背景音乐普遍偏低。当前系统只有全局 `audio_separation_background_volume`，任务详情页不能按单个视频选择更强的背景音方案。

当前响度链路还存在一个容易误导用户的展示问题：当 B 算法整体匹配偏差过大并自动切到 A 兜底时，顶部消息会说明“已切 A”，但响度卡片内部仍按 `tts_loudness.algorithm` 显示“算法 B”。用户会误以为当前结果仍然是 B 算法产物。

用户确认的产品方向：

1. 响度匹配卡片做三个胶囊按钮：`标准`、`增强背景`、`手动调整`。
2. `标准` 作为默认方案，保持当前输出口径。
3. `增强背景` 自动抬高 BGM/环境音，但必须有上限。
4. `手动调整` 打开弹窗选择增强幅度，可选 `+10%` 到 `+100%`，对应当前标准背景音量上的线性增强比例。
5. 按钮下方用小字说明方案差异。
6. 用户选择方案后，从 `loudness_match` 步骤继续，让新响度方案生效。

## 目标

1. 每个 Omni 任务拥有独立 `loudness_profile`，取值为 `standard`、`bg_boost` 或 `manual_boost`。
2. `standard` 默认使用当前全局背景音量，不改变既有任务的默认听感。
3. `bg_boost` 自动计算更高的背景音量，目标是让背景音约比配音低 10 LU，同时把最终背景音量乘数限制在 `1.8` 以内。
4. `manual_boost` 按用户选择的 `manual_boost_pct` 计算背景音量，范围 `10` 到 `100`，含义是标准背景音量的额外增强百分比。
5. 用户在响度匹配卡片选择方案后，选择状态立即保存；点击现有“从此步继续”后，从 `loudness_match` 重跑并使用新方案。
6. UI 明确显示“已选择但未重跑”和“当前结果已按此方案生成”的区别。
7. 重复切换方案不能在已经 loudnorm 过的 TTS 上继续叠加处理；新实现后的任务必须保留响度处理前的 TTS 源文件用于重跑。
8. 响度卡片展示实际生效算法：B 成功显示 B，B 失败或偏差过大切 A 时显示 `A_after_B_failure` 或 `A_after_B_excess_deviation` 的用户可读说明。

## 非目标

1. 不改变全局 `audio_separation_background_volume` 设置默认值。
2. 不改变人声分离 API、preset 或 GPU 调用协议。
3. 不重做 `compose`、字幕、TTS 或 CapCut 工程包生成流程。
4. 不把 CapCut 独立 ambience 音轨预先增益；CapCut 继续保留独立背景音轨，让用户在剪映里单独调音。
5. 不给 multi-translate 生产详情页加同款交互；本次先限定 Omni 实验页。

## UX 设计

响度匹配卡片顶部增加一组分段胶囊按钮：

- `标准`
  - 小字：`按当前背景音量混入，优先保证配音清晰。`
- `增强背景`
  - 小字：`自动提高 BGM/环境音，最高 1.8，避免盖住配音。`
- `手动调整`
  - 小字：`按所选比例提高背景音，最高 1.8。`

按钮视觉和自适应规则：

1. 三个胶囊按钮需要比初版更醒目，按钮高度、横向内边距和字号按约 2 倍视觉尺度调整。
2. 按钮容器必须 `flex-wrap` 自适应换行；窄屏或侧栏挤压时允许一行变多行。
3. 按钮文字必须完整显示，不做省略号、不裁切；`标准`、`增强背景`、`手动调整` 在移动和桌面宽度下都应能读全。
4. 每个按钮保留稳定最小宽度，避免 active、disabled 或文字状态造成布局抖动。

`手动调整` 的交互：

1. 点击 `手动调整` 胶囊时打开小弹窗，不立即保存。
2. 弹窗提供 `+10%` 到 `+100%` 的选择，步长 `10%`。实现可用分段按钮或 slider + 当前值，但提交值必须是整数 `10, 20, ..., 100`。
3. 弹窗确认后保存 `loudness_profile="manual_boost"` 和 `manual_boost_pct`。
4. 弹窗取消时保持原方案不变。
5. 小字显示当前选择，例如 `手动 +50%，点击“从此步继续”后生效。`

交互规则：

1. 默认选中 `标准`。
2. 点击 `标准` / `增强背景` 只保存选择，不自动重跑；点击 `手动调整` 先弹窗确认比例，避免用户误触后立刻启动耗时任务。
3. 如果选择方案与当前 `tts_loudness.profile` 不同，卡片显示小字：`已选择，点击“从此步继续”后生效。`
4. 如果选择方案与当前 `tts_loudness.profile` 相同，并且手动比例也一致，卡片显示小字：`当前结果已按此方案生成。`
5. `loudness_match` running 时按钮禁用或显示处理中状态，避免并发修改。
6. 按钮请求使用 CSRF header；失败时保留原选中状态并显示轻量错误提示。
7. 卡片同时标注本次已经生成结果实际采用的背景音逻辑，文案形如：`当前运行逻辑：标准`、`当前运行逻辑：增强背景`、`当前运行逻辑：手动 +50%`。当用户已选择新方案但尚未从此步继续时，这行仍显示旧结果实际采用的逻辑，选择状态行继续提示新方案待生效。

## 数据设计

任务状态新增顶层字段：

```json
{
  "loudness_profile": "standard | bg_boost | manual_boost",
  "loudness_manual_boost_pct": 50
}
```

`task.separation.tts_loudness` 新增本次运行实际参数：

```json
{
  "profile": "manual_boost",
  "manual_boost_pct": 50,
  "background_volume": 0.8,
  "effective_background_volume": 1.2,
  "background_boost": {
    "enabled": false,
    "mode": "auto",
    "fallback_reason": "profile_not_bg_boost"
  },
  "manual_boost": {
    "enabled": true,
    "boost_pct": 50,
    "standard_volume": 0.8,
    "raw_volume": 1.2,
    "effective_volume": 1.2,
    "max_volume": 1.8,
    "capped": false
  }
}
```

`effective_background_volume` 是本次混音实际传给 `mix_with_background()` 的值。`background_volume` 保留为全局/标准值，便于 UI 对比。运行时还要把同一值同步到 `task.separation.effective_background_volume`，让 A 兜底或跳过 B postmix 的 `compose` 路径也能使用增强后的背景音量，而不是重新读取全局设置。

如果增强模式无法测量 accompaniment 或 accompaniment 近似静音：

```json
{
  "enabled": false,
  "fallback_reason": "accompaniment_lufs_unavailable | accompaniment_near_silence"
}
```

此时运行时回退到标准背景音量，但 UI 仍保留用户选中的 `增强背景`，并提示“该素材背景音过弱，已按标准音量混入”。

`manual_boost` 不依赖 accompaniment LUFS 测量。只要 `manual_boost_pct` 合法，就按用户比例计算；即使 accompaniment 近似静音，也不额外自动推高到上限。

## 自动增强算法

常量：

- `BOOST_TARGET_GAP_LU = 10.0`
- `BOOST_MAX_BACKGROUND_VOLUME = 1.8`
- `BOOST_SILENCE_LUFS_THRESHOLD = -50.0`，沿用现有静音阈值

输入：

- `standard_volume`：当前全局 `settings.background_volume`
- `accompaniment_lufs`：分离出的 accompaniment integrated LUFS
- `tts_reference_lufs`：优先使用当前步骤预计 TTS 目标响度；A 兜底使用 `vocals_lufs`；B 算法使用反推前的 TTS 原始响度作为初始估计，并在 summary 中记录实际结果

计算：

```text
target_bg_lufs = tts_reference_lufs - BOOST_TARGET_GAP_LU
needed_gain_lu = target_bg_lufs - accompaniment_lufs
raw_volume = standard_volume * pow(10, needed_gain_lu / 20)
effective_volume = min(BOOST_MAX_BACKGROUND_VOLUME, max(standard_volume, raw_volume))
```

规则：

1. 增强模式只抬高背景，不降低背景；如果计算结果小于标准音量，使用标准音量。
2. `effective_volume` 不超过 `1.8`。
3. accompaniment 近似静音时不强行打到上限，避免把分离噪声放大。
4. B 算法和 A 兜底都使用同一个 `effective_volume`；如果 B 因偏差过大切 A，A 后续合成也继续使用同一个有效背景音量。
5. Summary 要记录增强计算过程，便于排查“为什么没有明显增强”。

## 手动增强算法

输入：

- `standard_volume`：当前全局 `settings.background_volume`
- `manual_boost_pct`：用户选择的整数百分比，范围 `10` 到 `100`

计算：

```text
manual_multiplier = 1 + manual_boost_pct / 100
raw_volume = standard_volume * manual_multiplier
effective_volume = min(BOOST_MAX_BACKGROUND_VOLUME, raw_volume)
```

示例：

- 标准音量 `0.80`，手动 `+10%` → `0.88`
- 标准音量 `0.80`，手动 `+50%` → `1.20`
- 标准音量 `0.80`，手动 `+100%` → `1.60`
- 标准音量 `1.20`，手动 `+100%` → 原始 `2.40`，按上限截断为 `1.80`

规则：

1. `manual_boost_pct` 必须是 `10` 到 `100` 的整数，且必须是 10 的倍数。
2. 手动增强不读取 `tts_reference_lufs`，不按素材自动判断。
3. 手动增强和自动增强共用 `BOOST_MAX_BACKGROUND_VOLUME = 1.8` 上限。
4. Summary 要记录 `manual_boost_pct`、`raw_volume`、`effective_volume` 和 `capped`。

## 后端接口

新增 Omni 路由：

```http
POST /api/omni-translate/<task_id>/loudness-profile
Content-Type: application/json

{"profile": "manual_boost", "manual_boost_pct": 50}
```

行为：

1. 必须登录并可访问该任务。
2. `profile` 只允许 `standard`、`bg_boost` 或 `manual_boost`。
3. `profile="manual_boost"` 时必须提供 `manual_boost_pct`，合法范围 `10` 到 `100`，且必须是 10 的倍数。
4. 写入任务顶层 `loudness_profile`；手动模式同时写入 `loudness_manual_boost_pct`。
5. 不自动启动 runner。
6. 返回当前选择、最新已应用方案、已保存手动比例和是否需要重跑：

```json
{
  "status": "ok",
  "profile": "manual_boost",
  "manual_boost_pct": 50,
  "applied_profile": "standard",
  "applied_manual_boost_pct": null,
  "needs_resume": true
}
```

现有 `/api/omni-translate/<task_id>/resume` 保持入口不变。用户点击 `loudness_match` 的“从此步继续”后，runner 读取最新 `task.loudness_profile`。

## 运行时设计

`_step_loudness_match()` 在读取分离结果和全局设置后解析 profile：

1. `profile = task.get("loudness_profile") or "standard"`
2. `manual_boost_pct = task.get("loudness_manual_boost_pct")`，仅 `manual_boost` 使用
3. `standard_volume = settings.background_volume`
4. `effective_volume = standard_volume`，除非 profile 为 `bg_boost` 且自动增强计算可用，或 profile 为 `manual_boost` 且手动比例合法
5. B 算法的 pre-mix、post-mix 都使用 `effective_volume`。
6. A 兜底本身只归一化 TTS，但必须把 `effective_volume` 写回 `separation`，后续 `compose` 现场 mix 时读取该值。

为避免反复切换方案导致 TTS 音频漂移：

1. 第一次进入新实现后的 `loudness_match` 时，为每个 variant 保存一份响度处理前源音频，例如 `loudness_match/source.<variant>.mp3`。
2. 每次重跑 `loudness_match` 前，先用该源音频覆盖 variant 的 `tts_audio_path`，再执行 B/A 算法。
3. 如果任务是旧任务且没有源音频备份，只能把当前 `tts_audio_path` 作为首次源音频；UI 不额外阻塞，但 summary 记录 `source_backup_origin="current_tts_audio"`。
4. 从 `tts` 或更早步骤恢复时，清理旧的 loudness source backup，下一轮 TTS 产物重新成为源音频。

## UI 展示修正

当前卡片内部算法展示必须改为读取 `primary.algorithm || tl.algorithm`：

- `B`：显示 `算法 B：整体对整体（mp4 vs 原视频整体）`
- `A`：显示 `算法 A：人声对人声（TTS vs vocals）`
- `A_after_B_excess_deviation`：显示 `算法 A：B 整体偏差过大后兜底`
- `A_after_B_failure`：显示 `算法 A：B 执行失败后兜底`

当实际算法不是 B 时，不再渲染 B 的 `pre_amix_lufs/post_amix_lufs` 为主指标；若存在 B 失败前的诊断数据，可放到折叠详情里。

## 验证策略

实现后需要覆盖：

1. 纯函数测试：自动增强背景音量会抬高但不超过 `1.8`，静音 accompaniment 回退标准音量。
2. 纯函数测试：手动增强 `+10%`、`+50%`、`+100%` 按标准音量线性放大并受 `1.8` 上限约束。
3. Runtime 测试：`loudness_profile=bg_boost` 或 `manual_boost` 时 `_step_loudness_match()` 把 `effective_background_volume` 写入 summary，并传给 B/A mix。
4. Runtime 测试：反复从 `loudness_match` 恢复时先还原 source backup，不在已 loudnorm 音频上叠加。
5. Route 测试：`POST /api/omni-translate/<id>/loudness-profile` 接受三种 profile；`manual_boost` 接受合法 `manual_boost_pct` 并拒绝非法比例；接口不自动调用 runner。
6. Template/JS 静态测试：页面包含 `标准`、`增强背景`、`手动调整`、手动比例弹窗、小字说明、CSRF header、未生效提示和实际算法展示修正。
7. 回归测试：`tests/test_omni_translate_routes.py`、`tests/test_translate_detail_shell_templates.py`、相关 runtime/audio loudness 测试通过。
8. Web 验证：起 dev server 后，未登录 `/omni-translate/<id>` 返回 302，不是 500。

## 发布与兼容

1. 新字段都在 `state_json` 内，不需要 DB migration。
2. 没有 `loudness_profile` 的历史任务默认等价 `standard`；没有 `loudness_manual_boost_pct` 的历史任务不进入手动模式。
3. 旧任务没有响度源音频备份时，第一次切换 profile 不保证回到最原始 TTS；从 `tts` 重跑后即可获得干净源音频。
4. 多语种生产链路不受影响。
