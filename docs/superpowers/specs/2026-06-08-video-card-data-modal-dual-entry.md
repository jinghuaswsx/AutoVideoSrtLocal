# 视频卡片数据弹窗双入口设计

Date: 2026-06-08

## Anchors

- `AGENTS.md`：文档驱动代码、素材管理路由验证、POST CSRF 和 focused pytest 规则。
- `web/templates/CLAUDE.md`：模板修改需遵守 Jinja/CSRF/登录守卫规则。
- `docs/superpowers/specs/2026-06-06-medias-product-video-workbench-design.md`：素材工作台视频卡片 V2 数据面板、翻译版本、投放 ROAS、订单量和 AI 8 国评估口径。
- `docs/superpowers/specs/2026-05-22-mk-video-material-search-index-design.md`：`/xuanpin/mk#videos` 明空视频素材库卡片和本地快照事实来源。
- `docs/superpowers/specs/2026-06-08-medias-workbench-mk-card-flow-alignment.md`：素材工作台与明空视频卡片流程对齐。

## Context

素材工作台 `/medias/product/video_workbench/<pid>` 已经在每张视频卡右侧展示运营判断需要的数据：翻译版本、投放消耗 / ROAS、订单量和 AI 8 国评估。选品中心 `/xuanpin/mk#videos` 的明空视频卡片仍只能看到卡片基础字段，运营需要在选品中心不离开当前卡片即可查看同一份工作台数据。

当前素材工作台卡片底部已有“广告数据”按钮，但它打开的是广告明细弹窗；用户希望“数据”入口显示工作台右侧这种完整视频数据卡片，同时里面的“广告 / 广告明细”按钮仍然能打开对应广告明细。

## Goals

1. 在 `/xuanpin/mk#videos`、`昨天消耗前300` 等使用明空视频卡片的入口，卡片顶部新增一个图标按钮，含义为“数据”。
2. 点击“数据”后打开自适应 modal，内容使用素材工作台视频卡片右侧同款数据面板：翻译版本、投放消耗 / ROAS、订单量、AI 8 国评估。
3. 选品中心数据弹窗读取现有 `/medias/api/product/<pid>/video-workbench`，按卡片的 `media_product_id` / `material_status.media_product_id` 和明空 `video_path` 匹配对应工作台卡片，确保是同一份后端数据。
4. 素材工作台卡片的“广告数据”按钮改为同一份完整数据弹窗入口；数据弹窗内部保留“广告明细”和语种“广告”按钮，继续懒加载 `/medias/api/product/<pid>/video-workbench/ad-detail`。
5. Modal 必须桌面和移动端自适应：宽屏可展示完整数据表，窄屏上下堆叠，表格在弹窗内横向滚动，不撑破视口。

## Non-Goals

1. 不新增数据库表、迁移或定时任务。
2. 不改变明空素材快照同步、入库、创建小语种任务和 AI 评估接口契约。
3. 不把选品中心未命中本地素材管理产品的卡片强行兜底成产品级数据；没有 `media_product_id` 时弹窗显示暂无工作台数据。
4. 不取消素材工作台页面右侧的内联数据面板；它仍作为工作台主视图。

## UX Contract

### 选品中心卡片

- 顶部状态图标区域新增“数据”图标按钮，使用图标承载，悬停 title/aria-label 为“查看视频数据”。
- 若卡片存在素材管理产品 ID，点击后：
  - 打开 modal 并显示加载状态。
  - 请求 `/medias/api/product/<pid>/video-workbench`。
  - 优先按 `video_path` 精确匹配卡片；其次按 `material_key`、视频名匹配。
  - 匹配成功后渲染数据面板。
- 若没有素材管理产品 ID 或匹配不到卡片，modal 显示“暂无工作台数据”，并提示先入库或确认素材管理产品关联。
- 数据弹窗中的翻译版本必须归属到当前视频卡绑定素材：优先使用 `source_raw_id` / `source_ref_id` / 当前绑定 `media_item_id`，仅允许同日期、同产品素材名的历史文件名指纹兜底；禁止只凭“补充素材”等宽泛关键词合并其他原素材。

### 素材工作台卡片

- 卡片“广告数据”按钮改为打开完整数据 modal。
- 右侧内联数据面板继续展示，不影响页面首屏判断。
- 数据 modal 内的“广告明细”和各语种“广告”按钮才打开原广告明细弹窗。

### Modal

- 宽度 `min(1120px, 96vw)` 以上，最大高度不超过 `90vh`，内部滚动。
- 表格区域单独横向滚动，避免弹窗在移动端溢出。
- 关闭按钮固定在 modal 顶部。

## Verification

1. `pytest tests/test_medias_product_video_workbench.py tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q`
2. `python3 -m compileall web/routes/medias web/routes/xuanpin.py -q`
3. `git diff --check`
4. 按项目规则跳过全量 `pytest -q`：本次只改视频卡片前端和已覆盖路由模板断言，不涉及 schema/auth/deploy/scheduler/LLM/storage/billing 等广影响模块。
