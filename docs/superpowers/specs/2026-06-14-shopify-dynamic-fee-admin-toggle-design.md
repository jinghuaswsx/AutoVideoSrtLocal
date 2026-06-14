# 手续费真实优先开关搬进超管设置（toggle） Design

> 关联：`docs/superpowers/specs/2026-06-14-cost-accounting-real-data-first-design.md` §6.2（真实优先手续费总开关）。本 spec 只负责把已有的 `SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT` 开关从 env/config 搬到超管 UI 的一个 toggle，**不改手续费计算逻辑本身**。

## 目标

让超级管理员在「系统设置」页用一个 toggle 开关控制手续费真实优先链路的启停，**切换后无需重启服务即时生效**（最多 ~30s），不再依赖改环境变量 + 重启。

## 背景与现状

- 手续费 resolver `shopify_fee_resolver.resolve_shopify_fee_for_order` 已实现真实优先三级链路：`actual_payment → dynamic_region_rate → strategy_c_fallback`。
- 总开关 `shopify_fee_resolver.is_dynamic_fee_effective(order_time)` 当前只读 `os.getenv → Config.SHOPIFY_DYNAMIC_FEE_EFFECTIVE_AT`（一个「生效时间戳」，`config.py:80` 默认 `2026-01-01T00:00:00+08:00`）。改这个值要重启服务才生效。
- 超管「系统设置」页 = `web/routes/admin.py::settings`（路由 `/admin/settings`，`@login_required @admin_required`，general/domains 两个 tab）渲染 `web/templates/admin_settings.html`；现有业务配置项（汇率 `material_roas_rmb_per_usd`、广告阈值、TTS 并发、保留期）都走 `request.form.get(...)` + `appcore.settings.set_setting(...)` 保存、`get_setting(...)` 回显。

## 设计决策（已与用户确认）

1. **纯开/关 toggle**：ON = 全量启用真实优先；OFF = 全部回退策略 C 估算。不做日期分界、不做三态、不做按店铺分别开关（YAGNI）。
2. **优先级 system_settings(UI) > env > config 默认**：超管 UI 点了即准，不被 env/config 暗中覆盖。
3. **切换不自动重算**：只改后续计算与实时大盘未落库订单；历史已落库手续费是快照，需单独跑全量重算才更新。UI 旁注说明。
4. **进程内缓存 TTL ~30s**：`is_dynamic_fee_effective` 是每单热路径，缓存 system_settings 值避免每单查 DB；代价是切换后最多 ~30s 全 worker 生效（非瞬时）。

## 架构与组件

### 组件 1：system_settings 键

- 新增键 `shopify_dynamic_fee_enabled`，值 `"1"`（开）/ `"0"`（关）。复用现有 `appcore.settings.get_setting` / `set_setting`。
- 未设置（NULL）= 未通过 UI 干预过 → 回退 env/config 行为（保持当前线上默认=开）。

### 组件 2：resolver 读取 + 缓存（`shopify_fee_resolver.py`）

新增进程内缓存读取（线程安全，TTL 30s，沿用 `_open_day_freshness.py` 的 `threading.Lock` + 单调时钟模式）：

```python
import threading, time
_TOGGLE_CACHE_TTL = 30.0
_toggle_lock = threading.Lock()
_toggle_cache = {"value": None, "fetched_at": 0.0, "primed": False}

def _read_dynamic_fee_toggle() -> str | None:
    """读 system_settings.shopify_dynamic_fee_enabled，进程内缓存 30s。
    DB 异常时返回 None（回退 env/config），不抛错。"""
    now = time.monotonic()
    with _toggle_lock:
        if _toggle_cache["primed"] and now - _toggle_cache["fetched_at"] < _TOGGLE_CACHE_TTL:
            return _toggle_cache["value"]
    try:
        from appcore.settings import get_setting
        value = get_setting("shopify_dynamic_fee_enabled")
    except Exception:
        value = None
    with _toggle_lock:
        _toggle_cache.update(value=value, fetched_at=now, primed=True)
    return value

def invalidate_dynamic_fee_toggle_cache() -> None:
    """保存设置后由路由调用，立即失效本进程缓存（其他 worker 靠 TTL 收敛）。"""
    with _toggle_lock:
        _toggle_cache["primed"] = False
```

`is_dynamic_fee_effective(order_time)` 改为：

```python
def is_dynamic_fee_effective(order_time):
    toggle = _read_dynamic_fee_toggle()
    if toggle == "0":
        return False                      # UI 显式关闭 → 全部策略 C
    if toggle == "1":
        return order_time is not None     # UI 显式开启 → 全量真实优先（忽略 env/config 日期）
    # toggle 未设 → 回退现有 env/config effective_at 逻辑（保持当前线上行为）
    effective_at = _parse_effective_at()
    if effective_at is None or order_time is None:
        return False
    comparable = order_time
    if comparable.tzinfo is not None:
        comparable = comparable.astimezone(timezone.utc).replace(tzinfo=None)
    return comparable >= effective_at
```

