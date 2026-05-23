# Audio Separator API 使用文档

## 概述

GPU 加速音频人声/伴奏分离服务，运行在内网 `172.30.254.12:80`。
基于 [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) + RTX 3060 (12GB)。

### 文件位置

| 路径 | 说明 |
|------|------|
| `G:\audio\api_server.py` | FastAPI 服务主程序 |
| `G:\audio\models\` | 已下载的模型文件 |
| `G:\audio\output\` | 临时输出目录 |
| `G:\audio\logs\` | 服务日志 |
| `G:\audio\venv312\` | Python 3.12 虚拟环境 |
| `G:\audio\start_api.bat` | 启动脚本 |
| `G:\audio\docs\audio_separator_api.md` | **本文档** |

### 资源分配

| 资源 | 分配 |
|------|------|
| GPU 显存 | 90%（~10.8 GB / 12 GB） |
| CPU 核心 | 50%（前 10 个逻辑核心） |
| CPU 优先级 | HIGH |
| 模型缓存 | 热加载，最多缓存 3 个模型实例 |

---

## 快速开始

```bash
# 健康检查
curl http://172.30.254.12/health

# 最简调用：上传文件分离（默认使用 vocal_balanced 集成预设）
curl -X POST http://172.30.254.12/separate \
  -F "file=@song.mp3"

# 下载分离结果 ZIP
curl -X POST http://172.30.254.12/separate/download \
  -F "file=@song.mp3" \
  -o separated.zip
```

---

## API 端点

### `GET /health`

健康检查 + 系统状态。

**响应示例：**
```json
{
  "status": "ok",
  "cuda_available": true,
  "cuda_device": "NVIDIA GeForce RTX 3060",
  "gpu_memory_90pct": "10.8 GB",
  "default_preset": "vocal_balanced",
  "cpu_affinity": "[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]"
}
```

---

### `GET /models`

列出所有可用单模型（83 个）。通常不需要直接使用，建议用集成预设。

---

### `GET /presets`

列出所有集成预设（Ensemble Presets），这是推荐的调用方式。

| 预设名 | 说明 | 适用场景 |
|--------|------|---------|
| `vocal_balanced` **(默认)** | Resurrection + Beta 6X (avg_fft) | 人声分离最佳综合质量 |
| `vocal_clean` | Revive V2 + FT2 bleedless (min_fft) | 最小乐器串扰 |
| `vocal_full` | Revive 3e + becruily (max_fft) | 最大人声捕捉（含和声） |
| `vocal_rvc` | Beta 6X + Gabox FV4 (avg_wave) | AI 声音训练数据 |
| `instrumental_clean` | FV7z + Resurrection Inst (uvr_max_spec) | 最干净伴奏，人声残留最少 |
| `instrumental_full` | v1e+ + becruily inst (uvr_max_spec) | 最大乐器保留 |
| `instrumental_balanced` | INSTV8 + Resurrection Inst (uvr_max_spec) | 伴奏综合平衡 |
| `karaoke` | 3 模型集成 (avg_wave) | 去除主唱人声 |

---

### `POST /separate`

上传音频，返回分离结果元数据。

**参数：**

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `file` | File | **必填** | 音频/视频文件 |
| `ensemble_preset` | Form | `null` → 默认 `vocal_balanced` | 集成预设名，见 `/presets` |
| `model_filename` | Form | `null` | 单模型文件名（预设未设置时生效） |
| `output_format` | Form | `WAV` | WAV / FLAC / MP3 / OGG / M4A |
| `single_stem` | Form | `null` | 只输出指定音轨：`Vocals` / `Instrumental` |

**响应：**
```json
{
  "status": "ok",
  "duration_seconds": 16.37,
  "input_file": "song.mp3",
  "input_size_mb": 4.3,
  "preset": "vocal_balanced",
  "model": "default",
  "output_format": "WAV",
  "stems": [
    "input_abc123_(Instrumental)_preset_vocal_balanced",
    "input_abc123_(Vocals)_preset_vocal_balanced"
  ],
  "output_filenames": [
    "input_abc123_(Instrumental)_preset_vocal_balanced.wav",
    "input_abc123_(Vocals)_preset_vocal_balanced.wav"
  ]
}
```

**调用示例：**
```bash
# 使用 vocal_balanced 预设（默认）
curl -X POST http://172.30.254.12/separate \
  -F "file=@song.mp3"

