# AutoVideoSrtLocal
视频翻译 + 电商运营数据分析的内部一体化工具。Web 端 Flask + Playwright/CDP 自动化 + 多 provider LLM 编排。本文件是 agent-agnostic 主指南，CLAUDE.md / GEMINI.md 都从这里 import。

## Stack
- Python 3.12 / Flask / SQLite（生产单机）/ gunicorn + systemd
- Playwright sync API（CDP 接管浏览器）；不要在 asyncio loop 内直接调用 sync API
- LLM 统一入口 `appcore.llm_client`（OpenRouter / Doubao / Gemini AIStudio / Vertex）
- 部署：本机 = 生产（172.30.254.14），`/opt/autovideosrt`，`autovideosrt.service`

## Structure
- `appcore/` — 业务逻辑、LLM 编排、定时任务、广告/订单分析
- `pipeline/` — 视频翻译 / TTS / 字幕处理
- `tools/` — 子工具（`shopify_image_localizer`、`meta_daily_final_sync`、`dianxiaomi_order_freshness_watchdog` 等）
- `web/` — Flask 路由 + 模板 + 静态资源；蓝图 url_prefix 注意 `/medias`
- `link_check_desktop/` — 桌面端链接巡检 GUI
- `docs/superpowers/specs/` — 唯一事实来源；`plans/` 是历史计划
- `tests/` — pytest

## Commands
- Web dev: `python -m web.app`（默认 5000，本地起空闲端口避免撞生产）
- Link Check Desktop dev: `python -m link_check_desktop.main`
- Shopify Image Localizer dev: `python -m tools.shopify_image_localizer.main`
- Test: `pytest -q`
- 测试账号: `admin / 709709@`（[testuser.md](testuser.md)）

## Verification（每次改动后顺序执行）
1. 跑相关 `pytest <files> -q` 通过
2. 起 dev server，未登录路由必须 302（不能 500）
3. 登录后 200；新路由必须 `@login_required + @admin_required`
4. POST 前端必带 `X-CSRFToken`（从 `layout.html` meta 读）

## 硬红线（违反 = 立即停手）
- **主工作目录零污染**：除非用户当条消息明确说「马上 hotfix」，否则只能在 `git worktree add` 隔离目录里改代码。
- **`master` 只 hotfix**：常规需求 / 重构 / 跨模块改动一律走 worktree。
- **文档驱动代码**：改代码前必须先有文档锚点（本文件 / spec / 模块级 `CLAUDE.md`）。无锚点 = 无授权。
- **本机即生产**：`/opt/autovideosrt` 是 root 拥有；用户明确「发测试 / 上线」时，Windows 开发机可直接 `ssh root@172.30.254.14`，Ubuntu 服务器上只操作 `/opt/autovideosrt-test` / `/opt/autovideosrt`。禁用 SSH 跳板、`gh auth login`。
- **服务重启需明示**：用户没说「发测试 / 上线」就别 `systemctl restart`；默认验证去 `http://172.30.254.14:8080/` 测试环境。
- **DB 凭据走 `infra_credentials`**：不要只改 `.env`；UI `/settings?tab=infrastructure` 是首选。
- **定时任务一律登记**：APScheduler / systemd timer / crontab / 后台轮询，全部同步到 `appcore/scheduled_tasks.py` + Web 后台「定时任务」模块。
- **Wine 打包**：Wine ≥ 11、Windows Python ≥ 3.12.10，build 必须 `xvfb-run`，`wine ./*.exe` 不能当 smoke。
- **AGENTS.md / CLAUDE.md ≤ 80 行**：超量内容下沉到 spec 或模块级 `CLAUDE.md`。

