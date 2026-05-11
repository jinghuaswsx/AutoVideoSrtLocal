# Shopify Image Localizer 多域名图片映射

日期：2026-05-11

## 背景

Shopify Image Localizer 已支持按商品启用多个域名，并能在桌面端选择目标域名运行 EZ Product Image Translate 与 Translate and Adapt 自动换图。多域名商品里，默认域名（当前为 `newjoyloo.com`）与第二、第三或更多域名（如 `omurio.com`）对应同一个业务商品，但 Shopify 店铺、product ID、CDN store path 与上传时间戳不同。

实测 `baseball-cap-organizer-rjc`：

- `https://newjoyloo.com/products/baseball-cap-organizer-rjc` 的 Shopify product ID 是 `8558985150637`。
- `https://omurio.com/products/baseball-cap-organizer-rjc` 的 Shopify product ID 是 `9163928862932`。
- 轮播图文件 basename 多数相同，但 Shopify CDN 目录不同，完整 URL 与从 URL 提取的 hash token 不能作为跨域名稳定身份。
- 详情图可能仍是同一批外链，也可能在 Translate and Adapt 上传保存后变成各店铺自己的 Shopify CDN URL。

现有桌面端匹配逻辑优先用目标域名当前图片 URL/文件名里的 32 位 token，再回退到 `source_index`。当第二域名的 CDN URL 与默认域名不同、且本地翻译图只记录默认域名原图 token 时，轮播图和详情图会出现“本质同图，但找不到候选”的问题，只能落到视觉兜底或失败。

## 事实来源

- `tools/shopify_image_localizer/CLAUDE.md`：桌面端 CDP、EZ/TAA、发布与回归红线。
- `docs/superpowers/plans/2026-05-07-multi-domain-unification.md`：多域名产品链接与 Shopify Image Localizer 的域名感知合同。
- `docs/superpowers/specs/2026-05-09-product-link-default-domain.md`：默认域名是全系统 URL 解析的单一语义来源。
- `tools/shopify_image_localizer/rpa/run_product_cdp.py`：轮播图配对、详情图配对、视觉兜底与 TAA 替换入口。
- `tools/shopify_image_localizer/rpa/taa_cdp.py`：详情页 HTML 图片候选选择逻辑。

## 目标

1. 默认域名建立 canonical 图片身份，其他域名只建立 alias 映射。
2. 第二、第三、第十个域名执行换图时，不重复下载和翻译图片，直接复用默认域名那套英语原图与目标语种翻译图。
3. 轮播图与 Translate and Adapt 详情图都能通过跨域名映射找到正确的小语种替换图。
4. 映射自动完成；用户无需维护多份图片文件。
5. 桌面端提供“映射管理”入口，用于查看当前目标域名相对默认域名的自动映射结果和低置信度项。

## 非目标

- 不新增第二套图片存储；`omurio.com` 等非默认域名不下载并保存独立原图资产。
- 不让图片翻译流程按域名重复运行。
- 不改服务端图片翻译任务的产物结构；目标语种翻译图继续按 canonical 原图身份复用。
- 第一版不做复杂人工拖拽映射编辑；低置信度项先展示诊断，自动化仍按稳定规则执行。

## 设计

### Canonical 图片身份

默认域名是 canonical source。对一个 `product_code`：

- canonical carousel image：默认域名英文产品 `.js` 的 `images[index]`。
- canonical detail image：默认域名英文产品 `description/body_html` 中参与替换的 `<img>`，按出现顺序编号，编号偏移为 `carousel_count + detail_index`。
- canonical source key：优先用 `source_index`，其次用默认域名图片 URL/filename 中的 32 位 token，再其次用文件 basename。

目标语种本地化图只绑定 canonical source key。非默认域名运行时不生成新图，只把目标域名当前图片映射回 canonical source key。

### 自动 alias 映射

桌面端每次运行会抓：

1. 默认域名英文产品 `.js`。
2. 当前目标域名英文产品 `.js`。
3. 当前目标域名目标语种产品 `.js`（用于 TAA 当前详情 HTML）。

对于非默认域名，生成 `DomainImageMapping`：

- `carousel_slots`：目标域名轮播 slot index → canonical carousel source index / token。
- `detail_sources`：目标域名详情图 src token/name → canonical detail source index / token。

匹配策略按稳定度排序：

1. 相同位置 + basename 相同：高置信度。
2. 相同位置但 basename 不同：中置信度，允许自动使用，因为同一商品跨店铺复制后图片顺序是最稳定信号。
3. basename 相同但位置不同：中置信度，记录诊断。
4. 无法自动匹配：保留给既有视觉兜底。

### 轮播图替换

`pair_carousel_images()` 增加可选 `domain_mapping`：

1. 先尝试既有 token 匹配。
2. token 失败时，查目标域名 token/name 对应的 canonical token。
3. 再按 alias 的 canonical source index 查目标语种本地化图。
4. 仍失败时走既有视觉兜底。

这保证 `omurio.com` 当前 slot 0 即使 CDN URL 不同，也会映射到 `newjoyloo.com` canonical slot 0，并拿到 slot 0 的德语/法语/意大利语翻译图。

### 详情图替换

TAA 当前 `body_html` 中的 `<img src>` 先通过 domain mapping 转换为 canonical source index：

- `source_index_by_token[target_token] = canonical_source_index`
- `source_index_by_token[target_name_key] = canonical_source_index`

`taa_cdp.plan_body_html_replacements()` 调整为：当目标 src 有 token 但 token 找不到候选时，如果 source index map 已给出 canonical source index，就改用 source index 候选，而不是直接判 missing。

### 映射管理入口

桌面端新增“映射管理”按钮：

- 要求输入商品 ID。
- 读取当前选择的域名；若是默认域名，展示“默认域名无需跨域映射”。
- 抓默认域名与当前域名英文产品数据，生成映射报告。
- 弹窗展示轮播图与详情图映射数量、低置信度项、缺失项。

第一版不保存手工映射，因为自动规则已覆盖当前业务主路径；后续若出现跨店铺图片顺序明显不一致，再扩展为本地 JSON 或服务端表持久化。

## 验证

- `pytest tests/test_shopify_image_localizer_batch_cdp.py tests/test_shopify_image_localizer_domains.py -q`
- `python3 -c 'import py_compile; [py_compile.compile(p, doraise=True) for p in ["tools/shopify_image_localizer/gui.py","tools/shopify_image_localizer/controller.py","tools/shopify_image_localizer/rpa/run_product_cdp.py","tools/shopify_image_localizer/rpa/taa_cdp.py"]]; print("ok")'`
- `bash scripts/build_shopify_image_localizer_wine.sh --version <next-version> --release-note "..."`

