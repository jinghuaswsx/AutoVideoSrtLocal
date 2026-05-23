# 音频分离器外部 API 服务

基于 [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) 的 GPU 加速音频人声/伴奏分离服务。

## 部署位置

- **运行目录**: `G:\audio\`
- **服务地址**: `http://172.30.254.12/separate/*`（走 [Caddy 网关](../gateway/README.md) 80 端口）
- **内部端口**: 8081（仅本机；外部访问统一走网关 80）
- **URL 前缀**: `/separate`（用 `APIRouter(prefix="/separate")` 实现）
- **GPU**: NVIDIA RTX 3060 (12GB)；显存软限 50%（≈ 6GB），与 subtitle/vace 共租 12GB 卡
- **CPU**: 50% 逻辑核心 + HIGH 优先级

## 架构

```
客户端 POST http://172.30.254.12/separate/{run|download}  (timeout=300s)
  → Caddy:80 反向代理到 localhost:8081
  → 服务端计算 MD5 → 检查 1h 内存缓存
  → 命中则 0ms 返回缓存
  → 未命中则 asyncio.Lock 排队等 GPU
  → GPU 空闲 → 同步分离 → 写入缓存 → 返回结果
```

## 依赖

| 软件 | 位置 |
|------|------|
| Python 3.12 | `G:\audio\python312\` |
| 虚拟环境 | `G:\audio\venv312\` |
| 模型文件 | `G:\audio\models\` (~3.5GB) |

## API 端点（外部 URL = 网关 + 前缀）

| 方法 | 网关 URL | 说明 |
|------|------|------|
| `GET` | `/separate/health` | 健康检查 + 队列/缓存状态 |
| `GET` | `/separate/queue` | 队列深度速查 |
| `GET` | `/separate/models` | 全部模型清单 |
| `GET` | `/separate/presets` | 9 个集成预设 |
| `GET` | `/separate/docs` | Swagger 交互式文档 |
| `POST` | `/separate/run` | 提交分离任务（**原 POST `/separate`**） |
| `POST` | `/separate/download` | 提交并下载 ZIP |

## 客户端调用

```python
import requests

API = "http://172.30.254.12"

# 健康检查
r = requests.get(f"{API}/separate/health")

# 分离音频（设 300s 超时，自动排队）
with open("song.mp3", "rb") as f:
    r = requests.post(f"{API}/separate/run", files={"file": f}, timeout=300)

print(r.json())
# { "status":"ok", "duration_seconds":16.37, "stems":["...Instrumental","...Vocals"], "cached":false }
```

业务客户端：[`appcore/audio_separation_client.py`](../../appcore/audio_separation_client.py) 已对齐前缀。

## 启动/重启

```bash
# 1. 杀掉旧进程
taskkill //F //PID $(netstat -ano | grep ':8081 ' | grep LISTEN | awk '{print $NF}')

# 2. 启动（自动预热模型）
cd /g/audio && nohup /g/audio/venv312/Scripts/python /g/audio/api_server.py &
```

## 文件清单

| 文件 | 说明 |
|------|------|
| `api_server.py` | FastAPI 服务主程序（带 MD5 缓存 + 排队），APIRouter prefix=/separate |
| `start.bat` | Windows 启动脚本（端口 8081） |
| `README.md` | **本文档** |
| `../../tests/audio/test_api.py` | API 集成测试 |

## 速度基准

预设 `vocal_balanced`（默认）：

| 音频时长 | 分离耗时 | 实时倍率 |
|---------|---------|---------|
| 25s | ~16s | 1.6x |
| 60s | ~38s | 1.6x |

缓存命中：0ms 返回。

## 与 subtitle / VACE 协同

3060 12GB 同时跑两个服务：
- audio 进程 `set_per_process_memory_fraction(0.5)` → 自留 6GB 上限
- subtitle 进程同样 0.5
- VACE 接入时各服务再让一让（或加跨进程 GPU lock）

详见 [tools/gateway/README.md](../gateway/README.md)。
