# 大文件拆分与维护性治理设计

日期：2026-05-01
状态：已确认设计，待实现计划
分支：`docs/spec-large-file-decomposition`
基线：`master c395cf53`

## 背景

`appcore/runtime.py`（3061 行）、`web/routes/medias.py`（3304 行）、`appcore/order_analytics.py`（2686 行）三个文件已经长到难以维护：单文件超 2.5k 行、`PipelineRunner` 单类跨 ~1990 行、`medias` 单 blueprint 承载 ~78 条路由。文件越大，AI 上下文越容易溢出，PR review 也越粗，回归风险越高。

本轮目标是行为不变的结构治理：把这三个大文件改成 package（`appcore/runtime/`、`web/routes/medias/`、`appcore/order_analytics/`），按职责拆 sub-module，外部导入路径和页面/API 行为不变。本轮不改业务逻辑、不重构 `PipelineRunner` 类内部、不动其他文件。

## 目标

1. 三个大文件改成 package 形态，单 sub-module ≤ 1000 行（`PipelineRunner` 单类除外，本轮保持原样）。
2. 公开导入路径完全兼容：`from appcore import runtime as r` / `from web.routes.medias import bp as medias_bp` / `from appcore import order_analytics as oa` 调用方零改动。
3. 每个被拆的 sub-module 在迁出之前，先有 characterization test 锁定输入输出"形状"。
4. 每次拆分都是一个独立可回滚的 PR，所有现有测试套件持续绿。
5. 拆分顺序：`order_analytics` → `medias` → `runtime`（从独立度高、子类引用少到耦合度高、子类多）。

## 非目标

- 不修复任何业务逻辑 bug（包括下文 `已知小问题` 列出的 4 项）。
- 不重设计 `PipelineRunner` 的模板方法继承结构、不改 6 个 `runtime_*.py` 子类。
- 不调整 medias 路由 URL 或响应字段。
- 不改 `order_analytics` 任何 SQL / 数据口径。
- 不引入新依赖、不改 CI、不改部署流程。
- 不在本轮覆盖其他大文件（`web/routes/task.py` 1473 行、`appcore/bulk_translate_runtime.py` 1362 行、`appcore/medias.py` DAO 层 1315 行、`appcore/pushes.py` 1207 行 等列入"后续候补"）。

## 设计原则

### 行为不变

"行为不变"是这一轮的硬约束。任何函数从 module A 移到 module B，移完之后：

- 函数签名（参数名、默认值、类型注解）完全一致
- 函数体逐字符相等（git diff 等价于"删除 + 插入"，不是"修改"）
- 模块内常量、`log = logging.getLogger(__name__)` 这种保留在迁出 module，不重命名
- 所有外部引用（包括 `_` 前缀的"私有"函数被外部模块或子类显式引用的情况）保持原符号路径可用

### Façade re-export

每个 package 的 `__init__.py` 用**显式列名**做 re-export，不用 `from .x import *`。原因：

- 显式清单可被 pyright/pyflakes 静态检查；
- 给后续 reviewer 一个明确的"公开 API 边界"；
- 防止 sub-module 顺手定义的 helper 被外部依赖。

re-export 清单的生成规则：

1. 落库所有外部引用名：
   ```bash
   git grep -hE "from appcore\.runtime import" | sed 's/.*import //' | tr ',' '\n' | sort -u
   git grep -hE "from appcore import order_analytics" -A1
   git grep -hE "from web\.routes\.medias import"
   ```
2. 落库结果作为该 package 的 `__init__.py` 必须 re-export 的"白名单"
3. 此外所有 `def <name>` / `class <name>` 在原 module 中以非 `_` 开头的公开符号默认全部 re-export
4. 以 `_` 开头但出现在白名单的，强制 re-export（典型例子：`_av_target_lang`、`_resolve_translate_provider` 被 `runtime_de.py` 等子类引用）

