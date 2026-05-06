# VACE on RTX 4070 Ti Super：调研结论 + 接入实施方案

**目标**：把 [`appcore/vace_subtitle/`](../../appcore/vace_subtitle/) 默认走的 RTX 3060 路径
切换到 4070 Ti Super 16 GB 优化路径。**不引入 14B 支持**（显存仍不足）；
聚焦 1.3B / 480p / 提速 + 提质，并解决 audio + subtitle + VACE 三服务共卡时的
显存预算冲突。

本文同时是**阶段 2（实测验证）启动前的实施清单**——所有改动在不真实跑 VACE
的前提下都能 commit、能 dry-run、能跑测试。真正端到端验证留给阶段 2。

---

## 1. 硬件画像

| 项 | RTX 3060 12GB（基线） | **RTX 4070 Ti Super 16GB** | RTX 5090 32GB |
|---|---|---|---|
| 架构 | Ampere GA106 sm_86 | **Ada Lovelace AD103 sm_89** | Blackwell GB202 sm_120 |
| CUDA cores | 3584 | **8448** | 21760 |
| FP16 Tensor TFLOPS | ~25 | **~88** | ~165 |
| 显存 / 类型 | 12 GB GDDR6 | **16 GB GDDR6X** | 32 GB GDDR7 |
| 显存带宽 | 360 GB/s | **672 GB/s** | 1792 GB/s |
| TGP | 170 W | **285 W** | 575 W |
| MPS（多进程并行） | 不支持 | **不支持** | 不支持 |
| PyTorch wheel 兼容 | cu118/cu121/cu124/cu126 | **cu118/cu121/cu124/cu126**（成熟） | 需 cu128 nightly（不成熟） |

**关键定位**：
- 算力 ≈ 3060 × 3.5
- 显存 ≈ 3060 × 1.33（4 GB 增量在 audio+subtitle 双驻留场景刚好够用）
- 仍是 GeForce 消费卡，**多进程 GPU 调用按时间片轮转**，不是真并行

---

## 2. VACE 可行性结论

### 2.1 模型档位

| VACE 档 | FP16 推理峰值显存 | 4070 Ti Super 16GB | 结论 |
|---|---|---|---|
| Wan2.1-VACE-1.3B / 480p | ~6-8 GB | ✅ 单跑 OK；与 audio+subtitle 共卡需调 fraction | **本卡主路径** |
| Wan2.1-VACE-1.3B / 720p | ~10-12 GB | ⚠️ 单跑 OK；共卡时易 OOM | 不作默认 |
| Wan2.1-VACE-14B / 480p | ~16-20 GB（量化前） | ❌ OOM | 禁用 |
| Wan2.1-VACE-14B / 720p | ~22-28 GB | ❌ OOM | 禁用（要 24 GB+ 卡） |

→ **本卡 VACE 路径只跑 1.3B，主分辨率 480p，可选 720p（仅独占 GPU 时）**。

### 2.2 速度估算（单任务，1080p 输入 ROI 合成）

> 数值基于 4070 Ti Super ≈ 4080 (~95%) ≈ 3060 × 3.5 推算，**±30%** 误差。

| 算法 | 输入 60s | 3060 实测/估算 | **4070 Ti Super 估算** |
|---|---|---|---|
| STTN（subtitle 默认） | 1080p / 60s | ~70s | **~22-25s** |
| LAMA | 1080p / 60s | ~180s | **~50-60s** |
| ProPainter（max_load=70） | 1080p / 60s | ~480s @ max_load=25 | **~150s @ max_load=70** |
| **VACE 1.3B / 480p / steps=20** | 1080p输入 / 60s | ~200s | **~75-90s** |
| **VACE 1.3B / 480p / steps=25** | 1080p输入 / 60s | ~260s | **~95-115s** |
| **VACE 1.3B / 720p / steps=20**（独占 GPU） | 1080p输入 / 60s | OOM 边缘 | **~150-180s** |

实时倍率（视频长度 / 处理时长）：
- 60s 视频 STTN ≈ 2.5x 实时
- 60s 视频 VACE 1.3B 480p ≈ 0.6-0.7x 实时（处理慢于实时）
- 长视频可分 chunk 并行（受单卡 SM 调度限制，提升有限）

### 2.3 并发显存预算（共卡场景）

每服务进程 `torch.cuda.set_per_process_memory_fraction(F)` 自留上限。
4070 Ti Super 16 GB 上的合理切分（单位 GB）：

