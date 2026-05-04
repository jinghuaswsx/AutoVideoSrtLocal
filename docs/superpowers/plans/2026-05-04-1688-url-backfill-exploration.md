# 1688 采购链接自动回填：完整探索报告

> 日期：2026-05-04
> 状态：阶段性完成，约 80 个产品待通过其他途径获取 1688 URL

---

## 1. 背景与目标

ROAS 数据管线的 `media_products.purchase_1688_url` 字段用于存储 1688 采购链接。用户通过店小秘（dianxiaomi.com）的供应链配对功能管理 1688 供应商，期望能自动从店小秘拉取 1688 链接回填到本地产品。

---

## 2. 探索过程

### 2.1 初步探测：找到正确的 API

**文件**：[tools/probe_supply_pairing.py](../../tools/probe_supply_pairing.py)

初期尝试了 7 个候选 API endpoint 全部返回 404。最终通过 Playwright-over-CDP 连接服务器上的共享 Chromium 实例（`http://127.0.0.1:9222`），拦截页面 XHR 请求，找到了真正的 API。

### 2.2 "已配对" Tab 点击拦截实验

用户坚持有 100+ 条已配对记录，但直接调用 API 只返回 11 条。用户推测需要点击页面上的"已配对"tab 才能触发正确的 API。

**探针实验**（[tools/probe_supply_pairing.py](../../tools/probe_supply_pairing.py) 最终版）：

1. 打开 `https://www.dianxiaomi.com/web/supply/pairing`
2. 监听所有 XHR POST 请求
3. 点击"已配对"tab
4. 打印点击前后所有 API 请求

**关键发现：点击"已配对"tab 后，0 个新的 XHR 请求被触发。** 页面在初始加载时就用 `status=2` 请求了已配对数据，tab 切换只是客户端过滤。

---

## 3. API 技术细节

### 3.1 正确的 Endpoint

```
POST https://www.dianxiaomi.com/api/dxmAlibabaProductPair/alibabaProductPairPageList.json
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
X-Requested-With: XMLHttpRequest
```

### 3.2 请求参数

| 参数 | 说明 | 可选值 |
|------|------|--------|
| `pageNo` | 页码 | "1", "2", ... |
| `pageSize` | 每页条数 | 最大 100 |
| `status` | 配对状态 | "0"=全部, "1"=待配对, "2"=已配对 |
| `searchType` | 搜索类型 | "1"=SKU, "2"=关键词 |
| `searchValue` | 搜索值 | SKU 码或中文关键词 |
| `searchMode` | 搜索模式 | "1" |

### 3.3 响应结构

```json
{
  "code": 0,
  "msg": null,
  "data": {
    "page": {
      "list": [ ... ],
      "pageNo": 1,
      "totalPage": 1,
      "pageSize": 100,
      "totalSize": 11
    },
    "status": 2,
    "isOpenAlibabaPair": true
  }
}
```

### 3.4 Item 字段（31 个）

核心字段：`id`, `sku`, `skuCode`, `name`, `sourceUrl`（1688/Amazon/TikTok 链接），`alibabaProductId`, `alibabaProductList`, `imgUrl`, `state`

### 3.5 CDP 连接方式

服务器上运行着带 `--remote-debugging-port=9222` 的 Chromium。所有 Playwright 脚本通过 `p.chromium.connect_over_cdp("http://127.0.0.1:9222")` 连接，共享浏览器会话（保持店小秘登录态）。

并发控制：`appcore.browser_automation_lock`（文件锁）确保同一时间只有一个脚本操作 CDP 浏览器。

---

## 4. 数据真相

### 4.1 店小秘数据全貌

通过探针直接 API 调用获得的实际数据：

| status 参数 | totalSize | 实际返回 | 说明 |
|-------------|-----------|---------|------|
| status=2（已配对） | 11 | 11 | **全部已配对记录只有 11 条** |
| status=1（待配对） | 367 | 91（第 1 页） | 待配对记录有 367 条 |
| status=0（全部） | 11 | 11 | 空搜索时 status=0 行为同 status=2 |