## 主题指引（按需读对应文档，不要内联进本文件）
- LLM 调用：`docs/superpowers/plans/2026-04-19-llm-call-unification.md`；新业务在 `appcore/llm_use_cases.py` 注册 use case
- 数据分析数据质量护栏：`docs/analytics-data-quality-guardrails.md`；所有 `/order-profit/*` 等 JSON 顶层带 `data_quality`，逻辑集中在 `appcore/order_analytics/data_quality.py`
- SKU 实际保本 ROAS：`docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md`；每天 01:00 由 `tools/sku_actual_roas_snapshot.py` 计算 `D-32` 到 `D-3`，快照表 `sku_actual_breakeven_roas_snapshots`
- Meta 多账户广告：`2026-05-07-meta-ads-multi-account-design.md` 起串读补丁 — `2026-05-09-ads-purchase-value-order-fallback-design.md` / `2026-05-09-meta-ads-xhr-token-channel.md` / `2026-05-09-meta-ads-account-timezone-and-async-fix.md` / `2026-05-09-meta-daily-final-permission-recovery.md`
- TTS / 音频：变速短路见 `2026-05-13-tts-deferred-adaptive-speedup-design.md` + `2026-05-13-tts-segment-candidate-assembly-design.md`；背景保留分离见 `2026-05-14-audio-separation-background-preserve-design.md`
- 实时大盘 / 业务日对齐：`docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md` + `2026-05-09-realtime-dashboard-store-filter.md` + `2026-05-10-realtime-dashboard-profit-margin.md`
- 店小秘 watchdog / ROI 锁告警：`docs/superpowers/specs/2026-05-09-dianxiaomi-order-freshness-watchdog.md` + `2026-05-09-roi-hourly-sync-lock-recovery.md`
- 选品/产品链接体系：3 份 `2026-05-09-product-link-*.md`; 顶部国家勾选前置校验 `2026-05-09-product-edit-ad-supported-langs-precheck-design.md`; TABCUT 价格筛选 `2026-05-13-tabcut-video-price-filter-design.md`
- Shopify Image Localizer：发布/打包/API key/BOM/CDP 门禁必须先读 [tools/shopify_image_localizer/CLAUDE.md](tools/shopify_image_localizer/CLAUDE.md)；配置门禁见 `2026-05-11-shopify-image-localizer-runtime-config-release-guard.md`
- 模板/静态资源/订单分析：见 [web/templates/CLAUDE.md](web/templates/CLAUDE.md)、[web/static/CLAUDE.md](web/static/CLAUDE.md)、[appcore/order_analytics/CLAUDE.md](appcore/order_analytics/CLAUDE.md)
- 任务中心端到端流程：`2026-05-16-task-center-e2e-flow-design.md`（选品→任务→素材→推送全链路补全）

## 发布（Windows 开发机直连 root；Ubuntu 服务器本地目录操作）
```bash
git push origin HEAD:master
ssh -i C:/Users/admin/.ssh/CC.pem root@172.30.254.14 '
set -e
cd /opt/autovideosrt-test && git pull origin master --ff-only
systemctl restart autovideosrt-test && sleep 3
systemctl is-active autovideosrt-test
curl -s -o /dev/null -w "TEST HTTP %{http_code}\n" http://127.0.0.1:8080/
cd /opt/autovideosrt && git pull origin master --ff-only
if ! cmp -s deploy/autovideosrt.service /etc/systemd/system/autovideosrt.service; then
  cp deploy/autovideosrt.service /etc/systemd/system/ && systemctl daemon-reload
fi
systemctl restart autovideosrt && sleep 3
systemctl is-active autovideosrt
curl -s -o /dev/null -w "PROD HTTP %{http_code}\n" http://127.0.0.1/
'
```
验收：`active` + HTTP 200/302；404/500/000 = 失败。

## Don't
- 不在主工作目录改代码（除非明确 hotfix）；不调 `deploy/publish.sh`、不用 SSH 跳板、不 `gh auth login`
- 不在 Playwright `wait_for_*` 处替换为 `time.sleep` / `cancellable_sleep`（EZ/CDP 等待事故）
- 不 `{% include base_with_extends %}` 后追加 raw HTML（Jinja 继承事故）
- 不直接 `UPDATE meta_ad_accounts` 绕过服务层；不硬编码 `site_code -> ad_account_id`
- 不在 `meta_ad_realtime_*` fallback 里 `GROUP BY business_date` 取全局 `MAX(snapshot_at)`（必须 `(business_date, ad_account_id)`，2026-05-08 newjoyloo_bak 事故）