| 场景 | audio | subtitle | VACE | 总占用 | 4070 Ti Super 16 GB |
|---|---|---|---|---|---|
| audio + subtitle（无 VACE） | 5 (F=0.4) | 5 (F=0.4) | 0 | ~10 / 16 | ✅ 稳 |
| audio + subtitle + VACE 1.3B（典型生产） | 4.5 (F=0.3) | 4.5 (F=0.3) | 6.5 (F=0.45) | ~15.5 / 16 | ⚠️ 临界 |
| 仅 VACE 1.3B 单跑（峰值/精跑模式） | 0 | 0 | 14 (F=0.9) | ~14 / 16 | ✅ 720p 也能开 |

→ 三服务共卡必须**降低 audio/subtitle 的 fraction 到 0.3-0.35**（vs 3060 上 0.5），
并加**跨进程 GPU lock**避免 VACE 推理峰值时与 audio/subtitle 同步推理撞车。

### 2.4 兼容性

- **驱动**：560+ 即可（本机 566 已满足，**不需要升**）
- **CUDA Toolkit**：12.4 / 12.6 都行
- **PyTorch**：现有 cu124（audio）/ cu126（subtitle）wheel **直接跑**，无需 nightly
- **VACE 仓库**：torch 2.5.1+cu124 + wan2.1 库直接装即可（按 [`docs/vace_windows_backend.md`](../../docs/vace_windows_backend.md) §3 步骤）
- **paddlepaddle-gpu 3.0**（subtitle 用）在 sm_89 已稳定多年

→ **零兼容性坑**，跟 5090 (sm_120 nightly 链) 截然不同。

---

## 3. 推荐 profile（新增）

在 [`appcore/vace_subtitle/config.py`](../../appcore/vace_subtitle/config.py) 现有 3 档 profile
基础上**新增 3 档**，前缀 `rtx4070tis_`，专门匹配 4070 Ti Super：

| Profile name | model | size | frame_num | sample_steps | offload_model | t5_cpu | chunk_seconds | max_long_edge | max_short_edge | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|
| `rtx4070tis_fast` | vace-1.3B | 480p | 41 | 20 | False | False | 2.7 | 832 | 480 | 单跑、要快 |
| `rtx4070tis_balanced` (推荐默认) | vace-1.3B | 480p | 81 | 25 | False | False | 4.8 | 832 | 480 | 共卡也稳 |
| `rtx4070tis_quality` | vace-1.3B | 720p | 81 | 30 | False | False | 4.8 | 1280 | 720 | **要求 GPU 独占**（暂停 audio/subtitle） |

**关键差异 vs `rtx3060_*`**：
- `offload_model=False`：16 GB 显存够装权重，关闭 CPU offload 提速 30-50%
- `t5_cpu=False`：T5 文本编码器留在 GPU，省一次 CPU↔GPU 同步
- `chunk_seconds`：fast=2.7（与 frame_budget 41/30=1.37s 实测中折中）；balanced/quality=4.8（frame_num=81 给的 budget）
- `max_long_edge`：quality 模式抬到 1280（720p 模型路径）
- `frame_num` 仍受 4n+1 约束、上限 81

**OOM fallback chain**（`fallback_profile()` 自动接管）：
- `rtx4070tis_quality` → `rtx4070tis_balanced` → `rtx4070tis_fast` → 进一步降 `chunk_seconds` 到 2.5

---

## 4. GPU 显存预算改动

### 4.1 audio_separator（[`tools/audio_separator/api_server.py`](../audio_separator/api_server.py)）

```python
# 现有
GPU_MEMORY_FRACTION = 0.5   # 6 GB on 12GB

# 改为环境变量驱动（向后兼容默认 0.5），4070 Ti Super 上设 0.3
GPU_MEMORY_FRACTION = float(os.environ.get("AUDIO_GPU_MEMORY_FRACTION", "0.5"))
```

`G:\audio` 部署时 `start.bat` 加：

```bat
set AUDIO_GPU_MEMORY_FRACTION=0.3
```

### 4.2 subtitle (VSR)（[`tools/subtitle/api_server.py`](./api_server.py)）

```python
# 现有
GPU_MEMORY_FRACTION = 0.5
PROPAINTER_MAX_LOAD_NUM_SHARED = 25

# 改为
GPU_MEMORY_FRACTION = float(os.environ.get("SUBTITLE_GPU_MEMORY_FRACTION", "0.5"))
PROPAINTER_MAX_LOAD_NUM_SHARED = int(os.environ.get("SUBTITLE_PROPAINTER_MAX_LOAD", "25"))
```

`G:\subtitle\start.bat` 加：

```bat
set SUBTITLE_GPU_MEMORY_FRACTION=0.3
set SUBTITLE_PROPAINTER_MAX_LOAD=70
```

