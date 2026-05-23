# GPU 服务网关（Caddy）

把本机所有 GPU 服务统一暴露在 80 端口下，按 URL 前缀路由到独立 FastAPI 进程。

## 拓扑

```
                    Caddy :80 (LAN 入口 172.30.254.12)
                    ┌────────────┬───────────┬──────────┐
                    │            │           │          │
              /separate/*   /subtitle/*  /vace/*   handle /
                    ▼            ▼           ▼
                  :8081        :8082       :8083
                  audio       subtitle     vace(预留)
```

| 前缀 | 内部端口 | 服务 | 部署目录 | 状态 |
|---|---|---|---|---|
| `/separate/*` | 8081 | python-audio-separator | `G:\audio\` | 运行中 |
| `/subtitle/*` | 8082 | video-subtitle-remover (VSR) | `G:\subtitle\` | 运行中 |
| `/vace/*` | 8083 | VACE | `G:\vace\` | **预留，未启用** |

## 部署位置

- Caddy 二进制：`G:\gateway\caddy.exe`（v2.11.2）
- 配置文件：`G:\gateway\Caddyfile`
- 日志：`G:\gateway\logs\access.log`（json 格式，50MB 滚动）
- 防火墙规则：`GPU-Gateway-80`

## 一次性部署

```bash
# 1. 下载 caddy windows amd64 zip 解压拿 caddy.exe
#    https://github.com/caddyserver/caddy/releases/latest
#    本仓库 bootstrap 流程会顺便存到 G:\subtitle\downloads\caddy\caddy.exe

# 2. 装 + 加防火墙
WORKTREE=$(git rev-parse --show-toplevel)
cd "$WORKTREE/tools/gateway"
./install_caddy.bat                    # 默认从 G:\subtitle\downloads\caddy\caddy.exe 拷
# 或传路径：./install_caddy.bat C:\Downloads\caddy.exe

# 3. 启动
G:/gateway/start.bat
```

## 改配置（无重启 reload）

```bash
# 改完 Caddyfile 后
G:\gateway\caddy.exe reload --config G:\gateway\Caddyfile
```

## API 端点

| URL | 说明 | 内部端点 |
|---|---|---|
| `GET /` | 网关首页 | Caddy 直接返回纯文本 |
| `GET /separate/health` | 音频服务健康 | audio:8081 |
| `GET /separate/queue` | 音频队列状态 | audio:8081 |
| `GET /separate/models` | 全部模型清单 | audio:8081 |
| `GET /separate/presets` | ensemble preset 清单 | audio:8081 |
| `GET /separate/docs` | audio Swagger | audio:8081 |
| `POST /separate/run` | 上传音频 → JSON 元信息 | audio:8081 |
| `POST /separate/download` | 上传音频 → ZIP（vocals + instrumental） | audio:8081 |
| `GET /subtitle/health` | 字幕服务健康 | subtitle:8082 |
| `GET /subtitle/queue` | 字幕队列状态 | subtitle:8082 |
| `GET /subtitle/algorithms` | 算法清单 | subtitle:8082 |
| `GET /subtitle/docs` | subtitle Swagger | subtitle:8082 |
| `POST /subtitle/remove` | 上传视频 → JSON 元信息 | subtitle:8082 |
| `POST /subtitle/remove/download` | 上传视频 → MP4（已去字幕） | subtitle:8082 |
| `*/vace/*` | **预留 VACE 路由，启用前是 502** | （Caddyfile 注释中） |

## 客户端示例

```python
import requests

API = "http://172.30.254.12"

# 健康检查（任意一个）
print(requests.get(f"{API}/separate/health").json())
print(requests.get(f"{API}/subtitle/health").json())

# 音频分离
with open("song.mp3", "rb") as f:
    r = requests.post(f"{API}/separate/download",
                      files={"file": f},
                      data={"ensemble_preset": "vocal_balanced"},
                      timeout=300)

# 去字幕
with open("video.mp4", "rb") as f:
    r = requests.post(f"{API}/subtitle/remove",
                      files={"file": f},
                      data={"algorithm": "sttn"},
                      timeout=1800)
```

## 启用 VACE（未来）

1. 把 VACE 服务部署到 `:8083`（参考 tools/subtitle 的 QPT-style 整合包模式）
2. 取消 [Caddyfile](Caddyfile) 里 `# @vace` 那一段注释
3. `caddy reload`（无需重启 Caddy 进程，零 downtime）

## 故障排查

| 现象 | 原因 + 处置 |
|------|-----------|
| `/separate/*` 502 | audio 服务挂了，检查 8081 端口 + `tasklist /fi "IMAGENAME eq python.exe"` |
| `/subtitle/*` 502 | subtitle 服务挂了，同上 |
| `/vace/*` 502 | 预期，VACE 还没启用 |
| Caddy 启动报 `bind: permission denied` 或 `address in use` | 80 被其他进程占用，`netstat -ano | grep ":80 "` 找出来 |
| Caddyfile 改完没生效 | 跑一次 `caddy reload`，不会断连接 |
| LAN 客户端 connection refused | 防火墙规则没加，跑 `install_caddy.bat` 或手工 `netsh ...` |

## 性能

- Caddy 自身 idle 内存 ~30 MB，每秒上千 RPS 没问题
- 反向代理增加延迟 < 1 ms（同机 localhost）
- 单一上传文件最大 64 KB header（body 无限制，配 `read_timeout` 控制慢上传）