### Characterization Tests

每个 sub-module 迁出前必须先有 characterization test（`tests/characterization/test_<package>_<sub>_baseline.py`），目的是锁定行为不变。

测试要求：

- **形状测试**：每个公开函数和路由调用一次（用最简的合理输入），断言返回值的类型、顶层 keys、长度、关键字段类型；
- **不锁内容**：不锁具体业务数值（避免和现有 unit test 重复 + 减少 fixture 维护），只锁结构形状；
- **不联网、不写库**：用 `unittest.mock` patch DB / HTTP 调用；
- **快速**：单文件 ≤ 1s 跑完；
- **存活**：拆完之后保留这些 characterization test，作为长期回归防护。

每个 sub-module PR 的 CI 必须跑：

- `pytest tests/test_<existing>.py tests/characterization/test_<this>_baseline.py -q`
- `python -m pyflakes <package>/`（捕获 façade re-export 漏掉的符号）
- `python -c "from <package> import <symbol1>, <symbol2>, ..."`（白名单 smoke 检查）

### PR 粒度

每个 sub-module 一个 PR：

1. **新增 characterization test** → 单独 PR（先合，绿）
2. **搬代码 + 配 façade re-export** → 单独 PR（搬代码动作机械化，diff 全部是"删 + 插"）

不允许在搬代码 PR 里顺手改名、加注释、调整顺序。

## 阶段 1：`appcore/order_analytics` → package（最先做，最独立）

### 拆分图

`appcore/order_analytics.py` (2686 行) → `appcore/order_analytics/`：

| sub-module | 大致行数 | 公开符号 |
|---|---|---|
| `_constants.py` | ~50 | `META_ATTRIBUTION_CUTOVER_HOUR_BJ`、`META_ATTRIBUTION_TIMEZONE`、`_SHOPIFY_COLS`、`_TITLE_RE`、`_SHOP_TS_FMT`、`_META_AD_REQUIRED_COLS`、`_META_AD_NUMERIC_FIELDS`、`_DIANXIAOMI_SITE_DOMAINS`、`_DIANXIAOMI_EXCLUDED_DOMAINS` |
| `_helpers.py` | ~150 | `_money`、`_roas`、`_revenue_with_shipping`、`_beijing_now`、`_business_hour`、`_safe_decimal_float`、`_safe_int`、`_safe_float`、`_safe_float_default`、`_parse_meta_date`、`_parse_iso_date_param`、`_combined_link_text`、`_canonical_product_handle`、`_compute_pct_change`、`_json_dumps_for_db`、`_parse_dianxiaomi_ts` |
| `dianxiaomi.py` | ~600 | `DianxiaomiProductScope`、`compute_meta_business_window_bj`、`compute_order_meta_attribution`、`_infer_dianxiaomi_site_code_from_text`、`extract_dianxiaomi_shopify_product_id`、`extract_dianxiaomi_product_handle`、`build_dianxiaomi_product_scope`、`_dianxiaomi_order_lines`、`_resolve_dianxiaomi_line_product`、`normalize_dianxiaomi_order`、`start_dianxiaomi_order_import_batch`、`finish_dianxiaomi_order_import_batch`、`_dianxiaomi_order_line_values`、`upsert_dianxiaomi_order_lines`、`get_dianxiaomi_order_import_batches`、`_dianxiaomi_order_time_expr`、`get_dianxiaomi_order_analysis` |
| `shopify_orders.py` | ~250 | `parse_shopify_file`、`_parse_excel`、`_parse_shopify_ts`、`import_orders`、`get_import_stats`、`fetch_product_page_title`、`refresh_product_titles`、`match_orders_to_products` |
| `meta_ads.py` | ~450 | `product_code_candidates_for_ad_campaign`、`resolve_ad_product_match`、`_normalize_meta_ad_row`、`parse_meta_ad_file`、`import_meta_ad_rows`、`match_meta_ads_to_products`、`get_meta_ad_stats`、`get_meta_ad_periods`、`_resolve_meta_ad_period`、`_coerce_meta_product_id`、`_aggregate_meta_ad_summary_rows`、`get_meta_ad_summary` |
| `realtime.py` | ~350 | `_get_realtime_order_details`、`_get_realtime_campaign_details`、`_get_daily_campaigns`、`_get_today_realtime_meta_totals`、`_get_realtime_ad_updated_at`、`get_realtime_roas_overview`、`get_true_roas_summary` |
| `country_dashboard.py` | ~120 | `_sort_order_dashboard_rows`、`get_country_dashboard`、`_coerce_country_dashboard_date`、`_coerce_ad_frequency` |
| `periodic.py` | ~200 | `_load_enabled_lang_codes`、`get_enabled_country_columns`、`_month_range`、`get_monthly_summary`、`get_product_country_detail`、`get_daily_detail`、`get_weekly_summary`、`search_products`、`get_available_months` |
| `dashboard.py` | ~350 | `_resolve_period_range`、`_resolve_compare_range`、`_aggregate_orders_by_product`、`_aggregate_ads_by_product`、`_count_media_items_by_product`、`_join_and_compute_dashboard_rows`、`get_dashboard`、`_format_period_label`、`_load_products`、`_summarize_dashboard` |
| `__init__.py` | ~80 | façade，re-export 上述全部 |