### 4.3 VACE 服务（暂未启动）

阶段 3 把 backend 包装成 `tools/vace/api_server.py` 时，自然只在 VACE 进程持有显存。
4070 Ti Super 上 `set_per_process_memory_fraction(0.45)`（约 7.2 GB）已够 1.3B / 480p。

---

## 5. 跨进程 GPU lock（4070 Ti Super 必装）

3060 上是「nice-to-have」（双服务并发已经临界，OOM 概率高）；4070 Ti Super 上是
「**必装**」（三服务并发显存预算 15.5/16 GB，时间片重叠会瞬时爆）。

设计：
- 文件锁：`G:\gpu.lock`（Windows `msvcrt.locking` 或 `portalocker`）
- 三个服务在每次 GPU 推理 (`asyncio.to_thread(_run_xxx)`) 前 `flock`，结束 `funlock`
- 加超时（300s）防止死锁
- 单独写 `appcore/gpu_lock/file_lock.py` 模块复用

实现要点：
```python
# appcore/gpu_lock/file_lock.py
import contextlib
import time
from pathlib import Path

LOCK_PATH = Path(os.environ.get("GPU_LOCK_FILE", "G:/gpu.lock"))

@contextlib.contextmanager
def cross_process_gpu_lock(timeout: float = 300.0, poll: float = 0.5):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.touch(exist_ok=True)
    fp = LOCK_PATH.open("a+")
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                # Windows msvcrt.locking, non-blocking
                import msvcrt
                msvcrt.locking(fp.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"gpu lock timeout after {timeout}s")
                time.sleep(poll)
        yield
    finally:
        try:
            import msvcrt
            msvcrt.locking(fp.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fp.close()
```

接入点：
- audio: `_process_or_cache(...)` 里 `async with _gpu_lock:` 之后再嵌套 `with cross_process_gpu_lock():`
- subtitle: 同上
- VACE: `remover._process_chunk` 调 `run_invocation` 前

→ **关键**：lock 只保护**推理**阶段，不锁住 HTTP 入站/排队/缓存查询/上传，避免不必要的串行化。

---

## 6. CLI 默认 profile 切换

[`scripts/remove_subtitle_vace.py`](../../scripts/remove_subtitle_vace.py) 现在的 `--profile` 默认是 `rtx3060_safe`。

加一个根据 `nvidia-smi` 自动选 profile 的开关（**可选**）：

```python
def auto_detect_profile() -> str:
    """根据 GPU 名字猜默认 profile，未识别时回退 rtx3060_safe。"""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            text=True, timeout=5,
        ).strip()
    except Exception:
        return "rtx3060_safe"
    name = out.lower()
    if "5090" in name:
        return "rtx5090_balanced"   # 阶段 4 加
    if "4090" in name or "4080" in name:
        return "rtx4070tis_balanced"
    if "4070" in name and "ti super" in name:
        return "rtx4070tis_balanced"
    if "3060" in name:
        return "rtx3060_safe"
    return "rtx3060_safe"
```

CLI 用法：`--profile auto`（新值，触发自动探测）。

---

## 7. 测试改动

### 7.1 新增 [`tests/test_vace_subtitle/test_profile_4070tis.py`](../../tests/test_vace_subtitle/)

```python
def test_4070tis_profiles_registered():
    from appcore.vace_subtitle.config import PROFILES
    assert "rtx4070tis_fast" in PROFILES
    assert "rtx4070tis_balanced" in PROFILES
    assert "rtx4070tis_quality" in PROFILES

def test_4070tis_balanced_uses_1_3b_480p():
    from appcore.vace_subtitle.config import get_profile
    p = get_profile("rtx4070tis_balanced")
    assert p.model_name == "vace-1.3B"
    assert p.size == "480p"
    assert p.frame_num == 81
    assert p.offload_model is False    # 4070 Ti Super 显存够，关 offload 提速
    assert p.t5_cpu is False

def test_4070tis_quality_720p():
    from appcore.vace_subtitle.config import get_profile
    p = get_profile("rtx4070tis_quality")
    assert p.size == "720p"
    assert p.max_long_edge == 1280
    assert p.max_short_edge == 720

def test_4070tis_fallback_chain():
    from appcore.vace_subtitle.config import fallback_profile, get_profile
    quality = get_profile("rtx4070tis_quality")
    fb = fallback_profile(quality)
    assert fb is not None
    # quality -> balanced 或 quality -> fast 都可
    assert fb.size in ("480p", "720p")
    assert fb.frame_num <= quality.frame_num
```

### 7.2 [`tests/test_vace_subtitle/test_gpu_lock.py`](../../tests/test_vace_subtitle/)（新）

