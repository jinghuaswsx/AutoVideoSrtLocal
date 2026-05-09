# meta_daily_final 凌晨 PermissionError 恢复设计

最后更新：2026-05-09

## 文档锚点

- `CLAUDE.md` 「Meta 广告多账户同步（2026-05-07 起）」段：定义了 `output/meta_daily_final_exports/<date>/<ts>/<account.code>/` 目录布局。
- `CLAUDE.md` 「本机部署到线上的标准流程（Claude Code agent 必读）」段：`/opt/autovideosrt/` 属 `root`，agent 写需 sudo，构成跨用户写入同一 export 目录的前提。
- `docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md`：多账户 export 子目录布局来源。
- `docs/superpowers/specs/2026-05-08-feishu-bot-alerts-design.md`：失败告警接入点（`finish_run` → `_dispatch_failure_alert`）。
- `docs/superpowers/specs/2026-05-09-roi-hourly-sync-lock-recovery.md`：告警节流 / 恢复消息（`should_dispatch_failure` + `_dispatch_recovery_alert`）由 AUT-21 已落地；本 spec 不再重复定义节流策略，仅复用其入口并在 streak ≥ 2 时注入 `consecutive_failures` 字段。
- `appcore/scheduled_tasks.py:_dispatch_failure_alert`：现有告警入口。
- `tools/meta_daily_final_sync.py:_run_meta_ads_export`：受影响的 mkdir 现场。
- `deploy/server_browser/autovideosrt-meta-daily-final-sync.service` / `-check.service`：systemd 入口。

## 背景

2026-05-09 BJ 00:11 `meta_daily_final` 失败：

```
[newjoyloo_bak] [Errno 13] Permission denied:
  '/opt/autovideosrt/output/meta_daily_final_exports/2026-05-08/20260509_001149'
[Omurio]        [Errno 13] Permission denied:
  '/opt/autovideosrt/output/meta_daily_final_exports/2026-05-08/20260509_001149'
```

紧接着 5/9 00:12 重试以 `Meta Ads Manager final daily export failed with code 1` 失败。BJ 5/9 15:37–15:50 终于跑通入库，但 0–14 小时 BJ 业务日 5/8 daily_final 数据延迟。5/7 也有过同目录 PermissionError。

## 根因

`tools/meta_daily_final_sync.run_final_sync` 给每次执行造一个时间戳目录：

```
META_DAILY_FINAL_EXPORT_ROOT
  / <target_date.isoformat()>
  / <YYYYMMDD_HHMMSS>
  / <account.code>
```

`<target_date>` 父目录跨 run 复用，命中早先用别的 user（例如手工 `sudo python tools/meta_daily_final_sync.py ...`）创建的子目录后，service 进程 `mkdir(<ts>)` 即 PermissionError。`_run_meta_ads_export` 之前直接 `export_dir.mkdir(parents=True, exist_ok=True)`，无回退。

## 行为

### 1. 代码层 mkdir 自愈

新增 `tools/meta_daily_final_sync._ensure_export_dir(path, recovery_log)`：

1. 优先 `path.mkdir(parents=True, exist_ok=True)`。
2. 命中 `PermissionError`：
   1. 沿 `path` 向上找第一个不可写的祖先（必须仍在 `META_DAILY_FINAL_EXPORT_ROOT` 之下，绝不动 root 本身）。
   2. 把该祖先重命名为 `<原名>.conflicted-<bj_ts>`（不删除，运维事后可手工分析或回收）。
   3. 重试 `mkdir(parents=True, exist_ok=True)`。
   4. 把 `{blocker, relocated_to, target_path}` 追加到 `recovery_log`。
3. 仍失败：抛出**清晰的** `PermissionError`，正文带 blocker 路径与重定位失败原因；`run_final_sync` 把它当作账户级失败处理，整体 run 标 failed → 走告警链路。

`_run_meta_ads_export(target_date, export_dir, account, *, include_adsets=False, recovery_log=None)` 调用该 helper；`run_final_sync` 在每次 run 起始建一个 `export_dir_recovery: list` 并塞进 `summary["export_dir_recovery"]`，命中时人能直接在 `scheduled_task_runs.summary_json` 上看到恢复动作。

### 2. systemd 层默认权限

`autovideosrt-meta-daily-final-sync.service` 与 `-check.service` 均加：