# 指定集成预设
curl -X POST http://172.30.254.12/separate \
  -F "file=@song.mp3" \
  -F "ensemble_preset=instrumental_clean"

# 只提取人声
curl -X POST http://172.30.254.12/separate \
  -F "file=@song.mp3" \
  -F "ensemble_preset=vocal_balanced" \
  -F "single_stem=Vocals"

# 输出 MP3 格式
curl -X POST http://172.30.254.12/separate \
  -F "file=@song.mp3" \
  -F "output_format=MP3"

# 使用单模型（不推荐，质量不如集成预设）
curl -X POST http://172.30.254.12/separate \
  -F "file=@song.mp3" \
  -F "model_filename=model_bs_roformer_ep_317_sdr_12.9755.ckpt"
```

---

### `POST /separate/download`

上传音频，直接下载 ZIP 压缩包。

**参数：** 同 `/separate`

**响应头：**
- `X-Separation-Time`: 分离耗时（秒）
- `X-Stems`: 输出的音轨名称列表（逗号分隔）

**调用示例：**
```bash
# 下载分离结果
curl -X POST http://172.30.254.12/separate/download \
  -F "file=@song.mp3" \
  -o song_separated.zip

# 解压后得到：
#   song_(Instrumental)_preset_vocal_balanced.wav
#   song_(Vocals)_preset_vocal_balanced.wav
```

---

### `POST /prewarm`

预加载模型到显存。服务启动时已自动加载默认预设，一般不需要手动调用。

```bash
curl -X POST http://172.30.254.12/prewarm \
  -F "ensemble_preset=instrumental_clean"
```

---

## Python 调用示例

```python
import requests

API = "http://172.30.254.12"

# 健康检查
r = requests.get(f"{API}/health")
print(r.json())

# 分离音频
with open("song.mp3", "rb") as f:
    r = requests.post(
        f"{API}/separate",
        files={"file": f},
        data={
            "ensemble_preset": "vocal_balanced",
            "output_format": "WAV",
        },
    )

result = r.json()
print(f"分离耗时: {result['duration_seconds']}s")
print(f"音轨: {result['stems']}")
```

---

## 性能基准

### 单模型 (BS-Roformer) — 速度优先

| 音频时长 | 分离耗时 | 实时倍率 |
|---------|---------|---------|
| 30 秒 | 5.23s | 0.174x (≈5.7x) |
| 60 秒 | 7.29s | 0.122x (≈8.2x) |
| 120 秒 | 13.69s | 0.114x (≈8.8x) |

### 集成预设 `vocal_balanced` — 质量优先（默认）

| 音频时长 | 分离耗时 | 实时倍率 |
|---------|---------|---------|
| 25.6 秒 | 16.37s | 0.640x (≈1.6x) |
| 60 秒 | ~38s | ~0.63x (≈1.6x) |

### 集成预设 `instrumental_clean` — 高质量伴奏

| 音频时长 | 分离耗时 | 实时倍率 |
|---------|---------|---------|
| 25.6 秒 | 11.46s | 0.448x (≈2.2x) |

---

## 模型选择建议

| 目标 | 推荐预设 |
|------|---------|
| 提取人声用于训练/分析 | `vocal_balanced`（默认） |
| 提取最干净人声（无乐器串扰） | `vocal_clean` |
| 提取伴奏 | `instrumental_clean` |
| 保留最多乐器细节的伴奏 | `instrumental_full` |
| 快速处理长音频 | 单模型 `model_bs_roformer_ep_317_sdr_12.9755.ckpt` |
| 制作卡拉 OK（去主唱） | `karaoke` |

---

## Swagger 交互式文档

浏览器打开：
```
http://172.30.254.12/docs
```

可以在网页上直接上传文件测试。