```python
def test_gpu_lock_acquires_and_releases(tmp_path, monkeypatch):
    monkeypatch.setenv("GPU_LOCK_FILE", str(tmp_path / "gpu.lock"))
    from appcore.gpu_lock.file_lock import cross_process_gpu_lock
    with cross_process_gpu_lock(timeout=1.0):
        pass  # 拿到 + 释放
    # 第二次仍能拿到（无遗留死锁）
    with cross_process_gpu_lock(timeout=1.0):
        pass

def test_gpu_lock_times_out_when_held_by_other(tmp_path, monkeypatch):
    # ...另起一个进程持锁 5 秒，本进程 timeout=0.5 应该 raise TimeoutError
```

### 7.3 既有测试不能破

- `test_chunking.py`：保持 85 passed
- `test_remover_dry_run.py`：保持 dry-run 不依赖真实 VACE
- `test_audio_separation_client.py`：20 passed 不变（client URL 路径不变）

---

## 8. 部署 checklist（4070 Ti Super 拿到手以后）

> 阶段 2 启动时按这个顺序操作。**当前别动**。

1. **驱动 + CUDA**：
   - [ ] 装 NVIDIA driver 560+
   - [ ] `nvidia-smi` 显示 `NVIDIA GeForce RTX 4070 Ti Super`，CUDA Driver 12.4+
2. **三个服务 venv 验证**：
   - [ ] `G:\audio\venv312\Scripts\python.exe -c "import torch; print(torch.cuda.get_device_name(0))"` → 显示 4070 Ti Super
   - [ ] `G:\subtitle\Python\python.exe` 同样检查
3. **改 start.bat 加环境变量**：
   - [ ] `G:\audio\start.bat` 加 `set AUDIO_GPU_MEMORY_FRACTION=0.3`
   - [ ] `G:\subtitle\start.bat` 加 `set SUBTITLE_GPU_MEMORY_FRACTION=0.3` + `set SUBTITLE_PROPAINTER_MAX_LOAD=70`
4. **重启三服务**（按之前 [`tools/gateway/README.md`](../gateway/README.md) 流程）
5. **跑回归测试**：
   - [ ] `pytest tests/test_audio_separation_client.py -q` → 20 passed
   - [ ] `curl http://172.30.254.12/separate/health` → `gpu_memory_limit: "4.8 GB (30%)"`
   - [ ] `curl http://172.30.254.12/subtitle/health` → 同上
6. **VACE 安装**（按 `docs/vace_windows_backend.md` §3）
   - [ ] `VACE_PROFILE=rtx4070tis_balanced` 写入用户环境变量
   - [ ] dry-run：`python scripts\remove_subtitle_vace.py --input X.mp4 --output Y.mp4 --dry-run --profile rtx4070tis_balanced` → manifest 显示 `"frame_num": 81, "size": "480p", "offload_model": false`
7. **真实端到端**：
   - [ ] 1080P / 5s 测试视频，跑 `rtx4070tis_balanced`，确认输出无 OOM、合成保真
   - [ ] 1080P / 5s + 同时 audio/subtitle 各发一个请求 → 三服务并发不爆显存
   - [ ] 记录速度，回填本文 §2.2 估算表

---

## 9. 实施顺序（给 codex 的建议执行顺序）

不要一口气改完所有东西；按这个顺序 commit 拆开，每步可独立 review：

### Commit 1：4070 Ti Super profile 注册 + 测试
- 修改 `appcore/vace_subtitle/config.py`：在 `PROFILES` dict 加 3 个 `rtx4070tis_*`
- 新增 `tests/test_vace_subtitle/test_profile_4070tis.py`：4 个测试
- 不改任何运行时代码
- 期望：86+ passed（原 85 + 新 4）

### Commit 2：audio + subtitle 显存配额走环境变量
- 修改 `tools/audio_separator/api_server.py`：`GPU_MEMORY_FRACTION` 改为 env 读
- 修改 `tools/subtitle/api_server.py`：`GPU_MEMORY_FRACTION` + `PROPAINTER_MAX_LOAD_NUM_SHARED` 改为 env 读
- 同步两个 `start.bat`：默认值仍是 0.5 / 25（保持当前 3060 行为），4070 Ti Super 通过环境变量切换
- 不破坏现有 audio mock 测试（20 passed）

### Commit 3：跨进程 GPU lock 模块 + 测试
- 新增 `appcore/gpu_lock/file_lock.py`
- 新增 `tests/test_gpu_lock/`（不放进 vace_subtitle 子目录，跨服务共享）
- 不接入到 audio/subtitle/VACE 任何路径——仅模块单元测试

