from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import datetime
from functools import wraps
from typing import Any

from appcore.db import execute, query

log = logging.getLogger(__name__)

TaskDefinition = dict[str, Any]

META_HOT_POSTS_VIDEO_LOCALIZATION_TASK_CODE = "meta_hot_posts_video_localization_tick"
FAILURE_ALERT_MIN_CONSECUTIVE_RUNS = 20
FAILURE_ALERT_MIN_SAMPLE_ATTEMPTS = 20
FAILURE_ALERT_FAILURE_RATE_THRESHOLD = 0.80
VIDEO_LOCALIZATION_ALERT_MIN_DAILY_ATTEMPTS = FAILURE_ALERT_MIN_SAMPLE_ATTEMPTS
VIDEO_LOCALIZATION_ALERT_FAILURE_RATE_THRESHOLD = FAILURE_ALERT_FAILURE_RATE_THRESHOLD

TASK_DEFINITIONS: dict[str, TaskDefinition] = {
    "shopifyid": {
        "code": "shopifyid",
        "name": "Shopify ID 获取",
        "description": "每天从店小秘 Shopify 在线商品库抓取 shopifyProductId，并回填 media_products.shopifyid。",
        "schedule": "每天 12:11（与 ROI :00/:20/:40 错峰）",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-shopifyid-sync.timer",
        "runner": "tools/shopifyid_dianxiaomi_sync.py",
        "deployment": "线上已启用",
        "log_table": "scheduled_task_runs",
    },
    "dianxiaomi_sku": {
        "code": "dianxiaomi_sku",
        "name": "店小秘 SKU 配对同步",
        "description": (
            "每天从店小秘 Shopify 在线商品库与 ERP 商品管理库抓取 variants 与 SKU，"
            "按 shopifyid 回填 media_products.shopify_title 和 media_product_skus 配对表。"
        ),
        "schedule": "每天 12:21（与 shopifyid 12:11 错峰）",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-dianxiaomi-sku-sync.timer",
        "runner": "tools/dianxiaomi_sku_sync.py",
        "deployment": "线上待部署",
        "log_table": "scheduled_task_runs",
    },
    "shopifyid_windows_daily": {
        "code": "shopifyid_windows_daily",
        "name": "Shopify ID 获取（Windows 本机）",
        "description": "Windows 计划任务每天触发店小秘 Shopify ID 同步脚本，作为本机运行入口登记。",
        "schedule": "已停用（原每天 12:10；如重新启用建议 12:11）",
        "source_type": "windows",
        "source_label": "Windows 计划任务",
        "source_ref": "AutoVideoSrtLocal-ShopifyIdDianxiaomiSyncDaily",
        "runner": "tools/shopifyid_dianxiaomi_sync_daily.ps1",
        "deployment": "本机运维任务",
        "log_table": "",
        "output_file": "output/shopifyid_dianxiaomi_sync/",
        "default_enabled": False,
    },
    "roi_hourly_sync": {
        "code": "roi_hourly_sync",
        "name": "店小秘订单与 ROAS 实时同步",
        "description": "每 20 分钟同步店小秘订单、Meta 广告数据，并刷新真实 ROAS 小时事实与日内快照。",
        "schedule": "每 20 分钟（每小时 :00/:20/:40）",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-roi-realtime-sync.timer",
        "runner": "tools/roi_hourly_sync.py",
        "deployment": "线上已启用",
        "log_table": "roi_hourly_sync_runs",
    },
    "dianxiaomi_order_import": {
        "code": "dianxiaomi_order_import",
        "name": "店小秘订单导入",
        "description": "ROI 实时同步中的店小秘订单导入子任务，记录订单抓取、明细入库和跳过数量。",
        "schedule": "每 1 小时（随 ROI :02 触发）",
        "source_type": "subtask",
        "source_label": "ROI 同步子任务",
        "source_ref": "autovideosrt-roi-realtime-sync.timer",
        "runner": "tools/dianxiaomi_order_import.py（由 tools/roi_hourly_sync.py 调用）",
        "deployment": "线上已启用",
        "log_table": "dianxiaomi_order_import_batches",
    },
    "order_profit_incremental": {
        "code": "order_profit_incremental",
        "name": "订单利润增量核算",
        "description": "增量重算最近 2 天订单的 SKU 行利润（含 Shopify 手续费、广告分摊、采购、小包、退货占用 1%），upsert 到 order_profit_lines。完备性失败的 SKU 标 incomplete 不出数字。",
        "schedule": "建议每 20 分钟（与 ROI 同步频率一致）",
        "source_type": "systemd",
        "source_label": "Linux systemd timer（待启用）",
        "source_ref": "autovideosrt-order-profit-incremental.timer",
        "runner": "tools/order_profit_incremental.py",
        "deployment": "需手工配置 systemd timer 或追加到 ROI 同步流程后",
        "log_table": "order_profit_runs",
    },
    "auto_update_packet_costs": {
        "code": "auto_update_packet_costs",
        "name": "产品小包成本自动更新",
        "description": (
            "每天从 dianxiaomi_order_lines.logistic_fee 聚合各产品的实际小包成本："
            "packet_cost_actual=均值、packet_cost_estimated=中位数。样本≥5 才更新。"
        ),
        "schedule": "每天凌晨 3:07",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-auto-update-packet-costs.timer",
        "runner": "tools/auto_update_packet_costs.py",
        "deployment": "待部署",
        "log_table": "scheduled_task_runs",
    },
    "sku_actual_breakeven_roas": {
        "code": "sku_actual_breakeven_roas",
        "name": "SKU 实际保本 ROAS 快照",
        "description": (
            "每天北京时间 01:00 计算三天前结束的滚动 30 天订单窗口，"
            "按 ERP SKU 固化实际保本 ROAS；手续费优先用 Shopify Payment 真实值，"
            "缺失时按 7% 估算。Docs-anchor: "
            "docs/superpowers/specs/2026-05-10-sku-actual-breakeven-roas-design.md"
        ),
        "schedule": "每天 01:00（北京时间）",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-sku-actual-roas.timer",
        "runner": "tools/sku_actual_roas_snapshot.py",
        "deployment": "待部署",
        "log_table": "scheduled_task_runs",
    },
    "dianxiaomi_listing_ranking_sync": {
        "code": "dianxiaomi_listing_ranking_sync",
        "name": "店小秘 Listing 近7天销量 Top500 归档",
        "description": (
            "每天 12:40 使用 DXM02-MK 店小秘登录态，滚动刷新最近 7 个快照日；"
            "每个 snapshot_date 代表截至当日的近 7 天窗口，按 paidProductCount 倒序只采集前 500 名 Listing，"
            "快照事实写入 dianxiaomi_rankings；商品主图、详情图和明空素材中文名按产品维度写入 dianxiaomi_product_assets。"
            "Docs-anchor: docs/superpowers/specs/2026-05-18-dianxiaomi-full-listing-archive-design.md；"
            "docs/superpowers/specs/2026-05-19-mingkong-product-assets-dedup-top500-design.md"
        ),
        "schedule": "每天 12:40（北京时间，刷新最近 7 天最新榜单）",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-dianxiaomi-listing-ranking-sync.timer",
        "runner": "tools/dianxiaomi_listing_ranking_sync.py",
        "deployment": "待部署",
        "log_table": "scheduled_task_runs",
    },
    "mingkong_material_daily_snapshot": {
        "code": "mingkong_material_daily_snapshot",
        "name": "明空素材每日快照",
        "description": (
            "每天 05:00、17:00 读取店小秘 Listing 最新可用快照 Top500 产品 code，"
            "按产品全量同步明空后台视频素材库，并归档累计 90 消耗、昨日消耗差额和昨日消耗前100。"
            "Docs-anchor: "
            "docs/superpowers/specs/2026-05-20-mingkong-product-local-aggregate-stats-design.md"
        ),
        "schedule": "每天 05:00、17:00（北京时间，每轮跑完前500产品后结束）",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-mingkong-material-daily-snapshot.timer",
        "runner": "tools/mingkong_material_daily_snapshot.py",
        "deployment": "待部署",
        "log_table": "scheduled_task_runs",
    },
    "mingkong_material_ad_status_refresh": {
        "code": "mingkong_material_ad_status_refresh",
        "name": "明空素材投放状态缓存",
        "description": (
            "每 10 分钟刷新明空卡片用的产品/视频素材投放状态缓存；"
            "卡片接口只读 mingkong_material_ad_status_cache，不实时扫素材库和广告事实表。"
            "Docs-anchor: "
            "docs/superpowers/specs/2026-05-20-mingkong-card-material-ad-status-design.md"
        ),
        "schedule": "每 10 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "mingkong_material_ad_status_refresh",
        "runner": "appcore.mingkong_materials.refresh_ad_status_cache",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "mingkong_fine_ai_auto_evaluation_tick": {
        "code": "mingkong_fine_ai_auto_evaluation_tick",
        "name": "明空视频卡片 AI 精细评估任务池",
        "description": (
            "后台 worker 池持续从明空视频素材库 90 天消耗 Top500 优先取任务；"
            "Top500 无可跑任务后再跑昨天消耗前100的全部 Top100。"
            "复用现有卡片精细 AI 评估结果表和弹窗。Docs-anchor: "
            "docs/superpowers/specs/2026-05-23-mingkong-fine-ai-auto-evaluation-design.md"
        ),
        "schedule": "连续后台任务池（默认 2 个卡片并发，单卡国家评估默认串行）",
        "source_type": "systemd",
        "source_label": "Linux systemd service",
        "source_ref": "autovideosrt-mingkong-fine-ai-worker.service",
        "runner": "tools/mingkong_fine_ai_auto_evaluation_worker.py --workers 2",
        "deployment": "线上 systemd 常驻服务",
        "log_table": "mingkong_fine_ai_auto_evaluations",
    },
    "meta_realtime_import": {
        "code": "meta_realtime_import",
        "name": "Meta 实时广告导入",
        "description": "ROI 实时同步中的 Meta 实时广告导入子任务，记录导入行数、消耗金额和跳过状态。",
        "schedule": "每 1 小时（随 ROI :02 触发）",
        "source_type": "subtask",
        "source_label": "ROI 同步子任务",
        "source_ref": "autovideosrt-roi-realtime-sync.timer",
        "runner": "tools/roi_hourly_sync.py::_sync_meta_realtime_daily",
        "deployment": "线上已启用",
        "log_table": "meta_ad_realtime_import_runs",
    },
    "meta_daily_final": {
        "code": "meta_daily_final",
        "name": "Meta 收盘日数据",
        "description": "每天北京时间 16:30 抓取刚收盘的 Meta 广告整日数据，17:00 做成功检测和补跑。",
        "schedule": "每天 16:30 同步；17:00 检查补跑",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-meta-daily-final-sync.timer / autovideosrt-meta-daily-final-check.timer",
        "runner": "tools/meta_daily_final_sync.py",
        "deployment": "线上已启用",
        "log_table": "scheduled_task_runs",
    },
    "cdp_environment_watchdog": {
        "code": "cdp_environment_watchdog",
        "name": "CDP 环境监控",
        "description": (
            "每分钟检查 DXM01-Meta、DXM02-MK、DXM03-RJC、TABCUT 的 systemd、CDP 和 noVNC 可用性；"
            "并兼盯 /data/autovideosrt/browser/runtime*/automation.lock 持有时长（spec: "
            "docs/superpowers/specs/2026-05-09-roi-hourly-sync-lock-recovery.md）。"
            "异常时重启对应环境并通过本任务失败日志触发 admin 报警。"
        ),
        "schedule": "每 1 分钟",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-cdp-environment-watchdog.timer",
        "runner": "tools/cdp_environment_watchdog.py",
        "deployment": "线上已启用",
        "log_table": "scheduled_task_runs",
    },
    "dianxiaomi_order_freshness_watchdog": {
        "code": "dianxiaomi_order_freshness_watchdog",
        "name": "店小秘订单新鲜度看护",
        "description": (
            "每分钟读 dianxiaomi_order_lines 的 MAX(updated_at)；停摆超过阈值（默认 120 分钟）"
            "时把本任务标 failed 触发飞书告警，cooldown 内不重复告警。"
            "Docs-anchor: docs/superpowers/specs/2026-05-09-dianxiaomi-order-freshness-watchdog.md"
        ),
        "schedule": "每 1 分钟",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-dianxiaomi-order-freshness-watchdog.timer",
        "runner": "tools/dianxiaomi_order_freshness_watchdog.py",
        "deployment": "待部署",
        "log_table": "scheduled_task_runs",
    },
    "product_cover_backfill_tick": {
        "code": "product_cover_backfill_tick",
        "name": "商品组图回填",
        "description": "轮询缺少商品主图的商品，访问商品详情页并用详情轮播第一张图回填主图。",
        "schedule": "每 10 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "product_cover_backfill_tick",
        "runner": "appcore.product_cover_backfill_scheduler.tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "",
    },
    "material_evaluation_tick": {
        "code": "material_evaluation_tick",
        "name": "AI 素材评估",
        "description": "扫描已满足条件但尚未评估的商品素材，批量触发 AI 评估。",
        "schedule": "每 5 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "material_evaluation_tick",
        "runner": "appcore.material_evaluation_scheduler.tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "",
    },
    "push_quality_check_tick": {
        "code": "push_quality_check_tick",
        "name": "推送内容质量检查",
        "description": "扫描推送管理里待推送和已推送的非英语素材，只检查尚未产生 auto 结果的任务；每个素材最多自动检查一次，失败或异常结果后续由人工干预。",
        "schedule": "每 10 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "push_quality_check_tick",
        "runner": "appcore.push_quality_check_scheduler.tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "push_status_cache_refresh": {
        "code": "push_status_cache_refresh",
        "name": "推送状态缓存刷新",
        "description": (
            "每 2 分钟刷新推送管理列表的 status/readiness 缓存表，"
            "列表接口优先读取 media_push_status_cache。Docs-anchor: "
            "docs/superpowers/specs/2026-05-22-pushes-status-cache-design.md"
        ),
        "schedule": "每 2 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "push_status_cache_refresh",
        "runner": "appcore.push_status_cache_scheduler.tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "apimart_balance_watchdog": {
        "code": "apimart_balance_watchdog",
        "name": "APIMART 余额看护",
        "description": (
            "每小时查询 APIMART API key 与账户余额，对照本地 usage_logs 中 APIMART 成功调用成本；"
            "余额查询失败、低余额或远端用量明显高于本地账单时标记 failed 并立即触发飞书告警。"
            "Docs-anchor: docs/superpowers/specs/2026-05-15-apimart-balance-watchdog-design.md"
        ),
        "schedule": "每小时",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "apimart_balance_watchdog",
        "runner": "appcore.apimart_balance_watchdog.run_scheduled_check",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
        "failure_alert_immediate": True,
    },
    "meta_hot_posts_sync_tick": {
        "code": "meta_hot_posts_sync_tick",
        "name": "Meta 热帖同步",
        "description": (
            "每天北京时间 07:00 使用已同步的 wedev Cookie/Bearer 拉取 /api/spy/hot/posts，"
            "按上游接口 total/空页停止条件采集全集，单请求最小间隔 3 秒，并把热帖卡片字段与商品链接写入本地表。"
            "Docs-anchor: docs/superpowers/specs/2026-05-15-meta-hot-posts-full-sync-design.md"
        ),
        "schedule": "每天 07:00（北京时间），按上游接口全集采集",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "meta_hot_posts_sync_tick",
        "runner": "appcore.meta_hot_posts.scheduler.sync_tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "meta_hot_posts_analysis_tick": {
        "code": "meta_hot_posts_analysis_tick",
        "name": "Meta 热帖商品分析",
        "description": (
            "每 10 分钟扫描 Meta 热帖未完成商品链接，每轮最多 30 个，条目之间间隔 20 秒，"
            "串行抓商品页标题、主图、SKU 价格，"
            "再调用 ADC 通道 Gemini 3.1 Flash-Lite 按商品标题判断 TikTok Shop US 一级类目；"
            "支持只重算类目且不重抓商品页；DB 单例守护，1 小时内已有运行则跳过，"
            "超过 1 小时则标记旧 run failed 后接管。Docs-anchor: "
            "docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md"
        ),
        "schedule": "每 10 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "meta_hot_posts_analysis_tick",
        "runner": "appcore.meta_hot_posts.scheduler.analysis_tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "meta_hot_posts_translate_messages_tick": {
        "code": "meta_hot_posts_translate_messages_tick",
        "name": "Meta 热帖文案翻译",
        "description": (
            "每 10 分钟扫描 Meta 热帖下方视频文案中尚未生成中文缓存的记录，"
            "并同步扫描已提取但尚未生成 product_title_zh 的商品页标题；每轮最多 30 条，任务之间不额外停顿；"
            "逐条调用可单独配置的 LLM 翻译为简体中文并写回 message_zh_html；"
            "商品标题固定走 OpenRouter Gemini 3.1 Flash-Lite 并写回 product_title_zh；"
            "页面优先展示中文缓存，原始英文仍保留在 message_html / product_title。默认文案使用 OpenRouter Gemini 3 Flash。"
            "Docs-anchor: docs/superpowers/specs/2026-05-18-meta-hot-posts-translate-model-and-schedule-design.md; "
            "docs/superpowers/specs/2026-05-19-meta-hot-posts-product-title-translation-design.md"
        ),
        "schedule": "每 10 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "meta_hot_posts_translate_messages_tick",
        "runner": "appcore.meta_hot_posts.scheduler.translation_tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "meta_hot_posts_video_localization_tick": {
        "code": "meta_hot_posts_video_localization_tick",
        "name": "Meta 热帖视频本地化",
        "description": (
            "每 10 分钟串行下载 Meta 热帖中尚未本地化的视频，默认每轮最多 30 条；"
            "每条下载完成或失败后至少间隔 30 秒再处理下一条，下载、时长、首帧封面结果写回 local_video_* 字段。"
            "失败视频至少 12 小时后才重试，最多尝试 5 次，仍失败则标记 unavailable；"
            "页面优先使用本地 MP4，缺失时回退 Facebook iframe。Docs-anchor: "
            "docs/superpowers/specs/2026-05-14-meta-hot-posts-video-localization-design.md"
        ),
        "schedule": "每 10 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "meta_hot_posts_video_localization_tick",
        "runner": "appcore.meta_hot_posts.scheduler.video_localization_tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "meta_hot_posts_tos_video_sync_tick": {
        "code": "meta_hot_posts_tos_video_sync_tick",
        "name": "Meta 热帖视频 TOS 同步",
        "description": (
            "每 10 分钟扫描已本地化的 Meta 热帖投放视频和封面，按 OUTPUT_DIR 解析 local_video_path/local_video_cover_path，"
            "复用 TOS/NAS 备份 reconcile 逻辑把缺失对象上传到 TOS；"
            "也可通过 tools/meta_hot_posts_tos_sync.py 手工回填。Docs-anchor: "
            "docs/superpowers/specs/2026-05-16-meta-hot-posts-tos-video-sync-design.md"
        ),
        "schedule": "每 10 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "meta_hot_posts_tos_video_sync_tick",
        "runner": "appcore.meta_hot_posts.tos_sync.run_scheduled_tos_video_sync",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "meta_hot_posts_video_analysis_queue_tick": {
        "code": "meta_hot_posts_video_analysis_queue_tick",
        "name": "Meta hot posts unified video analysis queue",
        "description": (
            "Every 10 minutes, run one unified queue for Meta hot post video analysis. "
            "Each round processes items one-at-a-time within a 560-second window with a 40-second hard per-item timeout; "
            "the first timeout stops the round after counting one failed attempt; "
            "the first rate-limit response stops the current round early without changing that row's saved status and pauses automatic retries until a manual run clears it; "
            "task_type=us_copyability runs before task_type=europe_fit, and Europe starts only after "
            "US copyability has no remaining capacity in the round. Both modes use Vertex ADC "
            "gemini-3-flash-preview. A new round takes over any previous running queue run and resets "
            "running US/Europe rows. Docs-anchor: "
            "docs/superpowers/specs/2026-05-15-meta-hot-posts-unified-video-analysis-queue-design.md"
        ),
        "schedule": "Every 10 minutes",
        "source_type": "apscheduler",
        "source_label": "Web process APScheduler",
        "source_ref": "meta_hot_posts_video_analysis_queue_tick",
        "runner": "appcore.meta_hot_posts.scheduler.video_analysis_queue_tick_once",
        "deployment": "Registered on Web service startup",
        "log_table": "scheduled_task_runs",
    },
    "tos_backup": {
        "code": "tos_backup",
        "name": "TOS 文件与数据库备份",
        "description": "每天凌晨同步受保护文件到 autovideosrtlocal 桶，并保留 7 天 MySQL dump。",
        "schedule": "每天 01:00",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "tos_backup",
        "runner": "appcore.tos_backup_job.run_scheduled_backup",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "weekly_roas_report": {
        "code": "weekly_roas_report",
        "name": "ROAS 周报快照",
        "description": "每周二 09:00 把上一个完整 ISO 周（周一到周日）的真实/Meta ROAS 对比固化成快照，存入 weekly_roas_report_snapshots。",
        "schedule": "每周二 09:00",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "weekly_roas_report",
        "runner": "appcore.weekly_roas_report.run_scheduled_snapshot",
        "deployment": "Web 服务启动时注册",
        "log_table": "scheduled_task_runs",
    },
    "tabcut_daily_selection": {
        "code": "tabcut_daily_selection",
        "name": "Tabcut US 选品日快照",
        "description": (
            "每天北京时间 08:00 使用服务器 Tabcut 可视浏览器环境 "
            "autovideosrt-tabcut-vnc.service（CDP 127.0.0.1:9227，noVNC 6097）"
            "采集美国站数据：视频榜日/周/月播放与销量榜各 1000 条，"
            "以及已框选的 9 个商品榜类目每天前 50 名。运行结果写入 "
            "scheduled_task_runs，采集产物写入 /data/autovideosrt/tabcut/daily。Docs-anchor: "
            "docs/superpowers/specs/2026-05-12-tabcut-daily-task-management.md"
        ),
        "schedule": "每天 08:00（北京时间），采集 US 最近 30 天数据",
        "source_type": "systemd",
        "source_label": "Linux systemd timer",
        "source_ref": "autovideosrt-tabcut-daily-selection.timer",
        "runner": "python -m tools.tabcut_crawler.main --mode recent7 --days 30",
        "deployment": "生产服务器 cjh 用户 autovideosrt-tabcut-vnc.service 专用 Chrome profile",
        "log_table": "scheduled_task_runs",
        "output_file": "/data/autovideosrt/tabcut/daily/",
    },
    "active_task_pre_restart_check": {
        "code": "active_task_pre_restart_check",
        "name": "Active task pre-restart check",
        "description": "Manual operations guard that snapshots active background tasks and blocks restart when non-interruptible tasks are running.",
        "schedule": "Manual before service restart or release",
        "source_type": "manual_ops",
        "source_label": "Release / ops preflight",
        "source_ref": "python -m appcore.ops.active_tasks pre-restart",
        "runner": "python -m appcore.ops.active_tasks pre-restart",
        "deployment": "Run in test or production server project directory before restarting the web service",
        "log_table": "",
        "control_strategy": "readonly",
        "log_source": "db:runtime_active_task_snapshots",
        "log_available": True,
    },
    "subtitle_removal_vod_tick": {
        "code": "subtitle_removal_vod_tick",
        "name": "字幕移除 VOD 接力",
        "description": "当字幕移除 provider 为 VOD 时，持续轮询擦除任务状态并回填结果播放地址。",
        "schedule": "每 60 秒",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "subtitle_removal_vod_tick",
        "runner": "appcore.subtitle_removal_vod_scheduler.tick_once",
        "deployment": "Web 服务启动时注册",
        "log_table": "",
    },
    "task_center_raw_niuma_watch": {
        "code": "task_center_raw_niuma_watch",
        "name": "任务中心原视频牛马处理对账",
        "description": "对账 raw_in_progress 父任务与已持久化的牛马字幕移除任务，补偿进程内监听线程丢失导致的完成/失败/超时回填缺口。",
        "schedule": "每 1 分钟",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "task_center_raw_niuma_watch",
        "runner": "appcore.task_center_raw_niuma_scheduler.tick_once",
        "deployment": "Web 服务启动时注册；原始提交时仍会启动短期 watcher，定时对账负责兜底恢复",
        "log_table": "task_events",
    },
    "cleanup": {
        "code": "cleanup",
        "name": "临时文件清理",
        "description": "定期清理系统运行过程中产生的过期临时文件和中间产物。",
        "schedule": "每小时",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "cleanup",
        "runner": "appcore.cleanup.run_cleanup",
        "deployment": "Web 服务启动时注册",
        "log_table": "",
    },
    "medias_detail_fetch_cleanup": {
        "code": "medias_detail_fetch_cleanup",
        "name": "素材详情抓取任务清理",
        "description": "进程内维护任务，每 60 秒清理过期的素材详情抓取任务状态。",
        "schedule": "每 60 秒",
        "source_type": "in_process",
        "source_label": "进程内维护任务",
        "source_ref": "mdf-cleanup",
        "runner": "appcore.medias_detail_fetch_tasks._cleanup_loop",
        "deployment": "模块导入后后台线程启动",
        "log_table": "",
    },
    "voice_match_cleanup": {
        "code": "voice_match_cleanup",
        "name": "音色匹配任务清理",
        "description": "进程内维护任务，每 60 秒清理过期的音色匹配任务状态和临时文件。",
        "schedule": "每 60 秒",
        "source_type": "in_process",
        "source_label": "进程内维护任务",
        "source_ref": "vmt-cleanup",
        "runner": "appcore.voice_match_tasks._cleanup_loop",
        "deployment": "模块导入后后台线程启动",
        "log_table": "",
    },
    "tts_convergence_stats": {
        "code": "tts_convergence_stats",
        "name": "TTS 收敛统计",
        "description": "服务器 root crontab 每小时生成 TTS 收敛统计日志，用于排查配音收敛情况。",
        "schedule": "每小时整点",
        "source_type": "cron",
        "source_label": "Linux root crontab",
        "source_ref": "0 * * * *",
        "runner": "tools/tts_convergence_stats.py",
        "deployment": "线上 crontab 已启用",
        "log_table": "",
        "output_file": "/var/log/tts_convergence.log",
    },
    "meta_realtime_local_sync": {
        "code": "meta_realtime_local_sync",
        "name": "Meta 本地 ADS Power 实时导出",
        "description": "Windows 计划任务或本地守护进程每 20 分钟从 ADS Power 90 导出 Meta 实时广告数据，并上传到服务器导入。",
        "schedule": "每 20 分钟（00/20/40）",
        "source_type": "windows",
        "source_label": "Windows 计划任务 / 本地 daemon",
        "source_ref": "AutoVideoSrt Meta Realtime Local Sync",
        "runner": "tools/meta_realtime_local_sync.py / tools/meta_realtime_local_daemon.py",
        "deployment": "本地运维任务",
        "log_table": "",
        "output_file": "scratch/meta_realtime_local/logs/",
        "default_enabled": False,
    },
    "analytics_data_quality_inspection": {
        "code": "analytics_data_quality_inspection",
        "name": "数据分析数据质量巡检",
        "description": (
            "近 7 个 Meta 业务日扫描：广告费源表 vs 已分摊+未分摊对账、订单利润派生表新鲜度。"
            "结果写入 scheduled_task_runs.summary_json，供 /order-profit 等页面 data_quality "
            "复用。Docs-anchor: docs/analytics-data-quality-guardrails.md"
        ),
        "schedule": "每小时整点（与 ROI :00/:20/:40 错峰）",
        "source_type": "systemd",
        "source_label": "Linux systemd timer（待启用）",
        "source_ref": "autovideosrt-analytics-data-quality.timer",
        "runner": "appcore.order_analytics.data_quality.run_recent_inspection",
        "deployment": "待部署",
        "log_table": "scheduled_task_runs",
    },
    "tos_file_inventory_scan": {
        "code": "tos_file_inventory_scan",
        "name": "TOS文件管理资产扫描",
        "description": "扫描受保护业务文件并更新 TOS 文件映射表；每周日凌晨5点自动执行。",
        "schedule": "每周日 05:00",
        "source_type": "apscheduler",
        "source_label": "Web 进程 APScheduler",
        "source_ref": "tos_file_inventory_scan",
        "runner": "appcore.tos_file_management.run_scheduled_inventory_scan",
        "deployment": "Web 服务启动时注册",
        "log_table": "tos_file_scan_runs",
        "default_enabled": True,
    },
}

_RUNS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_task_runs (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  task_code VARCHAR(64) NOT NULL,
  task_name VARCHAR(120) NOT NULL,
  status ENUM('running', 'success', 'failed') NOT NULL DEFAULT 'running',
  scheduled_for DATETIME NULL,
  started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  finished_at DATETIME NULL,
  duration_seconds INT UNSIGNED NULL,
  summary_json JSON NULL,
  error_message MEDIUMTEXT NULL,
  output_file VARCHAR(512) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_scheduled_task_runs_task_started (task_code, started_at),
  KEY idx_scheduled_task_runs_status_started (status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_CONTROL_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS scheduled_task_controls (
  task_code VARCHAR(64) NOT NULL PRIMARY KEY,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  last_action_status VARCHAR(32) NULL,
  last_action_message MEDIUMTEXT NULL,
  updated_by VARCHAR(120) NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

CONTROL_LABELS = {
    "enabled": "启用中",
    "disabled": "已停用",
    "deprecated": "已废弃",
    "readonly": "只读登记",
    "unknown": "未知",
}

CONTROLLABLE_STRATEGIES = {"apscheduler", "systemd", "windows", "guard"}
CONFIRMATION_REQUIRED_STRATEGIES = {"systemd", "windows"}


def _log_source(task: TaskDefinition) -> str:
    log_table = str(task.get("log_table") or "").strip()
    if log_table:
        return f"db:{log_table}"
    output_file = str(task.get("output_file") or "").strip()
    if output_file:
        return f"file:{output_file}"
    source_type = str(task.get("source_type") or "").strip().lower()
    if source_type in {"apscheduler", "in_process"}:
        return "service:autovideosrt"
    if source_type == "systemd":
        return f"journal:{task.get('source_ref') or task.get('code') or 'unknown'}"
    if source_type == "windows":
        return "windows:event-log"
    if source_type == "cron":
        return "cron:external"
    return "unknown"


def _with_definition_metadata(task: TaskDefinition) -> TaskDefinition:
    item = dict(task)
    item.setdefault("control_strategy", _control_strategy(item))
    item.setdefault("log_source", _log_source(item))
    item.setdefault("log_available", bool(item["log_source"] and item["log_source"] != "unknown"))
    item.setdefault(
        "log_link_available",
        bool(item.get("log_table")) or item["log_source"] == "db:runtime_active_task_snapshots",
    )
    return item


def task_definitions() -> list[TaskDefinition]:
    return [_with_definition_metadata(item) for item in TASK_DEFINITIONS.values()]


def log_filter_definitions() -> list[TaskDefinition]:
    return [
        {
            "code": "all",
            "name": "全部日志",
            "description": "汇总所有已接入运行表的定时任务日志。",
            "schedule": "全部",
        },
        *task_definitions(),
    ]


def management_tasks() -> list[TaskDefinition]:
    controls = _control_rows_by_code()
    return [_with_control_state(item, controls.get(item["code"])) for item in task_definitions()]


def get_task_definition(task_code: str) -> TaskDefinition:
    code = (task_code or "").strip()
    if code in TASK_DEFINITIONS:
        return dict(TASK_DEFINITIONS[code])
    return {
        "code": code or "unknown",
        "name": code or "未知任务",
        "description": "未登记的定时任务。",
        "schedule": "-",
        "source_type": "unknown",
        "source_label": "未登记",
        "source_ref": "-",
        "runner": "-",
        "deployment": "未登记",
        "log_table": "",
    }


def is_known_task(task_code: str) -> bool:
    return (task_code or "").strip() in TASK_DEFINITIONS


def ensure_runs_table() -> None:
    execute(_RUNS_TABLE_SQL)


def ensure_control_table() -> None:
    execute(_CONTROL_TABLE_SQL)


def _is_truthy(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return str(value).strip().lower() not in {"", "0", "false", "no", "off", "disabled"}


def _control_strategy(task: TaskDefinition) -> str:
    explicit = str(task.get("control_strategy") or "").strip()
    if explicit:
        return explicit
    source_type = str(task.get("source_type") or "").strip().lower()
    if source_type in {"apscheduler", "systemd"}:
        return source_type
    if source_type == "windows":
        return "windows_local"
    if source_type in {"subtask", "in_process"}:
        return "guard"
    return "readonly"


def _is_deprecated(task: TaskDefinition) -> bool:
    return str(task.get("lifecycle") or "active").strip().lower() == "deprecated"


def _is_control_supported(task: TaskDefinition) -> bool:
    return (not _is_deprecated(task)) and _control_strategy(task) in CONTROLLABLE_STRATEGIES


def _requires_control_confirmation(task: TaskDefinition) -> bool:
    return _control_strategy(task) in CONFIRMATION_REQUIRED_STRATEGIES


def _control_unavailable_reason(task: TaskDefinition) -> str:
    if _is_deprecated(task):
        return "该任务已标记为废弃，不再提供启停入口。"
    strategy = _control_strategy(task)
    if strategy in CONTROLLABLE_STRATEGIES:
        return ""
    source_type = str(task.get("source_type") or "").strip().lower()
    if strategy == "windows_local" or source_type == "windows":
        return (
            "该任务运行在开发机 Windows 或本地 daemon 上，线上 Web 不能跨机器执行 "
            "schtasks 或控制 Windows 服务；请在对应 Windows 机器上用任务计划程序、"
            "服务管理器或管理员 PowerShell 手动启停。"
        )
    if source_type == "cron":
        return "该任务由 crontab 外部调度，Web 后台只做登记；需要停用请登录对应服务器调整 crontab。"
    return "该任务的触发器不在当前 Web 进程控制范围内，后台只做登记；需要在对应运行环境里手动启停。"


def _default_enabled(task: TaskDefinition) -> bool:
    if _is_deprecated(task):
        return False
    return _is_truthy(task.get("default_enabled"), default=True)


def _control_rows_by_code() -> dict[str, dict[str, Any]]:
    try:
        ensure_control_table()
        rows = query(
            "SELECT task_code, enabled, last_action_status, last_action_message, "
            "updated_by, updated_at FROM scheduled_task_controls"
        )
    except Exception:
        log.warning("failed to load scheduled task controls", exc_info=True)
        return {}
    return {str(row.get("task_code") or ""): row for row in rows if row.get("task_code")}


def _control_row(task_code: str) -> dict[str, Any] | None:
    try:
        ensure_control_table()
        rows = query(
            "SELECT task_code, enabled, last_action_status, last_action_message, "
            "updated_by, updated_at FROM scheduled_task_controls WHERE task_code=%s",
            (task_code,),
        )
    except Exception:
        log.warning("failed to load scheduled task control task_code=%s", task_code, exc_info=True)
        return None
    return rows[0] if rows else None


def _with_control_state(task: TaskDefinition, control: dict[str, Any] | None = None) -> TaskDefinition:
    item = dict(task)
    strategy = _control_strategy(item)
    supported = _is_control_supported(item)
    unavailable_reason = "" if supported else _control_unavailable_reason(item)
    enabled = _is_truthy((control or {}).get("enabled"), default=_default_enabled(item))
    if _is_deprecated(item):
        state = "deprecated"
        enabled = False
    elif enabled:
        state = "enabled"
    else:
        state = "disabled"
    item.update({
        "control_strategy": strategy,
        "control_supported": supported,
        "control_requires_confirmation": _requires_control_confirmation(item),
        "control_confirmation_value": item.get("code") or "",
        "control_enabled": enabled,
        "control_state": state,
        "control_label": CONTROL_LABELS.get(state, CONTROL_LABELS["unknown"]),
        "control_class": state,
        "control_action": "disable" if enabled else "enable",
        "control_action_label": "停用" if enabled else "启用",
        "control_unavailable_reason": unavailable_reason,
        "last_action_status": (control or {}).get("last_action_status") or "",
        "last_action_message": (control or {}).get("last_action_message") or "",
        "updated_by": (control or {}).get("updated_by") or "",
        "control_updated_at": (control or {}).get("updated_at"),
    })
    return item


def is_task_enabled(task_code: str) -> bool:
    task = TASK_DEFINITIONS.get((task_code or "").strip())
    if not task:
        return False
    row = _control_row(task["code"])
    return bool(_with_control_state(task, row).get("control_enabled"))


def _global_scheduled_tasks_enabled() -> bool:
    try:
        import config
    except Exception:
        return True
    return bool(getattr(config, "SCHEDULED_TASKS_ENABLED", True))


def _record_control_state(
    task_code: str,
    *,
    enabled: bool,
    action_status: str,
    message: str,
    actor: str | None = None,
) -> None:
    ensure_control_table()
    execute(
        "INSERT INTO scheduled_task_controls "
        "(task_code, enabled, last_action_status, last_action_message, updated_by) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE enabled=VALUES(enabled), "
        "last_action_status=VALUES(last_action_status), "
        "last_action_message=VALUES(last_action_message), "
        "updated_by=VALUES(updated_by)",
        (task_code, 1 if enabled else 0, action_status, message, actor),
    )


def _run_control_command(command: list[str]) -> dict[str, Any]:
    if not command or not shutil.which(command[0]):
        return {
            "ok": False,
            "message": f"控制命令不可用：{command[0] if command else '-'}",
            "command": " ".join(command),
        }
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (completed.stdout or completed.stderr or "").strip()
    return {
        "ok": completed.returncode == 0,
        "message": output or f"exit_code={completed.returncode}",
        "command": " ".join(command),
    }


def _systemd_units(task: TaskDefinition) -> list[str]:
    raw = str(task.get("source_ref") or "")
    parts = raw.replace(",", "/").split("/")
    units = [part.strip() for part in parts if part.strip().endswith((".timer", ".service"))]
    return units or ([raw.strip()] if raw.strip() else [])


def _apply_apscheduler_job_state(task_code: str, enabled: bool) -> dict[str, Any]:
    try:
        from appcore import scheduler as scheduler_module
        scheduler = scheduler_module.current_scheduler()
    except Exception:
        scheduler = None
    if scheduler is None:
        return {"ok": True, "message": "控制开关已记录，Web 调度器启动后会应用。"}
    try:
        job = scheduler.get_job(task_code)
        if not job:
            return {"ok": True, "message": "控制开关已记录，当前进程未找到该 APScheduler job。"}
        if enabled:
            scheduler.resume_job(task_code)
        else:
            scheduler.pause_job(task_code)
    except Exception as exc:
        return {"ok": False, "message": f"APScheduler 控制失败：{exc}"}
    return {"ok": True, "message": "APScheduler job 状态已更新。"}


def _apply_control_strategy(task: TaskDefinition, enabled: bool) -> dict[str, Any]:
    strategy = _control_strategy(task)
    if strategy == "guard":
        return {"ok": True, "message": "控制开关已写入；对应任务入口会在下一轮读取该状态。"}
    if strategy == "apscheduler":
        return _apply_apscheduler_job_state(task["code"], enabled)
    if strategy == "systemd":
        units = _systemd_units(task)
        if not units:
            return {"ok": False, "message": "未登记 systemd unit。"}
        action = "enable" if enabled else "disable"
        return _run_control_command(["systemctl", action, "--now", *units])
    if strategy == "windows":
        task_name = str(task.get("source_ref") or "").strip()
        if not task_name:
            return {"ok": False, "message": "未登记 Windows 计划任务名称。"}
        action = "/ENABLE" if enabled else "/DISABLE"
        return _run_control_command(["schtasks", "/Change", "/TN", task_name, action])
    return {"ok": False, "message": "该任务来源暂不支持从 Web 后台直接启停。"}


def set_task_enabled(
    task_code: str,
    enabled: bool,
    *,
    actor: str | None = None,
    confirmation: str | None = None,
) -> TaskDefinition:
    code = (task_code or "").strip()
    task = TASK_DEFINITIONS.get(code)
    if not task:
        raise ValueError("未知定时任务")
    if not _is_control_supported(task):
        reason = _control_unavailable_reason(task)
        suffix = f"：{reason}" if reason else ""
        raise ValueError(f"{task['name']} 不支持从 Web 后台直接启停{suffix}")
    if _requires_control_confirmation(task) and (confirmation or "").strip() != code:
        raise ValueError(f"{task['name']} 需要输入任务代码确认后才能启停")
    result = _apply_control_strategy(task, bool(enabled))
    if not result.get("ok"):
        current_enabled = is_task_enabled(code)
        _record_control_state(
            code,
            enabled=current_enabled,
            action_status="failed",
            message=str(result.get("message") or "控制失败"),
            actor=actor,
        )
        raise RuntimeError(str(result.get("message") or "控制失败"))
    _record_control_state(
        code,
        enabled=bool(enabled),
        action_status="success",
        message=str(result.get("message") or "控制成功"),
        actor=actor,
    )
    return _with_control_state(
        task,
        {
            "task_code": code,
            "enabled": 1 if enabled else 0,
            "last_action_status": "success",
            "last_action_message": str(result.get("message") or "控制成功"),
            "updated_by": actor,
            "updated_at": datetime.now(),
        },
    )


def sync_scheduler_job_state(scheduler: Any, task_code: str) -> None:
    task = TASK_DEFINITIONS.get(task_code)
    if not task or _control_strategy(task) != "apscheduler":
        return
    if not all(hasattr(scheduler, name) for name in ("get_job", "pause_job", "resume_job")):
        return
    try:
        job = scheduler.get_job(task_code)
        if not job:
            return
        if is_task_enabled(task_code):
            if getattr(job, "next_run_time", None) is not None:
                return
            scheduler.resume_job(task_code)
        else:
            scheduler.pause_job(task_code)
    except Exception:
        log.warning("failed to sync apscheduler job state task_code=%s", task_code, exc_info=True)


def apply_scheduler_controls(scheduler: Any) -> None:
    for task in TASK_DEFINITIONS.values():
        if _control_strategy(task) == "apscheduler":
            sync_scheduler_job_state(scheduler, task["code"])


def run_if_enabled(task_code: str, func, *args, **kwargs):
    if not _global_scheduled_tasks_enabled():
        log.info("scheduled task skipped because global scheduling is disabled: %s", task_code)
        return {
            "skipped": True,
            "reason": "scheduled tasks globally disabled",
            "task_code": task_code,
        }
    if not is_task_enabled(task_code):
        log.info("scheduled task skipped because it is disabled: %s", task_code)
        return {
            "skipped": True,
            "reason": "scheduled task disabled",
            "task_code": task_code,
        }
    return func(*args, **kwargs)


def add_controlled_job(scheduler: Any, task_code: str, func, trigger: str, **kwargs):
    @wraps(func)
    def _controlled_job():
        return run_if_enabled(task_code, func)

    kwargs.setdefault("id", task_code)
    job = scheduler.add_job(_controlled_job, trigger, **kwargs)
    sync_scheduler_job_state(scheduler, task_code)
    return job


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value)


def start_run(task_code: str, *, scheduled_for: datetime | None = None) -> int:
    ensure_runs_table()
    task = get_task_definition(task_code)
    return int(execute(
        "INSERT INTO scheduled_task_runs "
        "(task_code, task_name, status, scheduled_for, started_at) "
        "VALUES (%s, %s, 'running', %s, NOW())",
        (task["code"], task["name"], scheduled_for),
    ))


def latest_running_run(task_code: str) -> dict[str, Any] | None:
    ensure_runs_table()
    rows = query(
        """
        SELECT id, task_code, task_name, status, scheduled_for, started_at,
               finished_at, duration_seconds, summary_json, error_message,
               output_file, created_at, updated_at
        FROM scheduled_task_runs
        WHERE task_code = %s AND status = 'running'
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """,
        (task_code,),
    )
    return _normalize_row(rows[0]) if rows else None


def finish_run(
    run_id: int,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    error_message: str | None = None,
    output_file: str | None = None,
) -> None:
    summary_json = (
        json.dumps(summary, ensure_ascii=False, default=_json_default)
        if summary is not None
        else None
    )
    execute(
        "UPDATE scheduled_task_runs SET status=%s, finished_at=NOW(), "
        "duration_seconds=TIMESTAMPDIFF(SECOND, started_at, NOW()), "
        "summary_json=%s, error_message=%s, output_file=%s "
        "WHERE id=%s",
        (status, summary_json, error_message, output_file, int(run_id)),
    )
    if status == "failed":
        _dispatch_failure_alert(int(run_id))
    elif status == "success":
        _dispatch_recovery_alert(int(run_id))


def _scheduled_task_run_by_id(run_id: int) -> dict[str, Any] | None:
    rows = query(
        """
        SELECT id, task_code, task_name, status, scheduled_for, started_at,
               finished_at, duration_seconds, summary_json, error_message,
               output_file, created_at, updated_at
        FROM scheduled_task_runs
        WHERE id = %s
        """,
        (int(run_id),),
    )
    return _normalize_row(rows[0]) if rows else None


def _dispatch_failure_alert(run_id: int) -> None:
    try:
        row = _scheduled_task_run_by_id(run_id)
        if not row:
            return
        task_code = str(row.get("task_code") or "")
        sample_alert = _is_sample_failure_alert_worthy(row)
        from appcore import feishu_alerts

        if _is_immediate_failure_alert_task(task_code):
            should_send = True
            streak = feishu_alerts.consecutive_failure_count(
                task_code, current_run_id=run_id
            )
        else:
            should_send, streak = feishu_alerts.should_dispatch_failure(
                task_code, current_run_id=run_id, immediate=sample_alert
            )
        if not should_send:
            log.info(
                "feishu failure alert suppressed (streak=%s sample_alert=%s) task_code=%s run_id=%s",
                streak,
                sample_alert,
                task_code,
                run_id,
            )
            return
        if streak >= 2:
            row["consecutive_failures"] = streak
        feishu_alerts.send_scheduled_task_failure(row)
    except Exception:
        log.warning("failed to dispatch scheduled task failure alert", exc_info=True)


def _dispatch_recovery_alert(run_id: int) -> None:
    try:
        row = _scheduled_task_run_by_id(run_id)
        if not row:
            return
        task_code = str(row.get("task_code") or "")
        from appcore import feishu_alerts

        prior = feishu_alerts.prior_consecutive_failures_before_run(
            task_code, current_run_id=run_id
        )
        if not _should_dispatch_recovery_alert_for_run(row, prior_failures=prior):
            log.info(
                "scheduled task recovery alert suppressed by task rule task_code=%s run_id=%s",
                task_code,
                run_id,
            )
            return
        feishu_alerts.send_scheduled_task_recovery(row, prior_failures=prior)
    except Exception:
        log.warning("failed to dispatch scheduled task recovery alert", exc_info=True)


def record_failure(
    task_code: str,
    *,
    error_message: str,
    summary: dict[str, Any] | None = None,
    output_file: str | None = None,
) -> int:
    run_id = start_run(task_code)
    finish_run(
        run_id,
        status="failed",
        summary=summary,
        error_message=error_message,
        output_file=output_file,
    )
    return run_id


def _decode_summary(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


_SUMMARY_FAILURE_KEYS = (
    "failed",
    "failures",
    "failed_count",
    "failure_count",
    "error_count",
    "errors",
)
_SUMMARY_ATTEMPT_KEYS = (
    "attempts",
    "total",
    "scanned",
    "processed",
    "checked",
    "items",
    "count",
    "rows",
    "videos",
    "tasks",
)
_SUMMARY_SUCCESS_KEYS = (
    "success",
    "successes",
    "succeeded",
    "success_count",
    "downloaded",
    "completed",
    "passed",
    "ok",
    "updated",
    "created",
)
_FAILED_STATUS_VALUES = {
    "failed",
    "failure",
    "error",
    "timeout",
    "unavailable",
    "auth_failed",
    "request_failed",
    "cancelled",
    "canceled",
}


def _first_summary_int(summary: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        if key in summary:
            return _non_negative_int(summary.get(key))
    return None


def _status_list_failure_metrics(items: Any) -> tuple[int, int]:
    if not isinstance(items, list):
        return 0, 0
    attempts = 0
    failed = 0
    for item in items:
        status = ""
        if isinstance(item, dict):
            status = str(item.get("status") or item.get("result") or "").strip().lower()
        else:
            status = str(item or "").strip().lower()
        if not status:
            continue
        attempts += 1
        if status in _FAILED_STATUS_VALUES or status.endswith("_failed"):
            failed += 1
    return attempts, failed


def _summary_failure_sample_metrics(summary: dict[str, Any]) -> dict[str, int | float]:
    if not isinstance(summary, dict) or not summary:
        return {"attempts": 0, "failed": 0, "failure_rate": 0.0}

    list_attempts = 0
    list_failed = 0
    for key in ("results", "items", "account_results", "task_results"):
        attempts, failed = _status_list_failure_metrics(summary.get(key))
        list_attempts += attempts
        list_failed += failed

    failed_value = _first_summary_int(summary, _SUMMARY_FAILURE_KEYS)
    failed = max(list_failed, failed_value or 0)
    if failed <= 0:
        return {"attempts": list_attempts, "failed": 0, "failure_rate": 0.0}

    attempt_candidates: list[int] = [list_attempts, failed]
    attempts_value = _first_summary_int(summary, _SUMMARY_ATTEMPT_KEYS)
    if attempts_value is not None:
        attempt_candidates.append(attempts_value)
    success_value = _first_summary_int(summary, _SUMMARY_SUCCESS_KEYS)
    if success_value is not None:
        attempt_candidates.append(success_value + failed)

    attempts = max(attempt_candidates)
    failure_rate = (failed / attempts) if attempts else 0.0
    return {"attempts": attempts, "failed": failed, "failure_rate": failure_rate}


def _download_attempts_from_summary(summary: dict[str, Any]) -> tuple[int, int]:
    downloaded = _non_negative_int(summary.get("downloaded"))
    failed = _non_negative_int(summary.get("failed"))
    scanned = _non_negative_int(summary.get("scanned"))
    return max(scanned, downloaded + failed), failed


def _video_localization_daily_download_metrics(
    row: dict[str, Any],
) -> dict[str, int | float]:
    current_run_id = _non_negative_int(row.get("id")) or None
    if current_run_id:
        rows = query(
            """
            SELECT id, summary_json
            FROM scheduled_task_runs
            WHERE task_code = %s
              AND status IN ('success','failed')
              AND started_at >= CURDATE()
              AND started_at < CURDATE() + INTERVAL 1 DAY
              AND id <= %s
            ORDER BY started_at ASC, id ASC
            """,
            (META_HOT_POSTS_VIDEO_LOCALIZATION_TASK_CODE, current_run_id),
        )
    else:
        rows = query(
            """
            SELECT id, summary_json
            FROM scheduled_task_runs
            WHERE task_code = %s
              AND status IN ('success','failed')
              AND started_at >= CURDATE()
              AND started_at < CURDATE() + INTERVAL 1 DAY
            ORDER BY started_at ASC, id ASC
            """,
            (META_HOT_POSTS_VIDEO_LOCALIZATION_TASK_CODE,),
        )

    attempts = 0
    failed = 0
    seen_current = False
    for item in rows or []:
        item_id = _non_negative_int(item.get("id"))
        if current_run_id and item_id == current_run_id:
            seen_current = True
        item_attempts, item_failed = _download_attempts_from_summary(
            _decode_summary(item.get("summary_json") or item.get("summary"))
        )
        attempts += item_attempts
        failed += item_failed

    if current_run_id and not seen_current:
        item_attempts, item_failed = _download_attempts_from_summary(row.get("summary") or {})
        if not item_attempts:
            item_attempts, item_failed = _download_attempts_from_summary(
                _decode_summary(row.get("summary_json"))
            )
        attempts += item_attempts
        failed += item_failed

    failure_rate = (failed / attempts) if attempts else 0.0
    return {"attempts": attempts, "failed": failed, "failure_rate": failure_rate}


def _failure_sample_metrics_for_run(row: dict[str, Any]) -> dict[str, int | float]:
    task_code = str(row.get("task_code") or "")
    if task_code == META_HOT_POSTS_VIDEO_LOCALIZATION_TASK_CODE:
        return _video_localization_daily_download_metrics(row)
    summary = row.get("summary")
    if not isinstance(summary, dict):
        summary = _decode_summary(row.get("summary_json"))
    return _summary_failure_sample_metrics(summary)


def _is_sample_failure_alert_worthy(row: dict[str, Any]) -> bool:
    metrics = _failure_sample_metrics_for_run(row)
    return (
        int(metrics["attempts"]) > FAILURE_ALERT_MIN_SAMPLE_ATTEMPTS
        and float(metrics["failure_rate"]) > FAILURE_ALERT_FAILURE_RATE_THRESHOLD
    )


def _is_immediate_failure_alert_task(task_code: str) -> bool:
    task = TASK_DEFINITIONS.get((task_code or "").strip())
    return bool(task and task.get("failure_alert_immediate"))


def _should_dispatch_failure_alert_for_run(row: dict[str, Any]) -> bool:
    task_code = str(row.get("task_code") or "")
    if not task_code or str(row.get("status") or "") != "failed":
        return False
    if _is_immediate_failure_alert_task(task_code):
        return True
    if _is_sample_failure_alert_worthy(row):
        return True
    from appcore import feishu_alerts

    streak = feishu_alerts.consecutive_failure_count(
        task_code, current_run_id=_non_negative_int(row.get("id")) or None
    )
    return streak >= FAILURE_ALERT_MIN_CONSECUTIVE_RUNS


def _prior_failure_streak_has_sample_alert(row: dict[str, Any]) -> bool:
    task_code = str(row.get("task_code") or "")
    current_run_id = _non_negative_int(row.get("id"))
    if not task_code or not current_run_id:
        return False
    rows = query(
        """
        SELECT id, task_code, status, summary_json
        FROM scheduled_task_runs
        WHERE task_code = %s
          AND status IN ('success','failed')
          AND id < %s
        ORDER BY id DESC
        LIMIT 100
        """,
        (task_code, current_run_id),
    )
    for item in rows or []:
        status = str(item.get("status") or "").strip()
        if status == "success":
            return False
        candidate = {
            "id": item.get("id"),
            "task_code": task_code,
            "status": status,
            "summary": _decode_summary(item.get("summary_json")),
        }
        if _is_sample_failure_alert_worthy(candidate):
            return True
    return False


def _should_dispatch_recovery_alert_for_run(
    row: dict[str, Any],
    *,
    prior_failures: int | None = None,
) -> bool:
    task_code = str(row.get("task_code") or "")
    if not task_code:
        return False
    prior = _non_negative_int(prior_failures)
    if _is_immediate_failure_alert_task(task_code):
        return prior > 0
    if prior >= FAILURE_ALERT_MIN_CONSECUTIVE_RUNS:
        return True
    if prior <= 0:
        return False
    return _prior_failure_streak_has_sample_alert(row)


def _decode_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _normalize_row(
    row: dict[str, Any] | None,
    *,
    task_code: str | None = None,
    task_name: str | None = None,
    summary_fields: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    if not row:
        return None
    item = dict(row)
    if task_code and not item.get("task_code"):
        item["task_code"] = task_code
    if task_name and not item.get("task_name"):
        item["task_name"] = task_name
    summary = _decode_summary(item.pop("summary_json", None))
    for field in summary_fields:
        value = item.get(field)
        if value is not None and value != "":
            summary.setdefault(field, _decode_json_value(value))
    item["summary"] = summary
    return item


def _safe_query_rows(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    try:
        return query(sql, params)
    except Exception:
        log.warning("failed to load scheduled task runs", exc_info=True)
        return []


def _scheduled_task_runs(task_code: str, *, limit: int) -> list[dict[str, Any]]:
    if task_code == "all":
        rows = _safe_query_rows(
            """
            SELECT id, task_code, task_name, status, scheduled_for, started_at, finished_at,
                   duration_seconds, summary_json, error_message, output_file
            FROM scheduled_task_runs
            ORDER BY started_at DESC, id DESC
            LIMIT %s
            """,
            (limit,),
        )
    else:
        rows = _safe_query_rows(
            """
            SELECT id, task_code, task_name, status, scheduled_for, started_at, finished_at,
                   duration_seconds, summary_json, error_message, output_file
            FROM scheduled_task_runs
            WHERE task_code = %s
            ORDER BY started_at DESC, id DESC
            LIMIT %s
            """,
            (task_code, limit),
        )
    return [_normalize_row(row) for row in rows if row]


def _roi_hourly_runs(*, limit: int) -> list[dict[str, Any]]:
    task = TASK_DEFINITIONS["roi_hourly_sync"]
    rows = _safe_query_rows(
        """
        SELECT id, task_code, status, NULL AS scheduled_for,
               sync_started_at AS started_at, sync_finished_at AS finished_at,
               duration_seconds, summary_json, error_message, NULL AS output_file
        FROM roi_hourly_sync_runs
        ORDER BY sync_started_at DESC, id DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [
        _normalize_row(row, task_code=task["code"], task_name=task["name"])
        for row in rows
        if row
    ]


def _dianxiaomi_order_import_runs(*, limit: int) -> list[dict[str, Any]]:
    task = TASK_DEFINITIONS["dianxiaomi_order_import"]
    rows = _safe_query_rows(
        """
        SELECT id, status, NULL AS scheduled_for,
               started_at, finished_at, duration_seconds, summary_json,
               error_message, NULL AS output_file, date_from, date_to,
               total_pages, fetched_orders, fetched_lines, inserted_lines,
               updated_lines, skipped_lines, included_shopify_ids_count
        FROM dianxiaomi_order_import_batches
        ORDER BY started_at DESC, id DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [
        _normalize_row(
            row,
            task_code=task["code"],
            task_name=task["name"],
            summary_fields=(
                "date_from",
                "date_to",
                "total_pages",
                "fetched_orders",
                "fetched_lines",
                "inserted_lines",
                "updated_lines",
                "skipped_lines",
                "included_shopify_ids_count",
            ),
        )
        for row in rows
        if row
    ]


def _meta_realtime_import_runs(*, limit: int) -> list[dict[str, Any]]:
    task = TASK_DEFINITIONS["meta_realtime_import"]
    rows = _safe_query_rows(
        """
        SELECT id, status, NULL AS scheduled_for,
               started_at, finished_at, duration_seconds, summary_json,
               error_message, NULL AS output_file, business_date, snapshot_at,
               ad_account_ids, rows_imported, spend_usd
        FROM meta_ad_realtime_import_runs
        ORDER BY started_at DESC, id DESC
        LIMIT %s
        """,
        (limit,),
    )
    return [
        _normalize_row(
            row,
            task_code=task["code"],
            task_name=task["name"],
            summary_fields=(
                "business_date",
                "snapshot_at",
                "ad_account_ids",
                "rows_imported",
                "spend_usd",
            ),
        )
        for row in rows
        if row
    ]


def _active_task_snapshot_runs(*, limit: int) -> list[dict[str, Any]]:
    task = TASK_DEFINITIONS["active_task_pre_restart_check"]
    rows = _safe_query_rows(
        """
        SELECT id, snapshot_reason, project_type, task_id, user_id, runner,
               entrypoint, stage, thread_name, process_id, interrupt_policy,
               started_at AS task_started_at, last_heartbeat_at, captured_at,
               details_json
        FROM runtime_active_task_snapshots
        ORDER BY captured_at DESC, id DESC
        LIMIT %s
        """,
        (limit,),
    )
    runs: list[dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        interrupt_policy = str(row.get("interrupt_policy") or "").strip()
        summary = {
            "snapshot_reason": row.get("snapshot_reason"),
            "project_type": row.get("project_type"),
            "task_id": row.get("task_id"),
            "user_id": row.get("user_id"),
            "runner": row.get("runner"),
            "stage": row.get("stage"),
            "interrupt_policy": interrupt_policy,
            "task_started_at": row.get("task_started_at"),
            "last_heartbeat_at": row.get("last_heartbeat_at"),
            "details": _decode_json_value(row.get("details_json")),
        }
        runs.append({
            "id": row.get("id"),
            "task_code": task["code"],
            "task_name": task["name"],
            "status": "failed" if interrupt_policy == "block_restart" else "success",
            "scheduled_for": None,
            "started_at": row.get("captured_at"),
            "finished_at": row.get("captured_at"),
            "duration_seconds": None,
            "summary": {key: value for key, value in summary.items() if value not in (None, "")},
            "error_message": None,
            "output_file": None,
        })
    return runs


def _sort_runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda item: (str(item.get("started_at") or ""), int(item.get("id") or 0)),
        reverse=True,
    )


def list_runs(task_code: str = "all", *, limit: int = 60) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 200))
    code = (task_code or "all").strip() or "all"

    if code == "all":
        rows: list[dict[str, Any]] = []
        rows.extend(_scheduled_task_runs("all", limit=safe_limit))
        rows.extend(_roi_hourly_runs(limit=safe_limit))
        rows.extend(_dianxiaomi_order_import_runs(limit=safe_limit))
        rows.extend(_meta_realtime_import_runs(limit=safe_limit))
        rows.extend(_active_task_snapshot_runs(limit=safe_limit))
        return _sort_runs(rows)[:safe_limit]

    task = TASK_DEFINITIONS.get(code)
    if not task:
        return []
    if task.get("log_table") == "scheduled_task_runs":
        return _scheduled_task_runs(code, limit=safe_limit)
    if task.get("log_table") == "roi_hourly_sync_runs":
        return _sort_runs([
            *_roi_hourly_runs(limit=safe_limit),
            *_scheduled_task_runs(code, limit=safe_limit),
        ])[:safe_limit]
    if task.get("log_table") == "dianxiaomi_order_import_batches":
        return _sort_runs([
            *_dianxiaomi_order_import_runs(limit=safe_limit),
            *_scheduled_task_runs(code, limit=safe_limit),
        ])[:safe_limit]
    if task.get("log_table") == "meta_ad_realtime_import_runs":
        return _sort_runs([
            *_meta_realtime_import_runs(limit=safe_limit),
            *_scheduled_task_runs(code, limit=safe_limit),
        ])[:safe_limit]
    if code == "active_task_pre_restart_check":
        return _active_task_snapshot_runs(limit=safe_limit)
    return []


def latest_run(task_code: str = "all") -> dict[str, Any] | None:
    rows = list_runs(task_code, limit=1)
    return rows[0] if rows else None


def latest_failure_alert() -> dict[str, Any] | None:
    """Return the latest failed run only if it is still the latest run for that task."""
    for task in task_definitions():
        if not task.get("log_table"):
            continue
        try:
            row = latest_run(task["code"])
        except Exception:
            log.warning("failed to load scheduled task alert", exc_info=True)
            continue
        if row and row.get("status") == "failed":
            try:
                if _should_dispatch_failure_alert_for_run(row):
                    return row
            except Exception:
                log.warning("failed to apply scheduled task alert rule", exc_info=True)
                continue
    return None
