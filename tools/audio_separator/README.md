# 音频分离器外部 API 服务

基于 [python-audio-separator](https://github.com/nomadkaraoke/python-audio-separator) 的 GPU 加速音频人声/伴奏分离服务。

## 部署位置

- **运行目录**: `G:\audio\`
- **服务地址**: `http://172.30.254.12:80`
- **服务端口**: 80
- **GPU**: NVIDIA RTX 3060 (12GB, 限制 90%)
- **CPU**: 50% 逻辑核心 + HIGH 优先级

## 架构

```
客户端 POST /separate (timeout=300s)
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

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 + 队列/缓存状态 |
| `GET` | `/queue` | 队列深度速查 |
| `GET` | `/models` | 83 个可用模型 |
| `GET` | `/presets` | 9 个集成预设 |
| `GET` | `/docs` | Swagger 交互式文档 |
| `POST` | `/separate` | 提交分离任务 |
| `POST` | `/separate/download` | 提交并下载 ZIP |

## 客户端调用

```python
import requests

API = "http://172.30.254.12"

# 健康检查
r = requests.get(f"{API}/health")

# 分离音频（设 300s 超时，自动排队）
with open("song.mp3", "rb") as f:
    r = requests.post(f"{API}/separate", files={"file": f}, timeout=300)

print(r.json())
# { "status":"ok", "duration_seconds":16.37, "stems":["...Instrumental","...Vocals"], "cached":false }
```

## 启动/重启

```bash
# 1. 杀掉旧进程
taskkill //F //PID $(netstat -ano | grep ':80 ' | grep LISTEN | awk '{print $NF}')

# 2. 启动（自动预热模型）
cd /g/audio && nohup /g/audio/venv312/Scripts/python /g/audio/api_server.py &
```

## 文件清单

| 文件 | 说明 |
|------|------|
| `api_server.py` | FastAPI 服务主程序（带 MD5 缓存 + 排队） |
| `start.bat` | Windows 启动脚本 |
| `README.md` | **本文档** |
| `../../tests/audio/test_api.py` | API 测试用例 |

## 速度基准

预设 `vocal_balanced`（默认）：

| 音频时长 | 分离耗时 | 实时倍率 |
|---------|---------|---------|
| 25s | ~16s | 1.6x |
| 60s | ~38s | 1.6x |

缓存命中：0ms 返回。