### Commit 4：把 GPU lock 接入三服务（**可推迟到阶段 2 实测后**）
- audio: `_process_or_cache` 里 GPU lock 嵌套
- subtitle: 同上
- VACE: `remover._process_chunk` 调 VACE 前
- 加环境开关 `GPU_CROSS_PROCESS_LOCK=1` 默认关，方便 3060 单卡场景关掉

### Commit 5：CLI auto-detect profile（可选）
- `scripts/remove_subtitle_vace.py` 加 `auto_detect_profile()`
- `--profile auto` 触发探测

### Commit 6：文档串联
- 更新 [`docs/vace_windows_backend.md`](../../docs/vace_windows_backend.md) 加 4070 Ti Super 章节
- 更新 [`docs/services_overview.md`](../../docs/services_overview.md) 在 GPU 配额表加 4070 Ti Super 列
- 引用本文作为权威实施规格

---

## 10. 不要做的事

- ❌ **不要**实跑 VACE（阶段 1 边界尚未越过）
- ❌ **不要**新增 14B 支持代码（4070 Ti Super 16 GB 装不下）
- ❌ **不要**升 PyTorch 到 nightly（4070 Ti Super 不需要）
- ❌ **不要**改默认 `GPU_MEMORY_FRACTION` 数字（保持 0.5 兼容当前 3060 部署，仅通过 env 切换）
- ❌ **不要**改 audio/subtitle 现有 API（无 breaking change）
- ❌ **不要** merge master 直到阶段 2 实测通过

---

## 11. 验收标准

阶段 2 之前，**仅靠不实跑 VACE** 就能验收的项：

| 项 | 验收方法 |
|---|---|
| 3 个 4070 Ti Super profile 注册 | `pytest tests/test_vace_subtitle/test_profile_4070tis.py -v` 全过 |
| profile 字段正确 | manifest dry-run 显示 `offload_model=false, t5_cpu=false, frame_num=81` |
| audio/subtitle env 显存配额 | 设 env 后 `/separate/health` 显示 `4.8 GB (30%)` |
| GPU lock 模块独立可用 | `pytest tests/test_gpu_lock -v` 全过；并发持锁/超时分支都被覆盖 |
| OOM fallback chain 含 4070 Ti Super | `pytest tests/test_vace_subtitle/test_oom_fallback.py -v` 加新参数化 |
| 现有测试 0 破坏 | `pytest tests/test_vace_subtitle tests/test_audio_separation_client.py -q` ≥ 105 passed |
| dry-run CLI 输出含 4070 Ti Super profile | `scripts/remove_subtitle_vace.py --profile rtx4070tis_balanced --dry-run` 成功 |

阶段 2 实测后追加的项：
- 1080P / 5s 视频 `rtx4070tis_balanced` 端到端跑通无 OOM
- 三服务并发显存峰值 ≤ 15.5 GB
- 速度数据回填本文 §2.2

---

## 附录 A：和 RTX 5090 的差异（如果以后升 5090）

| 项 | 4070 Ti Super 16 GB | 5090 32 GB |
|---|---|---|
| VACE 14B | 不可用 | 可用 |
| profile 命名 | `rtx4070tis_*` | `rtx5090_*`（待加） |
| `GPU_MEMORY_FRACTION` | 0.3 共卡 / 0.45 独占 | 0.3 共卡 / 0.6 独占 |
| `t5_cpu` | False | False |
| `offload_model` | False | False |
| PyTorch wheel | cu124/cu126 现成 | **cu128 nightly** 必需 |
| 三服务并发 | 临界 (15.5/16) | 富余 (~22/32) |

→ 升 5090 时只需新加 `rtx5090_*` profile + 升 PyTorch；本卡的 GPU lock /
env 配额机制 **复用即可**。

## 附录 B：参考文档

- [`docs/vace_windows_backend.md`](../../docs/vace_windows_backend.md)——VACE backend 总文档（适用 / 不适用 / 安装 / CLI）
- [`docs/services_overview.md`](../../docs/services_overview.md)——本机三服务总览
- [`appcore/vace_subtitle/config.py`](../../appcore/vace_subtitle/config.py)——现有 PROFILES 表（要改的地方）
- [`tools/audio_separator/api_server.py:50`](../audio_separator/api_server.py)——`GPU_MEMORY_FRACTION` 定义
- [`tools/subtitle/api_server.py`](./api_server.py)——同上 + `PROPAINTER_MAX_LOAD_NUM_SHARED`
- [`scripts/remove_subtitle_vace.py`](../../scripts/remove_subtitle_vace.py)——CLI 入口