### 迁移顺序（10 个 PR）

| PR | 内容 |
|---|---|
| 1.0 | 新增 `tests/characterization/test_order_analytics_baseline.py` 覆盖现有公开函数（含 helpers）。先合并并通过现有测试套件。 |
| 1.1 | 单文件 `appcore/order_analytics.py` 改成 `appcore/order_analytics/__init__.py`，把常量挪到 `_constants.py`、helper 挪到 `_helpers.py`，其余函数留在 `__init__.py`。`__init__.py` 顶部 `from ._constants import *` + `from ._helpers import *`。所有 import 链 + 测试通过后合并。 |
| 1.2 | 拆 `dianxiaomi.py`（依赖 `_helpers` + `_constants`）。 |
| 1.3 | 拆 `shopify_orders.py`（依赖 `_helpers` + `_constants`）。 |
| 1.4 | 拆 `meta_ads.py`（依赖 `_helpers` + `_constants`）。 |
| 1.5 | 拆 `realtime.py`（依赖 `_helpers` + `dianxiaomi.compute_meta_business_window_bj`）。 |
| 1.6 | 拆 `periodic.py`（依赖 `_helpers`）。 |
| 1.7 | 拆 `country_dashboard.py`（依赖 `_helpers`）。 |
| 1.8 | 拆 `dashboard.py`（依赖 `_helpers` + 多模块）。 |
| 1.9 | 阶段验证：`pytest tests/test_order_analytics_*.py tests/characterization/test_order_analytics_*.py -q`、`web/routes/order_analytics.py` 端到端 smoke。打 tag `refactor/decomp-stage-1-done`。 |

### 暂缓

- `import_orders` 内部"解析 + 持久化 + 匹配"混合逻辑可以再细分，本轮**不动**。
- `get_dashboard` 的内部嵌套 helpers 多，可在阶段 1 完成后第二轮再细分，本轮保持作为 `dashboard.py` 单 module。

## 阶段 2：`web/routes/medias` → package（中等耦合）

### 拆分图

`web/routes/medias.py` (3304 行) → `web/routes/medias/`：

