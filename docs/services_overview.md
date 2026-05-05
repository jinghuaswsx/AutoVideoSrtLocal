# 本机 GPU 服务总览（172.30.254.12）

本机（Windows + RTX 3060 12GB）当前对外提供 **2 个 GPU 服务**，统一从 80 端口
反向代理。VACE 后端**代码已就位但未启动**，等阶段 2 实测后再上线。

---

## 1. 部署清单

| 端口 | 服务 | 部署目录 | 启动脚本 | 状态 |
|---|---|---|---|---|
| **80** | Caddy 网关 | `G:\gateway\` | `G:\gateway\start.bat` | ✅ 在跑 |
| **8081** | audio_separator (人声分离) | `G:\audio\` | `G:\audio\start.bat` | ✅ 在跑 |
| **8082** | subtitle (VSR 硬字幕) | `G:\subtitle\` | `G:\subtitle\start.bat` | ✅ 在跑 |
| 8083 | vace (VACE 去字幕) | — | — | ⏸ **未启动**（代码 ready，阶段 2 验证后再上） |

> 当前未做开机自启。重启后要手动跑这三个 `start.bat`（顺序无所谓）。
> 后续可考虑 NSSM 注册为 Windows Service。

### 拓扑

```
                   Caddy :80 (LAN 入口 172.30.254.12)
                   ┌──────────┬──────────┬─────────┐
              /separate/* /subtitle/* /vace/*    handle /
                   ▼          ▼          ▼
                 :8081      :8082    :8083 (预留)
                 audio    subtitle    vace
```

---

## 2. `/separate/*` — 音频人声/伴奏分离

基于 [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator)。

| 方法 | URL | 说明 |
|---|---|---|
| GET | `/separate/health` | 健康 + 队列 + 缓存 + GPU |
| GET | `/separate/queue` | 队列深度速查 |
| GET | `/separate/models` | 全部模型清单 |
| GET | `/separate/presets` | 9 个 ensemble preset |
| GET | `/separate/docs` | Swagger UI |
| **POST** | `/separate/run` | 上传音频 → JSON 元信息（**原 `/separate` 改名**） |
| **POST** | `/separate/download` | 上传音频 → ZIP（vocals + instrumental） |

**Form 参数**（`/run` 与 `/download` 通用）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `file` | multipart | 必填 | 音频文件，**只接受 mp3**（wav 会 500，业务客户端自动转） |
| `ensemble_preset` | str | `vocal_balanced` | 见 `/separate/presets` |
| `model_filename` | str | — | 单模型名（替代 ensemble） |
| `output_format` | str | `WAV` | `WAV / FLAC / MP3 / OGG / M4A` |
| `single_stem` | str | — | `Vocals` / `Instrumental` 仅出一路 |

```python
import requests
API = "http://172.30.254.12"

# 同步阻塞，自动排队，缓存命中 0ms
with open("song.mp3", "rb") as f:
    r = requests.post(
        f"{API}/separate/download",
        files={"file": f},
        data={"ensemble_preset": "vocal_balanced"},
        timeout=300,
    )
# r.content 是 zip：input_xxx_(Vocals)_..._.wav + input_xxx_(Instrumental)_..._.wav
```

业务客户端：[`appcore/audio_separation_client.py`](../appcore/audio_separation_client.py)
（自动 wav→mp3 + 重试 + zip 解包）。

---

## 3. `/subtitle/*` — 视频硬字幕去除（VSR）

基于 [video-subtitle-remover v1.1.1](https://github.com/YaoFANGUK/video-subtitle-remover)。

| 方法 | URL | 说明 |
|---|---|---|
| GET | `/subtitle/health` | 健康 + GPU |
| GET | `/subtitle/queue` | 队列 |
| GET | `/subtitle/algorithms` | 3 种算法说明 |
| GET | `/subtitle/docs` | Swagger UI |
| **POST** | `/subtitle/remove` | 上传视频 → JSON 元信息 |
| **POST** | `/subtitle/remove/download` | 上传视频 → MP4（已去字幕） |

**Form 参数**：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `file` | multipart | 必填 | 视频文件（mp4/mov 等） |
| `algorithm` | str | `sttn` | `sttn`（真人/快） / `lama`（动画/中速） / `propainter`（高速运动/慢；max_load=25） |
| `sub_area` | str | — | `"ymin,ymax,xmin,xmax"`，省略则自动检测全字幕 |

```python
with open("video.mp4", "rb") as f:
    r = requests.post(
        f"{API}/subtitle/remove/download",
        files={"file": f},
        data={"algorithm": "sttn"},
        timeout=1800,   # 长视频务必拉长
    )
with open("out.mp4", "wb") as o:
    o.write(r.content)
```

---

## 4. `/vace/*` — VACE 本地去字幕（**未启动**）

VACE 后端代码已落库（feature 分支 `feature/vace-windows-backend`），处于
**阶段 1 完成**态：骨架 + dry-run + 测试 + 文档全齐，但**不启动 HTTP 服务、
也不实际调用 VACE 推理**，等阶段 2 装完 VACE 仓库 + 模型后再实测启用。

- 代码：`appcore/vace_subtitle/` + `scripts/remove_subtitle_vace.py`（仅 feature 分支）
- 文档：[`docs/vace_windows_backend.md`](vace_windows_backend.md)（仅 feature 分支）
- 测试：`tests/test_vace_subtitle/` 85 passed + 1 e2e skipped
- HTTP 路由：[`tools/gateway/Caddyfile`](../tools/gateway/Caddyfile) 里 `/vace/*` 段已注释占位
- 端口 8083：未占用，**当前不要启动**

启用流程（阶段 2/3，**暂不操作**）：

1. 装 VACE 仓库 + venv + Wan2.1-VACE-1.3B 模型（按 `docs/vace_windows_backend.md` §3）
2. 跑 `python scripts\remove_subtitle_vace.py --dry-run ...` 确认环境
3. 真实跑 5 秒 1080P 视频验证 ROI 合成保真度
4. 接 OCR 动态 mask + chunk overlap（v2 路线图）
5. 包装为 `tools/vace/api_server.py` 跑 :8083 + 取消 Caddyfile `/vace/*` 注释 + `caddy reload`

> ⚠️ 当前 `/vace/health` 返回 HTTP 200 是 Caddy 根 handle 兜底，**不代表服务在跑**。

---

## 5. 共用细节

### GPU 配额（RTX 3060 12GB）

每个服务进程 `torch.cuda.set_per_process_memory_fraction(0.5)` = 自留 6 GB 上限。

| 并发组合 | 总显存预估 | 结果 |
|---|---|---|
| audio idle + subtitle 推理 STTN | 7-9 GB | ✅ 稳 |
| audio 推理 + subtitle 推理 STTN/LAMA | 8-12 GB | ⚠️ 临界 |
| audio 推理 + subtitle ProPainter (max_load=25) | 12-14 GB | ⚠️ 边缘，OOM 后客户端重试通过 |

VACE 接入时（阶段 3）必须加跨进程 GPU lock 避免三服务同时推理。

### 缓存

每个服务进程 1 小时 in-memory MD5 缓存。同文件 + 同参数 → 0ms 返回。

### 并发模型

每个服务进程一个 `asyncio.Lock()` 串行 GPU 推理。HTTP 入站并发，自动排队。
不实现跨服务进程互斥（v2 计划）。

### 防火墙

Windows 入站规则 `GPU-Gateway-80`（80 端口 TCP）已加，外部网段直接可达
`http://172.30.254.12/{separate,subtitle,vace}/...`。

---

## 6. 启动 / 重启 / 杀进程

```powershell
# 启动（顺序无所谓）
G:\audio\start.bat
G:\subtitle\start.bat
G:\gateway\start.bat

# 找占用端口的 PID
netstat -ano | findstr ":8081 "
netstat -ano | findstr ":8082 "
netstat -ano | findstr ":80 "

# 杀某个 PID
taskkill /F /PID <PID>
```

**当前未做开机自启。** 电脑重启后要手动跑三个 `start.bat`。后续可
考虑 NSSM 把它们注册成 Windows Service（自启 + 故障重启）。

---

## 7. 仓库结构

| 路径 | 内容 |
|---|---|
| [`tools/gateway/`](../tools/gateway/) | Caddyfile + start.bat + install_caddy.bat + README |
| [`tools/audio_separator/`](../tools/audio_separator/) | audio 服务端 api_server.py + start.bat + README |
| [`tools/subtitle/`](../tools/subtitle/) | subtitle 服务端 api_server.py + start.bat + README |
| [`appcore/audio_separation_client.py`](../appcore/audio_separation_client.py) | 业务调 audio 的客户端（自动 wav→mp3 + 重试 + zip 解包） |
| `appcore/vace_subtitle/` | VACE backend 包（**仅 feature/vace-windows-backend 分支**） |
| `scripts/remove_subtitle_vace.py` | VACE CLI（**仅 feature 分支**） |
| `docs/vace_windows_backend.md` | VACE 部署/使用手册（**仅 feature 分支**） |
| `tests/audio/test_api.py` | audio 集成测试（在线服务跑） |
| `tests/test_audio_separation_client.py` | client mock 测试（20 passed） |
| `tests/test_vace_subtitle/` | VACE 单元测试 85 passed（**仅 feature 分支**） |

每个服务子目录的 README 是该服务的权威文档；本文是**全局总览**。

---

## 8. 客户端最小调用 vector

```python
import requests
API = "http://172.30.254.12"   # 80 端口默认无需写

# 健康检查
print(requests.get(f"{API}/separate/health").json())
print(requests.get(f"{API}/subtitle/health").json())

# 音频分离 → JSON 元信息
with open("song.mp3", "rb") as f:
    print(requests.post(f"{API}/separate/run",
                        files={"file": f},
                        timeout=300).json())

# 音频分离 → 直接拿 zip
with open("song.mp3", "rb") as f:
    r = requests.post(f"{API}/separate/download",
                      files={"file": f}, timeout=300)
open("stems.zip", "wb").write(r.content)

# 去字幕 → JSON
with open("video.mp4", "rb") as f:
    print(requests.post(f"{API}/subtitle/remove",
                        files={"file": f},
                        data={"algorithm": "sttn"},
                        timeout=1800).json())

# 去字幕 → 直接拿 mp4
with open("video.mp4", "rb") as f:
    r = requests.post(f"{API}/subtitle/remove/download",
                      files={"file": f},
                      data={"algorithm": "sttn", "sub_area": "850,1000,200,1720"},
                      timeout=1800)
open("video_no_sub.mp4", "wb").write(r.content)
```

---

## 9. 路线图

| 项 | 状态 | 备注 |
|---|---|---|
| Caddy 网关 :80 + path-based routing | ✅ 已上线 | |
| audio_separator + subtitle 服务化 | ✅ 已上线 | |
| 防火墙 + LAN 暴露 | ✅ 已加 | `GPU-Gateway-80` 规则 |
| 跨进程 GPU lock | ❌ 未做 | VACE 接入前必须加 |
| 三服务 NSSM 自启 | ❌ 未做 | 重启后手动 start.bat |
| **VACE backend 骨架** | ✅ **阶段 1 完成（feature 分支）** | dry-run + 测试通；**不启动** |
| VACE 真实推理验证 | ⏸ 阶段 2 | 装 VACE repo + 1.3B 模型后启动 |
| VACE OCR 动态 mask + chunk overlap | ⏸ 阶段 2 | |
| VACE HTTP 服务化 + Caddy 接入 | ⏸ 阶段 3 | 取消 `/vace/*` 注释 + `caddy reload` |