### 4.2 URL 分布

拉取全部 102 条记录（11 已配对 + 91 待配对）后：

| URL 类型 | 数量 | 说明 |
|----------|------|------|
| **1688.com / Alibaba** | **15** | 仅 3 个唯一产品（太阳能收音机、泡泡机、全自动水枪/水炮） |
| Amazon | 13 | 来自 Amazon 采购的产品 |
| TikTok | 1 | 来自 TikTok 采购的产品 |
| 无 URL | 73 | 店小秘中从未配对的记录 |

### 4.3 三个有 1688 URL 的产品

| 店小秘名称 | 1688 URL | 变体数 |
|-----------|----------|--------|
| 太阳能收音机（多色） | `https://detail.1688.com/offer/749406979886.html` | 5 |
| 泡泡机 - 蓝色(10包） | `https://detail.1688.com/offer/1032525502740.html` | 1 |
| 全自动水枪/水炮（多色） | `https://detail.1688.com/offer/1027913506783.html` | 5 |

---

## 5. 核心模块

### 5.1 [appcore/supply_pairing.py](../../appcore/supply_pairing.py)（193 行）

对外暴露两个核心函数：

- **`search_supply_pairing(query, status, page_size, cdp_url)`** — 按 SKU（searchType=1）搜索，失败则回退到关键词搜索（searchType=2）。支持空 query 拉取全部。最多翻 50 页（`MAX_PAGES = 50`）。返回 `{"items": [...], "query": str, "search_type_used": "1"|"2", "total": int}`
- **`extract_1688_url(item)`** — 从 item 中提取 URL，优先级：`sourceUrl` → `alibabaProductList[0].sourceUrl`

内部实现：通过 `page.evaluate()` 在 CDP 浏览器中执行 JavaScript `fetch()`，模拟页面的 AJAX 调用（`URLSearchParams` + `application/x-www-form-urlencoded` + `credentials: "include"`），4 次重试机制。

### 5.2 [tools/backfill_1688_urls.py](../../tools/backfill_1688_urls.py)（248 行）

批量回填脚本，4 级匹配策略，**全部仅限 1688.com URL**：

| Method | 说明 | 匹配逻辑 |
|--------|------|---------|
| A: exact_sku | 本地 SKU 精确匹配店小秘 SKU | `db_sku == paired_sku` |
| B: partial_sku | SKU 子串匹配 | `db_sku in paired_sku` or vice versa |
| C: keyword | 本地产品名关键词 → 店小秘名称 | `_product_keywords(local_name)` 任一 in `paired_name` |
| D: rev_kw | 店小秘名称关键词 → 本地产品名（双向） | `_product_keywords(paired_name)` 任一 in `local_name` |

`_product_keywords()` 函数：
1. 正则去除颜色/尺码后缀
2. 按中英文分隔符拆分
3. 对每个片段生成 2-gram 和 3-gram 子串（用于 CJK 模糊匹配）
4. 按长度降序排列

**关键教训**：CJK 2-gram 模糊匹配容易产生大量误匹配（如"支架""多功能"等通用词匹配到不相关的 Amazon URL），因此所有 Method A-D 都加了 `_is_1688()` 守卫，只认包含 `1688.com` 的 URL。

### 5.3 Web API 路由

**路由**：`GET /medias/api/supply-pairing/search?q=<SKU>&status=0`

- 文件：[web/routes/medias/products.py](../../web/routes/medias/products.py) line 362-376
- 需要 `@login_required` 认证
- 返回：`{"ok": true, "items": [...], "query": str, "search_type_used": str, "total": int}`
- 错误返回：400（缺参数）或 502（店小秘 API 失败）

### 5.4 前端 UI

- **模板**：[web/templates/medias/_roas_form.html](../../web/templates/medias/_roas_form.html) line 23 — "从1688获取"按钮，位于 1688 采购链接输入框右侧
- **JS**：[web/static/roas_form.js](../../web/static/roas_form.js) lines 117-159 — `_fetch1688Url()` 方法：遍历产品的 `dianxiaomi_sku`（优先）和 `shopify_sku`（回退），逐个调用 `/medias/api/supply-pairing/search`，找到第一个有 `sourceUrl` 的结果后自动填入输入框

---

## 6. 回填最终结果

| 指标 | 数值 |
|------|------|
| 店小秘总记录数 | 102（11 已配对 + 91 待配对） |
| 有 1688 URL 的记录 | 15（3 个唯一产品） |
| 本地需回填产品 | ~97 |
| **精确 SKU 匹配成功** | 0（无本地 SKU 匹配到有 1688 URL 的记录） |
| **名称关键词匹配成功** | 4（#400 泡泡机、#427 水枪、#563 太阳能收音机、#571 水枪） |
| **之前已有 1688 URL** | 13 个产品（手动填写） |
| **仍缺 1688 URL** | ~80 个产品 |

---

## 7. 核心结论

1. **店小秘 API 已完全 API 化**：不再需要 UI 交互，直接 `POST` 调用 `alibabaProductPairPageList.json` 即可
2. **"已配对"tab 不会触发额外 API**：页面初始加载的 `status=2` 就是全部已配对数据
3. **~~店小秘中绝大多数产品没有 1688 URL~~**：~~73/102 条记录没有任何 URL，只有 15 条有 1688 URL~~ —— **2026-05-05 更新：此结论错误**，详见第 10 节
4. **本地 SKU 与店小秘 SKU 分属不同体系**：本地 SKU（dianxiaomi_sku / xmyc_storage_skus）和店小秘记录 SKU 几乎不重叠，SKU 精确匹配从未命中 1688 URL —— **2026-05-05 更新：扩大店小秘数据后，95 个本地待回填产品中有 51 个能精确 SKU 命中**

---

## 10. 2026-05-05 增补：alibabaProductId 字段彻底改变结论

**用户反馈**：UI 上"已配对"看到 100+ 而不是 11，触发再次探查（[tools/probe_supply_pairing_v4.py](../../tools/probe_supply_pairing_v4.py)）。

### 真正的事实

之前探索只看 `sourceUrl` 字段，忽略了 `alibabaProductId` 字段：

| status | totalSize | 实际拉到 | 有 alibabaProductId | 有 1688 sourceUrl |
|--------|-----------|----------|---------------------|-------------------|
| status=1 (待配对) | 367 | 337（去重后） | **337（100%）** | 4 |
| status=2 (已配对) | 11 | 11 | 11 | 11 |
| **合计** | **378** | **348** | **348（100%）** | **15** |

`status=1` 全部 337 条都已被店小秘自动匹配到一个 1688 候选供应商，`alibabaProductId` 字段非空，仅 `sourceUrl` 为 null（因为用户尚未在 UI 上点击"确认配对"）。

### 解决方案

1. **`extract_1688_url` 增加 alibabaProductId fallback**：当 `sourceUrl` 不是 1688 链接时，从 `alibabaProductId` 构造 `https://detail.1688.com/offer/{id}.html`
2. **翻页修复**：dxm 翻页 API 在最后一页之后会循环回到某一页（同 ID 重复），改用 ID 去重 + totalSize 双重判停
3. **search_supply_pairing 默认 status="" 拉全部**（覆盖 waiting + paired 共 ~378 条）
4. **backfill 加 URL 唯一性保护**：每个 1688 URL 仅回填给一个本地产品，按 exact_sku > partial_sku > keyword > rev_kw 优先级裁决，去除约 13 个 keyword 阶段产生的扇出误匹

### 实际效果

| 指标 | 旧版（2026-05-04） | 新版（2026-05-05） |
|------|--------------------|--------------------|
| 店小秘可拉记录数 | 102 | 348（去重后） |
| 可识别 1688 URL 数 | 15 (3 个唯一产品) | 348 (348 个唯一产品) |
| 本地 95 个待回填产品 → 命中 | 4 | **70 (73.7%)** |
| 数据库 `media_products` 含 1688 URL 总数 | 17 | **87** |

---

## 8. 给下一个 Agent 的交接指令

> 将以下内容粘贴给下一个 agent 开始工作：

```
## 任务：继续推进 1688 采购链接回填

### 当前状态
- 店小秘 API 已打通，核心模块在 `appcore/supply_pairing.py`
- 批量回填脚本在 `tools/backfill_1688_urls.py`
- 完整探索记录在 `docs/superpowers/plans/2026-05-04-1688-url-backfill-exploration.md`
- 本地约 80 个产品仍缺 `purchase_1688_url`

### 剩余方向

**方向 A：扩大店小秘数据拉取范围**
- 目前只拉 status=1,2。status=1 有 367 条 total，可能后续页也有 1688 URL
- 目前只用 searchType=1（SKU）空搜索，试试 searchType=2（关键词）+ 常见中文词
- 思路：对 status=1 的 367 条全部翻页拉下来，逐一检查有无 1688 URL

**方向 B：1688 站内搜索匹配**
- 用产品中文名去 1688.com 直接搜索，抓取搜索结果第一项的 URL
- 这需要新的 CDP 页面操作（打开 1688 搜索页、解析结果）
- 注意 1688 可能有反爬

**方向 C：人工填写 + Excel 导入**
- 导出缺失 URL 的产品列表，人工在 1688 搜索后填入
- 写一个 CSV/Excel 导入脚本批量更新 `purchase_1688_url`

**方向 D：大模型辅助匹配**
- 用 LLM 对比本地产品名和店小秘记录名，做语义级模糊匹配
- 可能捕获"ARP9电动水枪"↔"全自动水枪 vector"这类人工难以规则化的对应

### 运行环境
- 服务器：172.30.254.14（SSH key: ~/.ssh/CC.pem）
- 项目目录：/opt/autovideosrt
- Python venv：venv/bin/python
- CDP 浏览器：http://127.0.0.1:9222（需店小秘已登录）
- 数据库：MySQL（通过 appcore.db 模块访问，不需要手动连接）

### 关键约束
- 回填的 URL 必须是 1688.com 域名（`purchase_1688_url` 字段语义）
- 所有 CDP 操作需通过 `browser_automation_lock` 序列化
- 不要用 `extract_1688_url` 返回的 Amazon/TikTok URL 填入 1688 字段
- 开发用 git worktree，不要直接在 master 改代码（除非用户说 hotfix）
```

---

## 9. 相关文件清单

| 文件 | 说明 |
|------|------|
| [appcore/supply_pairing.py](../../appcore/supply_pairing.py) | 核心模块：搜索店小秘配対记录 |
| [tools/backfill_1688_urls.py](../../tools/backfill_1688_urls.py) | 批量回填脚本（4 级匹配） |
| [tools/probe_supply_pairing.py](../../tools/probe_supply_pairing.py) | 探针脚本：拦截页面 API 请求 |
| [web/routes/medias/products.py](../../web/routes/medias/products.py#L362) | Web API 路由 `/api/supply-pairing/search` |
| [web/templates/medias/_roas_form.html](../../web/templates/medias/_roas_form.html#L23) | "从1688获取"按钮模板 |
| [web/static/roas_form.js](../../web/static/roas_form.js#L117) | "从1688获取"按钮 JS 逻辑 |
| [tests/characterization/test_medias_routes_baseline.py](../../tests/characterization/test_medias_routes_baseline.py#L84) | 路由基线测试 |
| [appcore/browser_automation_lock.py](../../appcore/browser_automation_lock.py) | CDP 浏览器并发锁 |