| sub-module | 路由数 | URL 前缀 / 主要路由 |
|---|---|---|
| `pages.py` | 6 | `/`、`/<product_code>`、`/products/<int:pid>/translation-tasks`、`/mk-selection`、`/api/languages`、`/api/users/active` |
| `products.py` | 8 | `/api/products`（CRUD）、`/api/products/<int:pid>/owner`、`/api/mk-copywriting` |
| `pushes.py` | 6 | `/api/products/<int:pid>/product-{links,unsuitable,localized-texts}-push*` |
| `shopify_image.py` | 4 | `/api/products/<int:pid>/shopify-image/<lang>/{confirm,unavailable,clear,requeue}` |
| `link_check.py` | 3 | `/api/products/<int:pid>/link-check*` |
| `evaluation.py` | 3 | `/api/products/<int:pid>/evaluate*` |
| `raw_sources.py` | 4 | `/api/products/<int:pid>/raw-sources`、`/api/raw-sources/<int:rid>` |
| `translate.py` | 2 | `/api/products/<int:pid>/translate`、`/api/products/<int:pid>/translation-tasks` |
| `items.py` | 4 | `/api/products/<int:pid>/items/{bootstrap,complete}`、`/api/items/<int:item_id>` |
| `covers.py` | 11 | `/api/products/<int:pid>/cover/*`、`/api/products/<int:pid>/item-cover/*`、`/api/items/<int:item_id>/cover/*`、`/cover/<int:pid>`、`/item-cover/<int:item_id>`、`/thumb/<int:item_id>`、`/api/items/<int:item_id>/play_url` |
| `detail_images.py` | 13 | `/api/products/<int:pid>/detail-images*`、`/api/products/<int:pid>/detail-image-translate-tasks`、`/detail-image/<int:image_id>` |
| `mk_selection.py` | 6 | `/api/mk-selection*`、`/api/mk-media`、`/api/mk-video`、`/api/mk-detail/<int:mk_id>` |
| `media_upload.py` | 5 | `/api/local-media-upload/<upload_id>`、`/object`、`/obj/<path:object_key>`、`/raw-sources/<int:rid>/{video,cover}` |
| `_serializers.py` | - | `_serialize_product`、`_serialize_item`、`_serialize_raw_source`、`_serialize_link_check_task`、`_serialize_detail_image`、`_collect_link_check_reference_images` |
| `_helpers.py` | - | `_can_access_product`、`_ensure_product_listed`、`_product_not_listed_response`、`_parse_lang`、`_resolve_upload_user_id`、`_validate_product_code`、`_validate_material_filename_for_product`、`_check_filename_prefix`、`_suggest_raw_source_title`、`_validate_raw_source_display_name`、`_list_raw_source_allowed_english_filenames`、`_client_filename_basename`、`_raw_source_filename_error_response`、`_dianxiaomi_rankings_columns`、`_language_name_map`、`_download_image_to_local_media`、`_reserve_local_media_upload`、`_is_media_available`、`_download_media_object`、`_delete_media_object`、`_send_media_object`、`_int_or_none`、`_json_number_or_none`、`_start_image_translate_runner`、`_default_image_translate_model_id`、`_schedule_material_evaluation`、`_material_evaluation_message`、`_medias_page_context`、`_is_admin`、`_normalize_mk_copywriting_query`、`_mk_product_link_tail`、`_format_mk_copywriting_text`、`_extract_mk_copywriting`、`_get_mk_api_base_url`、`_normalize_mk_media_path`、`_mk_video_cache_object_key`、`_cache_mk_video`、`_build_mk_request_headers`、`_is_mk_login_expired`、`_get_mk_token`、`_shopify_image_lang_or_404`、`_detail_image_*`、`_detail_images_*` 等约 50 个 helper |
| `__init__.py` | - | `bp = Blueprint("medias", __name__, url_prefix="/medias")` + 装配 |

### Sub-blueprint 共享 bp 模式

`web/routes/medias/__init__.py`：

```python
from flask import Blueprint

bp = Blueprint("medias", __name__, url_prefix="/medias")

# 关键：导入 sub-module 后，sub-module 的 @bp.route 装饰会注册到这个 bp
from . import (
    pages, products, pushes, shopify_image, link_check, evaluation,
    raw_sources, translate, items, covers, detail_images, mk_selection,
    media_upload,
)
```

