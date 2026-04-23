# Web Service Tuning Design

## Goal

在不引入 Redis、sticky session、独立任务队列等架构改造的前提下，把 AutoVideoSrt 当前服务端 Web 部署从 `gunicorn + eventlet` 调整为更符合现状代码模型的单进程线程化方案，使其更好匹配 20 核 28 线程、32G 内存的新服务器。

## Context

当前线上部署配置是：

- `gunicorn -w 1 -k eventlet --bind 0.0.0.0:80 --timeout 300 main:app`
- `web/extensions.py` 中 `SocketIO(async_mode="threading")`
- 应用内部同时使用：
  - Flask-SocketIO room / 内存态任务状态
  - `BackgroundScheduler`
  - `threading.Thread`
  - `ThreadPoolExecutor`
  - 少量 `eventlet.spawn(...)`

这意味着服务已经不是以 Eventlet 绿色线程为唯一并发模型，而是“线程模型为主、Eventlet 仅残留在部分启动入口”。继续维持 `eventlet worker` 会放大并发模型混用问题；但直接把 Gunicorn `workers` 提升到 2、4、8 也会让 SocketIO 房间、内存状态、scheduler 扫描、SSE/实时进度等行为出现跨进程不一致。

## Constraints

- 本次不引入 Redis / RabbitMQ / Celery。
- 本次不做跨进程状态同步。
- 本次不把后台任务彻底拆成独立 worker service。
- 本次不修改业务任务语义，只调整 Web 服务承载方式与启动入口。
- 线上仍默认单 Gunicorn worker，避免破坏现有实时任务与内存态状态前提。

## Chosen Approach

采用单 worker 的 `gthread` 方案，替代 `eventlet`：

- Gunicorn 保持 `workers = 1`
- Gunicorn 改为 `worker_class = "gthread"`
- 增加 `threads`，默认提升到适配当前机器的线程数
- 路由层不再直接依赖 `eventlet.spawn(...)`
- 统一改用 `socketio.start_background_task(...)` 作为后台任务入口
- 增加 Gunicorn 独立配置文件，避免 systemd 里堆叠长命令
- 在 systemd 中补充更适合高并发 Web 服务的环境变量与资源限制

## Why Not Multi-Worker

本项目当前不能把“机器变强”简单映射成“Gunicorn worker 变多”，核心原因有三点：

1. Flask-SocketIO 官方文档要求多进程扩展时要有 sticky session 和消息队列；单纯 `-w N` 不成立。
2. 应用内部存在内存态任务状态、房间广播和单进程队列假设，多 worker 会造成可见性不一致。
3. `BackgroundScheduler` 和若干后台轮询逻辑在多 worker 下会重复启动，导致重复扫描与重复执行风险。

因此本次优先把单 worker 的吞吐、线程承载能力和稳定性吃满，而不是冒险扩大进程数。

## Runtime Tuning Decisions

### Gunicorn

- `worker_class = "gthread"`
- `workers = 1`
- `threads = 32`
- `timeout = 300`
- `graceful_timeout = 30`
- `keepalive = 10`
- `capture_output = True`
- `accesslog = "-"`, `errorlog = "-"`

`threads = 32` 的取值逻辑：

- 当前服务器是 20 核 28 线程，32G 内存。
- 单 worker 前提下，要提高的是“同一进程内可同时承载的请求 / SocketIO / 长连接 / 上传”等并发数。
- 32 线程对当前机器是保守偏积极的配置，足以明显优于旧的 `eventlet + 1 worker`，又不会把线程数抬到 100 这种更偏高连接密度场景的配置。
- 后续如果线上监控显示仍有等待堆积，可再把线程数抬到 48；如果 CPU 抢占明显，可回落到 24。

### Native Library Thread Limits

在 systemd 环境中增加：

- `OMP_NUM_THREADS=1`
- `OPENBLAS_NUM_THREADS=1`
- `MKL_NUM_THREADS=1`
- `NUMEXPR_NUM_THREADS=1`

原因：

- 本项目依赖 `numpy`、`scikit-image`、`librosa` 等本地计算库。
- Web 层已经通过 Gunicorn threads 和后台线程提供并发；若底层数值库再各自展开多线程，会导致 CPU 过度抢占与线程爆炸。
- 把数值库线程数固定为 1，更符合“Web 并发 + 后台任务并发”的整体调度。

## Code Changes

### Deployment Files

- 新增 `deploy/gunicorn.conf.py`
- 修改 `deploy/autovideosrt.service`
- 修改 `deploy/setup.sh`
- 修改 `requirements.txt`

### Web Background Launching

新增统一后台任务启动 helper，例如 `web/background.py`：

- 对外提供 `start_background_task(...)`
- 内部调用 `socketio.start_background_task(...)`
- 所有原先 `eventlet.spawn(...)` 的入口都改走这里

这样可以让后台任务启动方式和 `SocketIO async_mode` 对齐，避免部署从 Eventlet 切到 gthread 后仍混用 Eventlet API。

### Affected Routes

- `web/routes/bulk_translate.py`
- `web/routes/copywriting.py`
- `web/routes/copywriting_translate.py`
- `web/routes/video_creation.py`
- `web/routes/video_review.py`

## Validation

需要验证三类结果：

1. 路由触发后台任务时，改为走统一 helper，不再依赖 `eventlet.spawn(...)`
2. Web 服务配置文件中不再使用 `eventlet worker`
3. 关键路由 / helper / 配置测试通过

## Expected Outcome

完成后，线上 Web 服务将具备这些特征：

- 并发模型更统一：Web 线程 + 后台线程，不再混用 Eventlet worker
- 与 Flask-SocketIO `threading` 模式一致
- 在单 worker 前提下吃到更高线程并发，能更好适配新服务器
- 避免多 worker 带来的跨进程状态不一致

## Non-Goals

以下不在本次范围内：

- 多 worker / 多实例横向扩容
- Redis message queue
- 独立任务队列服务
- scheduler 去重锁
- 服务用户从 `root` 切换为专用低权限用户

这些是后续更大一轮部署治理的主题，不与本次“Web 服务线程化调优”混在同一个变更里。
