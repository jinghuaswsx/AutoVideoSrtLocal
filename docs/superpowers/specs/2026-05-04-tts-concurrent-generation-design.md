# TTS 并发生成 + 跨任务全局并发上限

- 创建日期：2026-05-04
- 模块：多语言视频翻译 → 语音生成（ElevenLabs TTS）
- 目标：把 ElevenLabs TTS 的 70 段串行 HTTP 调用改为受控并发，并且通过进程级单例线程池让多个翻译任务**自动共享并排队** ElevenLabs 并发额度，避免出现"两个任务同时跑导致 429 大批量失败"的情况。

## 1. 背景

当前 [pipeline/tts.py:generate_full_audio](../../../pipeline/tts.py) 是严格串行循环：每段 segment 调一次 `client.text_to_speech.convert()` → 等返回 → 写文件 → 下一段。一个 70 段的多语言任务在 TTS 阶段需要按顺序跑 70 次 ElevenLabs HTTP 请求，瓶颈纯在串行而不在算力。

ElevenLabs 各订阅套餐有明确的并发上限（[官方支持文档](https://help.elevenlabs.io/hc/en-us/articles/14312733311761)）：

| 套餐 | 并发 |
|------|-----|
| Free | 2 |
| Starter | 3 |
| Creator | 5 |
| Pro | 10 |
| Scale | 15 |
| **Business（本项目当前订阅，tier=`growing_business`）** | **15** |
| Enterprise | 自定义 |

超过会返回 HTTP 429，error code `concurrent_limit_exceeded`。

如果只在单任务内部加 `ThreadPoolExecutor(max_workers=12)`，多个翻译任务并行时会变成 N×12，远超 15 上限，触发集体 429。所以必须做**进程级**的全局并发限流。

幸运的是，生产 gunicorn 配置（[deploy/gunicorn.conf.py:32-33](../../../deploy/gunicorn.conf.py)）是 `workers=1, worker_class=gthread, threads=32`，所有翻译任务都跑在**同一个 Python 进程内**，因此进程内单例线程池就足以实现跨任务全局排队，不需要 Redis / 文件锁。

## 2. 范围

### 包含
- 把 [pipeline/tts.py:generate_full_audio](../../../pipeline/tts.py) 的串行循环改成基于全局线程池的并发提交。
- 引入进程级单例 `ThreadPoolExecutor`，所有 ElevenLabs TTS segment 调用都走它，自然实现跨任务 FIFO 排队。
- 扩展现有 `_call_with_network_retry`：除网络层异常外，再识别 ElevenLabs 的 HTTP 429 / `concurrent_limit_exceeded`，按 0.5/1/2/4s 退避重试（顶多 4 次）。
- `system_settings` 表加 `tts_max_concurrency` 配置项（默认 12，可调），admin 后台不强求新增 UI（直接走 `/settings` 既有"系统配置"输入框，与其他 system_settings 一致），改后通过 `systemctl restart autovideosrt` 生效。
- 进度回调改成线程安全的 atomic counter，前端 `audio_segments_done` 仍单调递增。
- 单测覆盖并发分支、429 退避、回调线程安全、跨任务排队。

### 不包含
- 不改 ElevenLabs SDK 调用本身的参数、speed、voice_settings 等。
- 不改 [pipeline/tts.py:_audio_file_already_valid](../../../pipeline/tts.py) 缓存命中逻辑（缓存命中段直接跳过 ElevenLabs 调用，对并发设计透明）。
- 不动 ffmpeg concat 阶段（仍在所有 segment 完成后串行跑）。
- 不引入 Redis / 文件锁等跨进程同步设施（gunicorn workers=1 不需要）。
- 不动 Duration Loop 收敛逻辑、不动变速短路设计。
- 不引入 `pipeline/tts_v2.py` 的分镜级 TTS 路径（那是另一条独立通道，本设计不涉及）。

## 3. 设计

### 3.1 进程级单例线程池

[pipeline/tts.py](../../../pipeline/tts.py) 模块级新增：

```python
from concurrent.futures import ThreadPoolExecutor, Future
import atexit

_TTS_POOL: ThreadPoolExecutor | None = None
_TTS_POOL_LOCK = threading.Lock()
_DEFAULT_TTS_MAX_CONCURRENCY = 12
_HARD_CAP_TTS_MAX_CONCURRENCY = 15  # ElevenLabs Business tier hard limit


def _resolve_tts_max_concurrency() -> int:
    """从 system settings 读 tts_max_concurrency，默认 12，硬上限 15。"""
    from appcore.settings import get_setting
    raw = get_setting("tts_max_concurrency")
    try:
        n = int(raw) if raw is not None else _DEFAULT_TTS_MAX_CONCURRENCY
    except (TypeError, ValueError):
        n = _DEFAULT_TTS_MAX_CONCURRENCY
    return max(1, min(n, _HARD_CAP_TTS_MAX_CONCURRENCY))


def _get_tts_pool() -> ThreadPoolExecutor:
    global _TTS_POOL
    if _TTS_POOL is None:
        with _TTS_POOL_LOCK:
            if _TTS_POOL is None:
                max_workers = _resolve_tts_max_concurrency()
                _TTS_POOL = ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="tts-elevenlabs",
                )
                atexit.register(_TTS_POOL.shutdown, wait=True)
    return _TTS_POOL
```

### 3.2 跨任务全局排队语义

所有任务的 segment 都向同一个 `_TTS_POOL` 提交。`ThreadPoolExecutor` 内部以 FIFO 顺序消费 work queue，自然产生：

| 场景 | 实际行为 |
|------|---------|
| 单任务跑 70 段 | 池满载 12 路并发，约 6 批跑完 |
| 任务 A 占满 12 slot 时任务 B 来 | B 的 70 段 submit 后排到 work queue 后面，A 每完成 1 段、B 接 1 段 |
| 任务 B 启动延迟 | ≈ 1 个 segment 时长（2-5 秒），不需等 A 整体跑完 |
| 任意时刻总在跑的 segments | 永远 ≤ `max_concurrency`，物理上不可能超 ElevenLabs 上限 |

### 3.3 generate_full_audio 改造

```python
def generate_full_audio(segments, voice_id, output_dir, *, variant=None,
                       elevenlabs_api_key=None, model_id="eleven_turbo_v2_5",
                       language_code=None, on_segment_done=None) -> Dict:
    seg_dir = ...
    os.makedirs(seg_dir, exist_ok=True)

    total = len(segments)
    pool = _get_tts_pool()

    # 1. 把每段封装成可独立执行的 task，submit 到全局 pool
    tasks: list[tuple[int, dict, str, str, Future]] = []
    for i, seg in enumerate(segments):
        text = seg.get("tts_text") or seg.get("translated") or seg.get("text", "")
        seg_path = os.path.join(seg_dir, f"seg_{i:04d}.mp3")
        future = pool.submit(
            generate_segment_audio,
            text, voice_id, seg_path,
            elevenlabs_api_key=elevenlabs_api_key,
            model_id=model_id, language_code=language_code,
        )
        tasks.append((i, seg, text, seg_path, future))

    # 2. as_completed 顺序回收（不一定按 i 顺序），更新进度回调
    seg_results: dict[int, dict] = {}
    done = 0
    failures: list[tuple[int, BaseException]] = []
    for fut in as_completed([t[4] for t in tasks]):
        idx_for_fut = next(t for t in tasks if t[4] is fut)
        i, seg, text, seg_path, _ = idx_for_fut
        try:
            fut.result()
        except BaseException as exc:
            failures.append((i, exc))
            continue
        duration = _get_audio_duration(seg_path)
        seg_copy = dict(seg)
        seg_copy["tts_path"] = seg_path
        seg_copy["tts_duration"] = duration
        seg_results[i] = seg_copy

        done += 1
        if on_segment_done is not None:
            try:
                on_segment_done(done, total, {
                    "segment_index": i,
                    "tts_duration": duration,
                    "tts_text_preview": (text or "")[:60],
                })
            except Exception:
                log.exception("on_segment_done callback raised; ignoring")

    if failures:
        # 任一段失败：取消尚未启动的 future（已启动的等结束），抛第一个异常
        for _, _, _, _, f in tasks:
            f.cancel()
        first_idx, first_exc = failures[0]
        raise RuntimeError(
            f"TTS segment generation failed at index {first_idx} "
            f"({len(failures)}/{total} failed): {first_exc}"
        ) from first_exc

    # 3. 按 i 顺序拼 concat 列表（保持音轨时序）
    updated_segments = [seg_results[i] for i in range(total)]
    concat_list_path = os.path.join(seg_dir, "concat.txt")
    with open(concat_list_path, "w", encoding="utf-8") as f:
        for seg_copy in updated_segments:
            f.write(f"file '{os.path.abspath(seg_copy['tts_path'])}'\n")

    # 4. ffmpeg concat（不变）
    ...
    return {"full_audio_path": full_audio_path, "segments": updated_segments}
```

关键点：
- `concat.txt` 必须按 `i` 顺序写，**不能**按完成顺序，否则音轨乱序。
- `on_segment_done` 在 `as_completed` 主线程调用，无线程安全担忧（counter 单线程 +1）。
- 第一个失败立即触发 cancel + 抛异常；已经在跑的 segment 由 ElevenLabs 自身返回后被丢弃。

### 3.4 429 退避重试

[pipeline/tts.py](../../../pipeline/tts.py) 现有 `_call_with_network_retry` 只识别网络层异常（`httpx.RemoteProtocolError` 等），不识别 HTTP 429。新增 `_call_with_throttle_retry` 包一层：

```python
from elevenlabs.core.api_error import ApiError as _ElevenLabsApiError  # 实际类名以 SDK 为准

_THROTTLE_RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)

def _is_concurrent_limit_429(exc: BaseException) -> bool:
    """识别 ElevenLabs 的 HTTP 429（特别是 concurrent_limit_exceeded）。"""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status != 429:
        return False
    body = getattr(exc, "body", None) or getattr(exc, "response", None)
    text = str(body or exc).lower()
    return "concurrent_limit_exceeded" in text or "rate_limit_exceeded" in text or status == 429


def _call_with_throttle_retry(fn, *, label="elevenlabs"):
    for attempt, delay in enumerate(_THROTTLE_RETRY_DELAYS):
        try:
            return fn()
        except BaseException as exc:
            if not _is_concurrent_limit_429(exc):
                raise
            if attempt >= len(_THROTTLE_RETRY_DELAYS) - 1:
                log.exception("%s throttle retry exhausted: %s", label, exc)
                raise
            log.warning("%s 429 throttle, retry in %.1fs: %s", label, delay, exc)
            time.sleep(delay)
```

`generate_segment_audio` 内部调用顺序：throttle_retry → network_retry → SDK convert（throttle 在外层，因为 429 是 HTTP 层错误，已经收到 response）。

### 3.5 admin 配置项

通用 settings 模块在 [appcore/settings.py](../../../appcore/settings.py)，对外暴露 `get_setting(key) / set_setting(key, value)`。本设计**不需要**新建 setting 模块，只新增一个键 `tts_max_concurrency`（字符串存数字，业务侧 `int()` 解析；默认 12，硬上限 15）。

`/settings` 页面（admin）已有 system settings 编辑入口（参考 `web/routes/admin.py` 现有 `from appcore.settings import ...` 用法），加一行表单字段：
- 标签："TTS 并发上限"
- 提示："ElevenLabs Business 套餐硬上限 15。改后需 systemctl restart autovideosrt 生效。"
- 验证：1 ≤ n ≤ 15

`ThreadPoolExecutor` 创建后无法动态调整 `max_workers`，因此改完配置依赖**重启服务**生效，与项目其他 system settings 改动行为一致。无需运行时动态 resize。

### 3.6 并发安全细节

| 共享对象 | 是否线程安全 |
|---------|-------------|
| `_get_client()`（每次新建 ElevenLabs 实例） | ✅ 线程局部，每次调用新建 |
| `_audio_file_already_valid` 缓存检查 | ✅ 无共享状态，纯 stat |
| `os.makedirs(..., exist_ok=True)` | ✅ POSIX/Windows 都对 exist_ok 容忍并发 |
| `concat_list_path` 写入 | ✅ 主线程串行写（在 `as_completed` 收尾后） |
| `on_segment_done` 回调 | ✅ 主线程串行调用 |
| `done` counter | ✅ 主线程单线程 +1 |

无需引入显式锁。

### 3.7 模块边界 / 文件改动清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| [pipeline/tts.py](../../../pipeline/tts.py) | 修改 | 新增 `_TTS_POOL` / `_get_tts_pool` / `_resolve_tts_max_concurrency` / `_call_with_throttle_retry` / `_is_concurrent_limit_429`；改写 `generate_full_audio` 为并发提交 + `as_completed` 收回 + cancellation 检查；保留接口签名（`segments, voice_id, output_dir, variant, elevenlabs_api_key, model_id, language_code, on_segment_done`） |
| [appcore/settings.py](../../../appcore/settings.py) | 不改 | 复用现有 `get_setting / set_setting` API，无需新增模块 |
| `web/templates/settings.html` + `web/routes/admin.py` | 修改 | 加 TTS 并发上限输入框（与现有 retention/RMB 等 system settings 编辑入口一致） |
| `tests/test_tts_concurrent_generation.py` | **新建** | 并发提交、429 退避、回调 atomic counter、concat 顺序、第一失败抛错、cancellation 行为、跨任务排队（多线程模拟） |
| [tests/test_tts_duration_loop.py](../../../tests/test_tts_duration_loop.py) | 微调（如需） | 把现有 `generate_full_audio` mock 适配新的并发提交路径，确保 duration loop 测试仍绿 |

预估改动行数：核心 ~120 行，测试 ~150 行；零数据库迁移；零新依赖（`concurrent.futures` 标准库）。

## 4. 失败模式与边界

| 场景 | 行为 |
|------|------|
| 单段 ElevenLabs 调用失败（网络） | 现有 `_call_with_network_retry` 兜底，最多 3 次指数退避后抛 |
| 单段 HTTP 429 / concurrent_limit_exceeded | 新增 `_call_with_throttle_retry` 兜底，0.5/1/2/4s 退避，4 次后抛 |
| 多段同时失败 | 第一个失败被抛，其余 future cancel，duration loop 该轮整体失败（与现有"任意一段失败 = 该轮失败"语义一致） |
| 任务被 cancel | 现有 [pipeline/tts.py](../../../pipeline/tts.py) 在 segment 内**没有** cancellation 检查（`appcore.cancellation.throw_if_cancel_requested` 由上层 runtime 在 `generate_full_audio` 调用前后检查）。并发改造后增加：在主循环 submit 之前用 `throw_if_cancel_requested()`，在 `as_completed` 收回每段后再次检查；若被取消，对所有未启动的 future 调 `cancel()`，正在跑的 segment 让 ElevenLabs 自然返回（单段 2-5s，可忽略），整体抛 `CancelledError` 让上层回收 |
| pool 在 gunicorn worker 重启时 | `atexit.register(_TTS_POOL.shutdown, wait=True)` 让正在跑的 segment 收尾完毕，配合 `gunicorn.conf.py` 的 `worker_exit` drain（参考 [deploy/autovideosrt.service:18-25](../../../deploy/autovideosrt.service)），不会丢段 |
| admin 把 `tts_max_concurrency` 改成 0 或负数 | `_resolve_tts_max_concurrency` `max(1, ...)` 兜底，不会让 pool 退化 |
| admin 改成 > 15（比如 100） | `min(n, 15)` 兜底，物理上不会超 ElevenLabs 套餐限制 |
| 同时 N 个任务并发提交，N×70 = 700 段同时入队 | 队列内存占用约 700 × ~200B = 140KB，可忽略；pool 仍按 12 路并发消费 |

## 5. 验收标准

1. 单个 70 段任务的 TTS 阶段从串行约 N×单段时长 缩短到约 N/12 × 单段时长（理论 12× 加速，实测期望 8-10×）。
2. 同时启动 2 个并发翻译任务（手动构造），两个任务的 TTS 段在 ElevenLabs 端的并发量从未超过 12（通过日志 / SDK 实例计数验证）。
3. `tests/test_tts_concurrent_generation.py` 全绿。
4. [tests/test_tts_duration_loop.py](../../../tests/test_tts_duration_loop.py) 全绿（并发改造对 duration loop 透明）。
5. 任一段返回 429 时，单元测试覆盖：先退避、退避耗尽抛错、上层 duration loop 进入下一轮 rewrite。
6. 前端任务详情页 `audio_segments_done / total` 仍单调递增（手动跑一个任务到 dev server 确认）。
7. admin 在 `/settings` 改 `tts_max_concurrency=8` → restart → 跑同一任务 → 实测并发数为 8（日志 + ElevenLabs Dashboard 双重确认）。

## 6. 上线判断与回滚

- 上线前：在 dev server 起 `prod .env`、用真账号跑 1 个 70 段任务 + 2 个并发任务，观察 ElevenLabs Dashboard 实时并发图，必须 ≤ 12。
- 回滚：本设计核心改动集中在 [pipeline/tts.py:generate_full_audio](../../../pipeline/tts.py)，回退一个 commit 即可恢复串行行为；线程池单例本身在导入时不创建（懒加载），不影响其他子系统启动。
- Kill switch：把 `system_settings.tts_max_concurrency` 设为 1 + restart → 退化为单并发（接近原串行行为），无需回滚代码。

## 7. 后续可拓展（非本次范围）

- 跨进程并发限流（如未来 gunicorn 升级到 multi-worker）：可换成 Redis 计数器或文件锁。
- 按用户/任务级 QoS 优先级：让 admin 用户的任务在 work queue 里抢先（需要从 `ThreadPoolExecutor` 换成自定义优先级队列）。
- 与 [docs/superpowers/specs/2026-05-04-tts-speedup-shortcut-design.md](2026-05-04-tts-speedup-shortcut-design.md) 的变速短路联动：变速 pass 的 70 段重生成同样走全局 pool，自然受益（设计天然兼容，无需额外改动）。