每个 sub-module 顶部：

```python
from . import bp  # 共享 __init__.py 里的 Blueprint 实例

@bp.route("/api/products", methods=["GET"])
def api_list_products():
    ...
```

调用方 `from web.routes.medias import bp as medias_bp`（`web/app.py`）不变。

### 迁移顺序（17 个 PR）

| PR | 内容 |
|---|---|
| 2.0 | 新增 `tests/characterization/test_medias_routes_baseline.py`，用 Flask test client 对每个路由发一次最简请求，断言 status code、Content-Type、JSON 顶层 keys。鉴权路由用现有 `tests/conftest.py` 的 admin fixture。 |
| 2.1 | 单文件 `medias.py` 改成 `medias/__init__.py`，蓝图实例移到顶部，原文件全部内容暂时仍在 `__init__.py`。`web/app.py` import 不变。 |
| 2.2 | 拆 `_serializers.py`（纯函数，依赖少）。 |
| 2.3 | 拆 `_helpers.py`（依赖 `_serializers`、`appcore.medias`、`web.background`）。 |
| 2.4 | 拆 `pages.py`。 |
| 2.5 | 拆 `products.py`（含 `mk_copywriting` GET）。 |
| 2.6 | 拆 `pushes.py`。 |
| 2.7 | 拆 `shopify_image.py`。 |
| 2.8 | 拆 `link_check.py`。 |
| 2.9 | 拆 `evaluation.py`。 |
| 2.10 | 拆 `raw_sources.py`。 |
| 2.11 | 拆 `translate.py`。 |
| 2.12 | 拆 `items.py`。 |
| 2.13 | 拆 `covers.py`。 |
| 2.14 | 拆 `detail_images.py`（最大子集）。 |
| 2.15 | 拆 `mk_selection.py`。 |
| 2.16 | 拆 `media_upload.py`。打 tag `refactor/decomp-stage-2-done`。 |

### 暂缓

- `_helpers.py` 内的 helpers 数量多，可以再拆成 `_filenames.py` / `_media_object.py` / `_mk_api.py` 等，本轮放在一起，等 sub-blueprint 全部稳定再细分。
- 路由参数预处理（如 `_parse_lang`、`_can_access_product`）改成装饰器是更好的设计，本轮**不动**。
- 编码乱码字符串保留原样（详见"已知小问题"）。

## 阶段 3：`appcore/runtime` → package（最后做，子类多）

### 拆分图

`appcore/runtime.py` (3061 行) → `appcore/runtime/`：

| sub-module | 大致行数 | 公开符号 |
|---|---|---|
| `_helpers.py` | ~370 | `_skip_legacy_artifact_upload`、`_save_json`、`_count_visible_chars`、`_join_utterance_text`、`_resolve_original_video_passthrough`、`_is_original_video_passthrough`、`_build_review_segments`、`_translate_billing_provider`、`_translate_billing_model`、`_log_translate_billing`、`_llm_request_payload`、`_llm_response_payload`、`_seconds_to_request_units`、`_resolve_translate_provider`、`_resolve_task_translate_provider`、`_lang_display`、`_is_av_pipeline_task`、`_av_target_lang`、`_tts_final_target_range`、`_compute_next_target`、`_distance_to_duration_range`、`_fit_tts_segments_to_duration`、`_trim_tts_metadata_to_segments` |
| `_av_helpers.py` | ~300 | `_default_av_variant_state`、`_ensure_variant_state`、`_join_source_full_text`、`_load_json_if_exists`、`_restore_av_localize_outputs_from_files`、`_normalize_av_sentences`、`_build_av_localized_translation`、`_build_av_tts_segments`、`_rebuild_tts_full_audio_from_segments`、`_build_av_debug_state`、`_fail_localize`、`_new_silent_runner` |
| `_pipeline_runner.py` | ~1990 | `class PipelineRunner`（**整体保留，本轮不动内部**） |
| `_dispatchers.py` | ~340 | `dispatch_localize`、`run_localize`、`run_av_localize`、`run_analysis_only` |
| `__init__.py` | ~50 | façade，re-export 上述全部 + 子类引用白名单 |