> 注：`toggle == "1"` 时直接 `order_time is not None`（全量），这才让「UI > env」成立——否则 env 设的未来日期会盖过 UI 的「开」。`order_time is None`（数据缺时间）仍返回 False，与现有保守口径一致。

### 组件 3：超管 UI（`web/routes/admin.py::settings` + `admin_settings.html`）

- **POST（general tab）**：HTML checkbox 未勾选时不进 form，故用存在性判断：
  ```python
  enabled = "1" if request.form.get("shopify_dynamic_fee_enabled") else "0"
  set_setting("shopify_dynamic_fee_enabled", enabled)
  from appcore.order_analytics.shopify_fee_resolver import invalidate_dynamic_fee_toggle_cache
  invalidate_dynamic_fee_toggle_cache()
  ```
- **GET**：读当前值传模板：
  ```python
  shopify_dynamic_fee_enabled = (get_setting("shopify_dynamic_fee_enabled") != "0")
  # NULL/“1” → 视作开（与回退默认一致）；仅显式“0”→关
  ```
  传入 `render_template("admin_settings.html", ..., shopify_dynamic_fee_enabled=...)`。
- **模板**：general tab 表单加一个 toggle（沿用页面现有 checkbox/switch 样式），`name="shopify_dynamic_fee_enabled"`，`{% if shopify_dynamic_fee_enabled %}checked{% endif %}`。旁注：「关闭后新订单与实时大盘未落库订单的手续费回退策略 C 估算；历史已落库数字需单独跑全量重算才更新。」

## 数据流

```
超管勾选/取消 toggle → POST /admin/settings (general)
  → set_setting("shopify_dynamic_fee_enabled","1"/"0")
  → invalidate_dynamic_fee_toggle_cache()（本 worker 立即失效）
后续手续费计算（backfill / 实时大盘未落库订单）：
  resolve_shopify_fee_for_order → is_dynamic_fee_effective → _read_dynamic_fee_toggle()（缓存 30s）
    "0" → strategy C ；"1" → 真实优先 ；NULL → env/config 默认
```

## 优先级表

| system_settings | env / config | is_dynamic_fee_effective(有效 order_time) |
|---|---|---|
| `"1"` | 任意 | True（全量真实优先） |
| `"0"` | 任意 | False（全部策略 C） |
| 未设 | effective_at 已配（默认 2026-01-01） | 按 order_time ≥ effective_at |
| 未设 | effective_at 空 | False |

## 错误处理

- `get_setting` DB 异常 → `_read_dynamic_fee_toggle` 返回 None → 回退 env/config，不影响手续费计算、不抛错。
- POST 保存 toggle 不做额外校验（只有勾/不勾两态），与现有 general tab 其他项一致；保存失败走现有 flash 提示路径。
- 多 gunicorn worker：保存只失效当前 worker 缓存，其他 worker 最多 30s 后经 TTL 自然刷新——可接受（设计点 4）。

## 测试

- `tests/test_shopify_fee_dynamic.py` 追加：
  - `_read_dynamic_fee_toggle` 缓存命中/过期/DB 异常回退 None；`invalidate_dynamic_fee_toggle_cache` 立即失效。
  - `is_dynamic_fee_effective` 三态 × env/config 组合：toggle="1"→全量 True（即使 env 设未来日）；toggle="0"→False（即使 env 设过去日）；toggle=None→沿用 env/config。
- `tests/test_admin_settings_routes.py`（或现有 admin settings 路由测试）追加：
  - POST general 勾选 → `set_setting("shopify_dynamic_fee_enabled","1")` + 调用 invalidate；不勾选 → `"0"`。
  - GET 渲染 `shopify_dynamic_fee_enabled` 上下文：NULL/"1"→True，"0"→False。

## 范围之外（YAGNI）

- 不做日期分界 / 三态 / 按店铺分别开关。
- 不在切换时触发任何重算。
- 不动 resolver 三级链路、动态快照生成、payments 导入重算等既有逻辑。

## 实现锚点

| 改动 | 文件 |
|---|---|
| 缓存 + toggle 读取 + is_dynamic_fee_effective | `appcore/order_analytics/shopify_fee_resolver.py`（`_parse_effective_at` / `is_dynamic_fee_effective` 附近，约 :50–:75） |
| POST 保存 + invalidate + GET 回显 | `web/routes/admin.py::settings`（:464 POST 段、:564 render_template） |
| toggle 控件 + 旁注 | `web/templates/admin_settings.html`（general tab 表单，约 :229–:290） |
| resolver 单测 | `tests/test_shopify_fee_dynamic.py` |
| 路由单测 | admin settings 路由测试文件 |