```ini
ExecStartPre=/usr/bin/install -d -o root -g root -m 02775 /opt/autovideosrt/output/meta_daily_final_exports
```

`02775` 含 setgid，即便后续有非 root 进程在此目录建子目录，子目录组主仍为 `root`，模式继承组写权限，避免新增 user 又踩同款坑。两个 unit 都跑 `User=root, Group=root`，预创建只是 idempotent 校正。

### 3. 告警 row 上注入 `consecutive_failures`

`appcore/scheduled_tasks._dispatch_failure_alert` 在确认要发飞书后，如果 AUT-21 的 `should_dispatch_failure` 返回 streak ≥ 2，就把 `row["consecutive_failures"] = streak` 写回再调 `feishu_alerts.send_scheduled_task_failure(row)`。`format_scheduled_task_failure` 在 row 含该字段时多渲染一行 `连续失败：N 次`，方便事故定位。

实际去重 / 节流策略继续沿用 [2026-05-09-roi-hourly-sync-lock-recovery.md](2026-05-09-roi-hourly-sync-lock-recovery.md)：

- 首次 failed → 立即发。
- 连续 failed 之后每 N 次（`system_settings.feishu_alerts.failure_repeat_every`，默认 5）发一次。
- failed → success 由 `_dispatch_recovery_alert` 推送恢复消息（含此前连续失败次数）。

本 spec 不引入新的阈值常量，也不与 AUT-21 的非目标冲突——只是在已有 row 上多带一个字段。

## 验收

| 场景 | 预期 |
|------|------|
| `_ensure_export_dir(<新路径>)` | 直接 mkdir 成功，不走 fallback |
| `<date>/` 已被 root-only 子目录占据，service 跑 `_ensure_export_dir(<date>/<ts>/<code>)` | 把 `<date>` 改名为 `<date>.conflicted-<bj_ts>`，重新 mkdir 干净链路；`recovery_log` 追加一条 |
| 重命名同样无权限 | 抛 PermissionError，错误信息含 blocker 路径，run 标 failed |
| 首次 daily_final 失败（streak=1） | 飞书发出，无 `连续失败：N 次` 行（沿用 AUT-21 策略） |
| 连续 2 次 daily_final 失败（streak=2） | AUT-21 节流可能跳过本次发送；若发，消息含「连续失败：2 次」 |
| 连续 5 次（streak=5） | 飞书发出，消息含「连续失败：5 次」 |
| failed → success | `_dispatch_recovery_alert` 发恢复消息（AUT-21） |

## 测试

新增 `tests/test_meta_daily_final_export_dir.py`：

- 正常路径：`_ensure_export_dir(tmp_path / "a/b/c")` → 创建成功，`recovery_log == []`。
- 冲突路径：先 `os.chmod(<parent>, 0o500)` 模拟不可写；`_ensure_export_dir(...)` 把它改名 + 重建；`recovery_log` 含一条记录。
- 持续冲突：mock `Path.rename` 抛 `OSError` → 抛 `PermissionError` 且消息含 blocker 路径。

扩展 `tests/test_appcore_scheduled_tasks.py`：

- streak ≥ 2 时（AUT-21 dedup 决定发送）→ row 含 `consecutive_failures=streak`。
- streak == 1（首次失败）→ row 仍发送，但不含 `consecutive_failures` 字段。
- AUT-21 dedup 决定不发 → 不调 `send_scheduled_task_failure`（已由 AUT-21 自带 case 覆盖，这里不重复）。

扩展 `tests/test_server_browser_runtime.py::test_meta_daily_final_units_use_dxm01_meta_without_shared_lock_and_staggered_timers`：断言两个 unit 都含 `ExecStartPre=/usr/bin/install -d` 行。

聚焦回归：

```
pytest tests/test_meta_daily_final_export_dir.py \
       tests/test_appcore_scheduled_tasks.py \
       tests/test_feishu_alerts.py \
       tests/test_meta_login_retry.py \
       tests/test_meta_server_sync_tools.py \
       tests/test_server_browser_runtime.py -q
```

## 非目标

- 不做 daily_final 数据补跑队列；现有 17:00 check + 手动同步 Tab 已能补。
- 不在仓库里维护 `output/meta_daily_final_exports.conflicted-*` 的清理 cron；运维侧手工。
- 不做飞书告警的多群路由 / 静默时段（沿用现有 spec 「## 非目标」）。

## 实现记录

待实施完成后追加。