### 子类引用白名单（关键）

通过 `git grep -hE "from appcore\.runtime import"` 落库后，re-export 白名单包括（不限于）：

- `PipelineRunner`（6 个子类全部用：`runtime_de/fr/ja/multi/omni/v2/sentence_translate`）
- `_av_target_lang`、`_is_av_pipeline_task`（多个 runtime 子类引用）
- `_resolve_translate_provider`、`_resolve_task_translate_provider`（`web/routes/task.py` 引用）
- `_new_silent_runner`、`run_localize`、`run_av_localize`、`run_analysis_only`、`dispatch_localize`（多个调用方引用）

实施前用一行命令落库白名单并写入 PR 描述：

```bash
git grep -hE "from appcore\.runtime import" -- '*.py' | sed 's/.*import //' | tr ',()' '\n' | sed 's/^[ \t]*//;s/[ \t]*$//' | grep -v "^$" | sort -u > runtime_reexport_whitelist.txt
```

### 迁移顺序（6 个 PR）

| PR | 内容 |
|---|---|
| 3.0 | 新增 `tests/characterization/test_runtime_baseline.py`，用 mock `EventBus` + 最小 task 字典调用纯函数 helpers（`_resolve_translate_provider`、`_av_target_lang`、`_compute_next_target`、`_fit_tts_segments_to_duration` 等）以及 `PipelineRunner` 的 `_set_step` 等可测方法。**不**实际跑 `_step_*` 完整视频流程。 |
| 3.1 | 单文件 `runtime.py` 改成 `runtime/__init__.py`，把 `class PipelineRunner` 完整搬到 `_pipeline_runner.py`，`__init__.py` 写 `from ._pipeline_runner import PipelineRunner` 等 re-export。`pyflakes runtime/` 通过、6 个子类 import 链通过。 |
| 3.2 | 拆 `_helpers.py`（顶层 helper），同时把 `_pipeline_runner.py` 内对这些 helper 的调用改成 `from ._helpers import _xxx`。 |
| 3.3 | 拆 `_av_helpers.py`（AV 子流程纯函数）。 |
| 3.4 | 拆 `_dispatchers.py`（入口/调度函数）。 |
| 3.5 | 阶段验证：`pytest tests/test_*runtime*.py tests/test_copywriting_*.py tests/test_image_translate_runtime.py tests/test_link_check_runtime.py tests/test_bulk_translate_runtime.py tests/test_ja_translate_pipeline.py tests/characterization/test_runtime_*.py -q`；额外手工 web pipeline smoke（跑一个 EN→DE 翻译任务到 `done`）。打 tag `refactor/decomp-stage-3-done`。 |

### 暂缓

- **`PipelineRunner` 内部 `_step_*` 抽核**（即原方案 B 的内容）：本轮**不做**，阶段 3.5 完成后单独立项。
- `_run_tts_duration_loop`（~485 行单方法）的内部分解：暂缓。
- 6 个 `runtime_*.py` 子类的拆分：本轮不动。

## 横向决策

### `__init__.py` re-export 写法

