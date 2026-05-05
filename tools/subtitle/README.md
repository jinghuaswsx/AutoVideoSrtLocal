# 视频去字幕外部 API 服务

基于 [video-subtitle-remover (VSR) v1.1.1](https://github.com/YaoFANGUK/video-subtitle-remover) 的 GPU 加速本地硬字幕去除服务。

## 部署位置

- **运行目录**: `G:\subtitle\`
- **服务地址**: `http://172.30.254.12:82`
- **服务端口**: 82
- **GPU**: NVIDIA RTX 3060 (12GB, 限制 90%)
- **CPU**: 50% 逻辑核心 + HIGH 优先级

## 架构

```
客户端 POST /remove (timeout=1800s)
  → 服务端计算 MD5 → 检查 1h 内存缓存
  → 命中则 0ms 返回缓存
  → 未命中则 asyncio.Lock 排队等 GPU
  → GPU 空闲 → 同步去字幕 → 写入缓存 → 返回结果
```

## 依赖

| 软件 | 位置 |
|------|------|
| Python 3.12 | `G:\subtitle\Python\` (整合包内置) |
| VSR 业务代码 | `G:\subtitle\resources\backend\` |
| 模型文件 | `G:\subtitle\resources\backend\models\` (~3GB) |
| ffmpeg | `G:\subtitle\resources\backend\ffmpeg\win_x64\ffmpeg.exe` |
| 第三方库 | `G:\subtitle\Python\Lib\site-packages\`（首次部署时从 `opt/packages` 装入） |

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET`  | `/health` | 健康检查 + 队列/缓存状态 |
| `GET`  | `/queue` | 队列深度速查 |
| `GET`  | `/algorithms` | 3 种算法（sttn / lama / propainter） |
| `GET`  | `/docs` | Swagger 交互式文档 |
| `POST` | `/remove` | 提交去字幕任务，返回 JSON 元信息 |
| `POST` | `/remove/download` | 提交并下载处理后的 MP4 |

### 算法选择

| 算法 | 适用场景 | 速度 | 显存 |
|------|---------|------|------|
| `sttn` (默认) | 真人/带货短视频 | 快 | 低 |
| `lama` | 动画/PPT/静态背景 | 中 | 低 |
| `propainter` | 高速运动/剧烈晃动 | 慢 | 高（12GB 卡 1080p 已经压到 max_load=40） |

### 客户端调用

```python
import requests

API = "http://172.30.254.12:82"

# 健康检查
print(requests.get(f"{API}/health").json())

# 去字幕（默认 STTN，自动检测字幕区域）
with open("video.mp4", "rb") as f:
    r = requests.post(f"{API}/remove", files={"file": f}, timeout=1800)
print(r.json())
# {"status":"ok","duration_seconds":47.2,"algorithm":"sttn","output_filename":"...","cached":false,...}

# 去字幕 + 直接下载结果
with open("video.mp4", "rb") as f:
    r = requests.post(
        f"{API}/remove/download",
        files={"file": f},
        data={"algorithm": "sttn", "sub_area": "850,1000,200,1720"},
        timeout=1800,
    )
with open("video_no_sub.mp4", "wb") as out:
    out.write(r.content)

# sub_area 格式：'ymin,ymax,xmin,xmax'，留空 = 自动检测全字幕
```

## 部署步骤（Windows）

> 一次性部署，约耗时 15-25 分钟（含模型解压、依赖安装、首次预热）。

### 1. 下载 VSR v1.1.1 CUDA 12.6 整合包

GitHub Release 三个分卷（共 ~4 GB），保存到 `G:\subtitle\downloads\`：

```bash
cd /g/subtitle/downloads
curl -fL --retry 10 -C - -o vsr.7z.001 'https://github.com/YaoFANGUK/video-subtitle-remover/releases/download/1.1.1/vsr-v1.1.1-windows-nvidia-cuda-12.6.7z.001'
curl -fL --retry 10 -C - -o vsr.7z.002 'https://github.com/YaoFANGUK/video-subtitle-remover/releases/download/1.1.1/vsr-v1.1.1-windows-nvidia-cuda-12.6.7z.002'
curl -fL --retry 10 -C - -o vsr.7z.003 'https://github.com/YaoFANGUK/video-subtitle-remover/releases/download/1.1.1/vsr-v1.1.1-windows-nvidia-cuda-12.6.7z.003'
```

CUDA 版本对照（按 `nvidia-smi` 显示的 CUDA Driver 版本选）：

- ≥ 12.8 → 用 `vsr-v1.1.1-windows-nvidia-cuda-12.8.7z.{001,002,003}`
- 12.6 ~ 12.7 → 用 12.6 整合包（本机驱动 566.36 / CUDA 12.7 即用此版）
- < 12.6 → 用 11.8 整合包

### 2. 解压到 `G:\subtitle\`

```bash
# 用 7zr.exe（轻量 CLI）解压（一条命令链 .001/.002/.003）
curl -fL -o /g/subtitle/downloads/7zr.exe https://www.7-zip.org/a/7zr.exe
cd /g/subtitle/downloads
./7zr.exe x vsr.7z.001 -o/g/subtitle -y
```

解压后 `G:\subtitle\` 会出现 `Python\` `resources\` `opt\` `configs\` `启动程序.exe` 等目录。

### 3. 安装依赖

整合包用 [QPT](https://github.com/QPT-Family/QPT) 打包，95 个 wheel 在 `opt/packages/`，torch 在 `opt/NoneName_torch==2.7.0 torchvision==0.22.0/`，**未自动安装**。

```bash
cd /g/subtitle

# 3.1 装 VSR 业务依赖（PaddleOCR + onnxruntime-gpu 等）
Python/python.exe -m pip install --no-index --find-links opt/packages -r resources/requirements.txt

# 3.2 装 torch GPU 版（CUDA 12.6）
Python/python.exe -m pip install --no-index --find-links "opt/NoneName_torch==2.7.0 torchvision==0.22.0" torch torchvision

# 3.3 装本服务的额外依赖（fastapi/uvicorn/multipart/psutil）
Python/python.exe -m pip install fastapi "uvicorn[standard]" python-multipart psutil
```

### 4. 部署本服务的代码

把仓库 `tools/subtitle/` 下的 `api_server.py`、`start.bat`、`.gitignore` 拷到 `G:\subtitle\`：

```bash
WORKTREE=$(git rev-parse --show-toplevel)
cp "$WORKTREE/tools/subtitle/api_server.py" /g/subtitle/
cp "$WORKTREE/tools/subtitle/start.bat" /g/subtitle/
```

### 5. 防火墙开放 82 端口（管理员 PowerShell）

```powershell
New-NetFirewallRule -DisplayName 'VSR-Subtitle-Remover-API' `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 82
```

### 6. 启动服务

双击 `G:\subtitle\start.bat`，或：

```bash
cd /g/subtitle && ./start.bat
```

首次启动会预热 VSR backend（解压模型分卷、加载 onnxruntime-gpu provider），耗时 30-60 秒。看到下面这行说明就绪：

```
Uvicorn running on http://0.0.0.0:82 (Press CTRL+C to quit)
```

### 7. 健康检查

```bash
curl http://172.30.254.12:82/health
```

期望 JSON 含 `"cuda_available": true`、`"cuda_device": "NVIDIA GeForce RTX 3060"`。

## 重启 / 杀进程

```bash
# 找占用 82 端口的 PID
netstat -ano | grep ':82 ' | grep LISTEN | awk '{print $NF}'
# 杀
taskkill //F //PID <PID>
# 重启
cd /g/subtitle && ./start.bat
```

## 文件清单

| 文件 | 说明 |
|------|------|
| `api_server.py` | FastAPI 服务主程序（GPU lock + MD5 缓存 + 队列计数） |
| `start.bat` | Windows 启动脚本 |
| `.gitignore` | 排除部署期生成目录 |
| `README.md` | **本文档** |

## 速度基准（默认 STTN）

| 视频规格 | 处理耗时 | 实时倍率 |
|---------|---------|---------|
| 720p / 60s | ~30s | 0.5x（快于实时） |
| 1080p / 60s | ~70s | 1.2x |
| 1080p / 600s（10 分钟） | ~12 min | 1.2x |

> 缓存命中（同 MD5 + 同算法 + 同 sub_area + 1h 内）：0ms 返回。
> 长视频客户端务必 `timeout >= 1800`，否则连接断开但 GPU 任务仍在跑。

## 故障排查

| 现象 | 原因 + 处置 |
|------|-----------|
| `from backend import config` 报 `ModuleNotFoundError` | `cd /d G:\subtitle` 没生效，或 `resources/` 内文件缺失，重新解压整合包 |
| `health` 返回 `cuda_available: false` | torch 未装 GPU 版，或驱动 < CUDA 12.6 → 重装 torch 或换 11.8 整合包 |
| ProPainter 算法 OOM | 12 GB 卡 1080p 默认已限到 `PROPAINTER_MAX_LOAD_NUM=40`，再 OOM 改 `api_server.py` 的 `min(..., 30)` |
| 整合包首次启动卡住 | QPT 在装依赖；改用本 README 第 3 步手工 pip 安装规避 |
| 客户端 timeout | 长视频处理慢，提高 client timeout 到 1800-3600s；GPU 队列每次只处理 1 个，多任务自动排队 |

## 与本仓库的集成

服务端已集成到 [AutoVideoSrtLocal](https://github.com/jinghuaswsx/AutoVideoSrtLocal) 项目结构（`tools/subtitle/`）。后续若做客户端封装（如自动从字幕识别坐标喂 `sub_area`），客户端代码也写在本目录下。
