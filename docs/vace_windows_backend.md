# VACE Windows 本地后端（去硬字幕）

把 [ali-vilab/VACE](https://github.com/ali-vilab/VACE) 接入本仓库，作为本地 GPU
**硬字幕移除**的可选后端。本文是 Windows + RTX 3060 12GB 的部署/使用手册。

代码入口：[`appcore/vace_subtitle/`](../appcore/vace_subtitle/) + CLI
[`scripts/remove_subtitle_vace.py`](../scripts/remove_subtitle_vace.py)。

---

## 1. 适用 / 不适用

**适用**：
- Windows 本地开发机；RTX 3060 12GB；
- 1080P 输入视频，输出仍为 1080P；
- **固定底部硬字幕**（默认 bbox: `0, ~778, 1920, ~1026`）；
- 短视频或可分段长视频。

**不适用**：
- 软字幕（SRT/ASS）— 直接关字幕轨道即可；
- 字幕压在人脸/手部/关键物体上 — VACE 难免推测出非原状内容；
- 期望 100% 还原被字幕遮挡的原始像素 — 物理不可能；
- 超长视频一次性全量 VACE — chunk 化才能稳定；
- RTX 3060 上原生全画幅 1080P 直跑 VACE — 显存爆。

---

## 2. 为什么 1080P 走 ROI 合成模式（默认）

VACE 模型在 480P/720P 尺寸上稳定。RTX 3060 12GB 显存直跑 1080P 几乎必爆。

ROI 合成的逻辑：
1. **保留**原视频 1080P；
2. 只裁出字幕区域 + 上下文 (`crop`)，缩放到 VACE 友好尺寸（默认 832x480 内）；
3. VACE inpainting 修这一小条；
4. 把 VACE 输出**逆变换**回原始 ROI 尺寸；
5. 用羽化 mask **只**把字幕 bbox 区域合成回原视频；
6. 非字幕区域**字节级保留**原始像素。

→ 12GB 卡稳；非字幕画质零损失；字幕位置自然过渡。

---

## 3. Windows 安装步骤

### 3.1 装 Python 3.10 独立 venv

VACE 用的依赖（torch/diffusers/wan）和本仓库其他模块依赖冲突，必须独立 venv。

```powershell
cd C:\AI
git clone https://github.com/ali-vilab/VACE.git
cd C:\AI\VACE
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
pip install wan@git+https://github.com/Wan-Video/Wan2.1
```

### 3.2 下载模型

至少装 1.3B 模型（约 11GB），14B 是可选实验项（80GB+）：

```
C:\AI\VACE\models\Wan2.1-VACE-1.3B
C:\AI\VACE\models\Wan2.1-VACE-14B   # 可选
```

按 VACE 仓库 README 下载到上述路径。

### 3.3 设置环境变量（PowerShell 用户配置）

```powershell
[Environment]::SetEnvironmentVariable("VACE_REPO_DIR",   "C:\AI\VACE", "User")
[Environment]::SetEnvironmentVariable("VACE_PYTHON_EXE", "C:\AI\VACE\.venv\Scripts\python.exe", "User")
[Environment]::SetEnvironmentVariable("VACE_MODEL_DIR",  "C:\AI\VACE\models\Wan2.1-VACE-1.3B", "User")
[Environment]::SetEnvironmentVariable("VACE_MODEL_NAME", "vace-1.3B", "User")
[Environment]::SetEnvironmentVariable("VACE_SIZE",       "480p", "User")
[Environment]::SetEnvironmentVariable("VACE_PROFILE",    "rtx3060_safe", "User")
# 可选：
[Environment]::SetEnvironmentVariable("VACE_RESULTS_DIR", "C:\Temp\vace_jobs", "User")
[Environment]::SetEnvironmentVariable("VACE_TIMEOUT_SEC", "1800", "User")
[Environment]::SetEnvironmentVariable("FFMPEG_PATH",     "ffmpeg", "User")
[Environment]::SetEnvironmentVariable("FFPROBE_PATH",    "ffprobe", "User")
```

新开一个 PowerShell 窗口验证：

```powershell
echo $env:VACE_REPO_DIR
echo $env:VACE_PYTHON_EXE
& $env:VACE_PYTHON_EXE -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

期望：`True NVIDIA GeForce RTX 3060`。

### 3.4 装 FFmpeg / ffprobe

本仓库其他模块也用，本机已装的话跳过。否则下载
[gyan.dev full build](https://www.gyan.dev/ffmpeg/builds/)，把 `bin/` 加 PATH。

---

## 4. Profile 速查

| Profile | model | size | frame_num | steps | 说明 |
|---|---|---|---|---|---|
| `rtx3060_safe` (默认) | 1.3B | 480p | 41 | 20 | 12GB 卡稳跑；chunk≈2.7s |
| `rtx3060_balanced` | 1.3B | 480p | 81 | 25 | chunk≈4.8s；偶尔 OOM 自动 fallback |
| `rtx3060_quality_experimental` | 14B | 720p | 41 | 25 | 容易 OOM；fallback → safe |

`frame_num` 必须满足 4n+1。OOM 自动降级链：
`14B/720p → 1.3B/480p → frame=41 → steps=20 → chunk=2.5s`。

---

## 5. 使用示例

### 5.1 dry-run（不调 VACE，只打印计划）

```powershell
python scripts\remove_subtitle_vace.py `
  --input "D:\videos\input_1080p.mp4" `
  --output "D:\videos\output_1080p_vace.mp4" `
  --bbox "0,780,1920,1025" `
  --profile "rtx3060_safe" `
  --dry-run
```

→ 看 `D:\videos\output_1080p_vace.mp4.vace.json` 里 chunks 切分、VACE 命令是否合理。

### 5.2 真实运行 1080P 去字幕（rtx3060_safe）

```powershell
python scripts\remove_subtitle_vace.py `
  --input "D:\videos\input_1080p.mp4" `
  --output "D:\videos\output_1080p_vace.mp4" `
  --bbox "0,780,1920,1025" `
  --profile "rtx3060_safe" `
  --prompt "clean natural video background, no subtitles, no text, no watermark"
```

### 5.3 较大块 + 更高质量

```powershell
python scripts\remove_subtitle_vace.py `
  --input input.mp4 --output output.mp4 `
  --profile "rtx3060_balanced" `
  --chunk-seconds 4.8
```

### 5.4 实验级 14B 模型（90% 概率 OOM 后自动降级）

```powershell
python scripts\remove_subtitle_vace.py `
  --input input.mp4 --output output.mp4 `
  --profile "rtx3060_quality_experimental"
```

### 5.5 不改 bbox（自动底部条）

省略 `--bbox`：默认 `0, round(h*0.72), w, round(h*0.95)`，1080P 上 ≈ `0,778,1920,1026`。

### 5.6 调试时保留中间文件

```powershell
python scripts\remove_subtitle_vace.py --input ... --output ... --keep-workdir
```

→ chunk 文件落 `VACE_RESULTS_DIR\vace_<stem>_<rand>\chunk_0000\` 等。

---

## 6. CLI 完整参数

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `--input` | path | 必填 | 输入视频 |
| `--output` | path | 必填 | 输出 mp4 |
| `--bbox` | `x1,y1,x2,y2` | auto 底部 | 字幕区域（原视频坐标） |
| `--mask` | path | 无 | （预留）OCR 动态 mask；v1 不用 |
| `--mode` | enum | `roi_1080` | `roi_1080` / `proxy_720`（v2） / `native_vace`（需 `--allow-native-vace`） |
| `--profile` | enum | `rtx3060_safe` | profile 名 |
| `--prompt` | str | clean natural… | VACE prompt |
| `--chunk-seconds` | float | profile 默认 | 切片长度 |
| `--context-top-px` | int | 128 | bbox 上方上下文 |
| `--context-bottom-px` | int | 48 | bbox 下方上下文 |
| `--dilation-px` | int | 8 | mask 膨胀 |
| `--feather-px` | int | 12 | mask 羽化 |
| `--keep-workdir` | flag | false | 保留中间文件 |
| `--dry-run` | flag | false | 仅规划，不调 VACE |
| `--allow-native-vace` | flag | false | 解锁 `--mode native_vace`（不推荐 3060） |
| `--log-level` | enum | INFO | 日志级别 |

---

## 7. 输出

`<output>.vace.json` 是任务清单，含：

- 输入元数据（width / height / fps / duration）；
- bbox + crop bbox；
- profile / model / size / frame_num / sample_steps / prompt；
- 每个 chunk 的：
  - 时间窗（start / duration）；
  - 中间文件路径（original_chunk / crop_chunk / vace_input / vace_output / composited_chunk）；
  - 完整 VACE 命令（list[str]）；
  - returncode / elapsed_seconds / status / error；
- final mux 步骤；
- 总状态 (`pending` / `running` / `done` / `failed` / `dry-run`)。

---

## 8. 常见错误

| 现象 | 原因 + 处置 |
|---|---|
| `VaceConfigError: VACE_REPO_DIR (got None)` | 没 set env；按 §3.3 设置后**新开 PowerShell** |
| `VaceConfigError: VACE_PYTHON_EXE (got ...) — point at .venv\Scripts\python.exe` | 路径写错或 venv 没装 |
| `VaceConfigError: VACE_MODEL_DIR ... should contain Wan2.1-VACE weights` | 模型没下载 |
| `RuntimeError: CUDA out of memory` | profile 太重；自动 fallback 一次后仍 OOM 时**手动**降到 `rtx3060_safe` 并 `--frame-num 41 --chunk-seconds 2.5` |
| `FFmpegError: command failed (rc=1) ... ffprobe -v error ...` | 输入视频损坏或路径含特殊字符；先 `ffprobe -i input.mp4` 单独跑 |
| 输出无声音 | 原视频本身无音轨 — `mux_audio_from_source` 用了 optional `1:a:0?`，无音轨即输出无声 |
| 字幕区域有"幽灵残影" | feather/dilation 太小；试 `--feather-px 18 --dilation-px 12` |
| 字幕外画面被改 | bbox 选错或 context 太大；缩 context 或确认 bbox |
| Windows 路径报错 `OSError: [WinError 123]` | 路径含中文/空格 + 编码异常；用 ASCII 路径或确保 stdout/file 用 utf-8 |

---

## 9. 测试

不需要装 VACE 的单元测试（85 用例 + 1 e2e skip）：

```powershell
cd <repo>
pytest tests\test_vace_subtitle -v
```

装 VACE 后 e2e smoke：

```powershell
$env:VACE_REPO_DIR = "C:\AI\VACE"
$env:VACE_PYTHON_EXE = "C:\AI\VACE\.venv\Scripts\python.exe"
$env:VACE_MODEL_DIR = "C:\AI\VACE\models\Wan2.1-VACE-1.3B"
pytest tests\test_vace_subtitle\test_e2e_smoke.py -v
```

---

## 10. 内部架构

```
appcore/vace_subtitle/
├── __init__.py          # 导出 VaceWindowsSubtitleRemover
├── config.py            # PROFILES + VaceEnv + env 解析 + OOM fallback
├── bbox.py              # 默认 bbox + crop + scale + 坐标三映射
├── chunking.py          # plan_chunks (4n+1 + chunk_seconds)
├── ffmpeg_io.py         # probe + cut/crop/concat/mux (list[str], 不 shell=True)
├── mask.py              # build_feather_mask (cv2 懒导入)
├── composite.py         # 逐帧 alpha-blend ROI 回原视频
├── vace_subprocess.py   # build_command + run_invocation + OOM 检测 + probe_help
├── manifest.py          # ChunkRecord + Manifest 读写
└── remover.py           # VaceWindowsSubtitleRemover (编排)

scripts/remove_subtitle_vace.py   # 独立 CLI（不依赖 main.py 的 web 启动）
tests/test_vace_subtitle/         # 7 个测试套
```

设计纪律：
- import 时无副作用（不导入 torch / 不查 GPU / 不读 VACE 仓库）；
- 所有 subprocess 用 `list[str]`，**禁止** `shell=True`；
- 路径用 `pathlib.Path`，Windows 反斜杠/空格无障碍；
- 临时文件统一在 `_make_workdir` 集中，`--keep-workdir` 保留；
- 单进程 / 单 worker，避免 GPU 抢占。

---

## 11. 当前限制与下一步

**v1 已实现**：
- ✅ ROI 1080P 默认模式；
- ✅ 固定 bbox（手传 + auto 默认）；
- ✅ chunk 切片 + 顺序处理；
- ✅ feather mask + alpha-blend；
- ✅ OOM 一次自动 fallback；
- ✅ 中间文件保留 / 失败时 manifest 落盘；
- ✅ dry-run + 完整测试覆盖（不依赖 VACE 实装）。

**v1 限制**：
- ❌ 无 chunk overlap（接缝处偶尔可见，长视频较明显）；
- ❌ 无 OCR 动态 mask（字幕位置变化的视频要手动多次跑）；
- ❌ `proxy_720` 模式仅占位；
- ❌ 无任务队列接入（不能从 web UI 触发）；
- ❌ 失败重启没有 chunk 级断点续跑（已有 manifest 数据，加上即可）。

**v2 路线图**：
1. **OCR 动态 mask**：用 PaddleOCR 逐帧检测字幕 bbox，按帧合成 mask，喂 `--mask`；
2. **chunk overlap + 淡入淡出**：相邻 chunk 重叠 N 帧，融合 fade；
3. **task 系统接入**：仿 `appcore/subtitle_removal_runtime_vod.py` 加一个 `subtitle_removal_runtime_vace.py`，让 web UI 可选；
4. **Caddy `/vace/*`**：HTTP 服务化（`tools/vace/api_server.py`），跟 audio/subtitle 网关并列。

---

## 12. 合规说明

**仅处理你有权处理的视频素材**（自有素材、公有领域、获得授权的内容）。
对他人版权作品做去字幕是侵权行为，本工具不为此场景背书。