```python
# appcore/order_analytics/__init__.py
from ._constants import (
    META_ATTRIBUTION_CUTOVER_HOUR_BJ,
    META_ATTRIBUTION_TIMEZONE,
    _SHOPIFY_COLS, _TITLE_RE, _SHOP_TS_FMT,
    _META_AD_REQUIRED_COLS, _META_AD_NUMERIC_FIELDS,
    _DIANXIAOMI_SITE_DOMAINS, _DIANXIAOMI_EXCLUDED_DOMAINS,
)
from ._helpers import (
    _money, _roas, _revenue_with_shipping, _beijing_now, _business_hour,
    _safe_decimal_float, _safe_int, _safe_float, _safe_float_default,
    _parse_meta_date, _parse_iso_date_param, _combined_link_text,
    _canonical_product_handle, _compute_pct_change, _json_dumps_for_db,
    _parse_dianxiaomi_ts,
)
from .dianxiaomi import (
    DianxiaomiProductScope, build_dianxiaomi_product_scope,
    compute_meta_business_window_bj, compute_order_meta_attribution,
    extract_dianxiaomi_shopify_product_id, extract_dianxiaomi_product_handle,
    normalize_dianxiaomi_order, start_dianxiaomi_order_import_batch,
    finish_dianxiaomi_order_import_batch, upsert_dianxiaomi_order_lines,
    get_dianxiaomi_order_import_batches, get_dianxiaomi_order_analysis,
)
# ... 其他 sub-module re-export 略

__all__ = [
    # 公开 API（不以 _ 开头）
    "DianxiaomiProductScope", "build_dianxiaomi_product_scope",
    "compute_meta_business_window_bj", "compute_order_meta_attribution",
    "normalize_dianxiaomi_order",
    "parse_shopify_file", "import_orders", "get_import_stats",
    "fetch_product_page_title", "refresh_product_titles", "match_orders_to_products",
    "parse_meta_ad_file", "import_meta_ad_rows", "match_meta_ads_to_products",
    "get_meta_ad_stats", "get_meta_ad_periods", "get_meta_ad_summary",
    "get_realtime_roas_overview", "get_true_roas_summary",
    "get_country_dashboard",
    "get_monthly_summary", "get_product_country_detail", "get_daily_detail",
    "get_weekly_summary", "search_products", "get_available_months",
    "get_dashboard", "get_enabled_country_columns",
    # 常量
    "META_ATTRIBUTION_CUTOVER_HOUR_BJ", "META_ATTRIBUTION_TIMEZONE",
]
```

`__all__` 只列公开符号，但 `_` 前缀的依然 import 进来（不放 `__all__`），这样 `from appcore.order_analytics import _money` 能用，但 `from appcore.order_analytics import *` 不会污染。

### Characterization Test 模板

```python
# tests/characterization/test_order_analytics_baseline.py
"""锁定 order_analytics 公开函数的形状。
拆分前先合并这个文件。拆分过程中此文件保持绿。
"""
from unittest.mock import patch
from appcore import order_analytics as oa


def test_money_shape():
    assert oa._money(None) == 0.0
    assert isinstance(oa._money("1.5"), float)


def test_roas_shape():
    assert oa._roas(0.0, 0.0) is None
    result = oa._roas(100.0, 50.0)
    assert isinstance(result, float)


@patch("appcore.order_analytics.query")
def test_get_dashboard_shape(mock_query):
    mock_query.return_value = []
    result = oa.get_dashboard(start_date="2026-01-01", end_date="2026-01-31")
    assert isinstance(result, dict)
    assert {"rows", "summary", "period"}.issubset(result.keys())
```

每个 sub-module 独立一份 baseline 文件，搬运过程中保持不变。

### 完工检查清单（每阶段最后 PR 必须通过）

- [ ] `pytest -q tests/` 全绿
- [ ] `pytest -q tests/characterization/` 全绿
- [ ] `python -c "from appcore import runtime, order_analytics; from web.routes.medias import bp"` 不报错
- [ ] `python -c "from appcore.runtime import PipelineRunner, _av_target_lang, _resolve_translate_provider, _resolve_task_translate_provider, _is_av_pipeline_task, _new_silent_runner, run_localize, run_av_localize, run_analysis_only, dispatch_localize"` 不报错（白名单 smoke）
- [ ] `python -c "from appcore import runtime_de, runtime_fr, runtime_ja, runtime_multi, runtime_omni, runtime_v2, runtime_sentence_translate"` 不报错
- [ ] `pyflakes appcore/runtime/ web/routes/medias/ appcore/order_analytics/` 无 unused/undefined
- [ ] 服务器手工 smoke：登录后台、打开素材管理列表 + 数据分析 dashboard、跑一个翻译任务到 `done`
- [ ] git diff 仅有"删 + 插"，无"改"（用 `git diff -M50% --stat` 验证 rename detection）

### 回滚策略

- 每个 PR 都是独立 commit，可单独 revert（搬代码 PR git 能识别 rename，revert 干净）。
- 所有 façade re-export 失败即时 ImportError，不会污染数据。
- 阶段切换之间留稳定 tag：`refactor/decomp-stage-{1,2,3}-done`，回滚直接 `git reset --hard <tag>` + 部署。

## 已知小问题（本轮不动）

为了"行为不变"，下列已发现的小问题保留原样，不在本轮修：

1. `appcore/runtime.py` 第 17 行 `log = logging.getLogger(__name__)` 与第 66 行 `logger = logging.getLogger(__name__)` 重复定义。
2. `web/routes/medias.py` 第 112 行 `f"涓嶆敮鎸佺殑璇: {lang}"` 是 GBK→UTF-8 转码错乱的中文字符串（应为"不支持的语言"），保留乱码原样。
3. `web/routes/medias.py` 第 11 行与第 17 行各有一次 `import uuid`，重复 import。
4. `appcore/runtime.py` 内 `class PipelineRunner` 单类约 1990 行、`_run_tts_duration_loop` 单方法约 485 行——结构问题，本轮不重构。

每条在搬运 PR 描述里以 `(known issue, keep as-is)` 标注。

## 后续候补（不在本轮范围）

按行数排，本轮之后的拆分候补：

| 文件 | 行数 | 优先级 |
|---|---|---|
| `web/routes/task.py` | 1473 | 高 |
| `appcore/bulk_translate_runtime.py` | 1362 | 中 |
| `appcore/medias.py`（DAO 层） | 1315 | 中 |
| `appcore/pushes.py` | 1207 | 中 |
| `appcore/gemini_image.py` | 997 | 低 |
| `pipeline/translate.py` | 941 | 低 |
| `appcore/material_evaluation.py` | 935 | 低 |
| `web/routes/omni_translate.py` | 934 | 低 |
| `appcore/scheduled_tasks.py` | 925 | 低 |
| `web/routes/subtitle_removal.py` | 923 | 低 |
| `web/routes/multi_translate.py` | 914 | 低 |

第二轮再单独立 spec。

## 时间估算

| 阶段 | 子 PR 数 | 每 PR 时长 | 阶段总计 |
|---|---|---|---|
| 1: order_analytics | 10 | 0.5 day | 5 day |
| 2: medias | 17 | 0.5 day | 8.5 day |
| 3: runtime | 6 | 1 day | 6 day |
| **合计** | **33** | - | **~20 day** |

阶段间可串行（推荐），也可在阶段 1 后半段开始阶段 2 baseline。乐观全工时 ~12 day。

## 实施前置条件

- master HEAD 在 `c395cf53` 或更新（无 conflict）。
- 测试数据库可用：用例可写入临时 schema。
- 至少一个 staging 环境用于阶段切换 smoke。
- 6 个 `runtime_*.py` 子类的现有测试在 master 上是绿的（前置基线）。

## 后续

本 spec 经用户审阅 + 确认后，按用户要求**本轮不进 writing-plans**（用户明确说"本轮先做方案"）。下一轮启动时：

1. 单独立 implementation plan（用 `superpowers:writing-plans` 技能），把本 spec 的 33 个 PR 展开为可独立执行的 step。
2. 阶段 1 PR 1.0 起步。
