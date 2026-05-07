# Shopify ID Dianxiaomi Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `media_products` 增加 `shopifyid` 字段，并提供一个 `tools/` 下可双击执行的一次性店小秘同步工具，按 `handle == product_code` 精确回填 Shopify 商品 ID。

**Architecture:** 数据库存储层只新增 `shopifyid` 字段与最小 DAO 支撑；同步逻辑全部收敛在独立 `tools/shopifyid_dianxiaomi_sync.py` 中，通过 Playwright 持久化浏览器上下文复用 `C:\chrome-shopifyid-diaoxiaomi` 的登录态，直接请求店小秘 `pageList.json` 接口分页抓取并回填。冲突与未匹配项只记日志，不自动覆盖。

**Tech Stack:** Python 3.14, Playwright, Flask 项目现有 `appcore.db` / `appcore.medias`, pytest

---

### Task 1: 数据模型与 DAO

**Files:**
- Create: `db/migrations/2026_04_24_media_products_shopifyid.sql`
- Modify: `appcore/medias.py`
- Test: `tests/test_appcore_medias.py`

- [ ] **Step 1: 写 `shopifyid` 迁移测试用例**

在 `tests/test_appcore_medias.py` 增加覆盖：
- `update_product(..., shopifyid="8560559554733")` 会成功
- 空串会归一化为 `None`
- 非数字字符串会抛 `ValueError`

- [ ] **Step 2: 运行目标测试，确认红灯**

Run: `pytest tests/test_appcore_medias.py -q`
Expected: 新增的 `shopifyid` 用例失败，因为 DAO 还不支持该字段

- [ ] **Step 3: 增加 migration 与 DAO 最小实现**

实现内容：
- 新建 `db/migrations/2026_04_24_media_products_shopifyid.sql`
- 在 `appcore/medias.py` 的 `update_product` 白名单中加入 `shopifyid`
- 归一化规则：
  - `None` / 空串 / 全空白 -> `None`
  - 非空必须是纯数字字符串
  - 落库时保留为字符串

- [ ] **Step 4: 再跑目标测试，确认绿灯**

Run: `pytest tests/test_appcore_medias.py -q`
Expected: `shopifyid` 相关测试通过

- [ ] **Step 5: 提交本任务**

```bash
git add db/migrations/2026_04_24_media_products_shopifyid.sql appcore/medias.py tests/test_appcore_medias.py
git commit -m "feat(medias): support shopifyid field on media products"
```

### Task 2: 提炼同步工具核心纯函数

**Files:**
- Create: `tools/shopifyid_dianxiaomi_sync.py`
- Test: `tests/test_shopifyid_dianxiaomi_sync.py`

- [ ] **Step 1: 先写纯函数测试**

在 `tests/test_shopifyid_dianxiaomi_sync.py` 增加覆盖：
- 计算分页数：`404 / 100 -> 5`
- 从接口响应提取商品记录
- 根据 `handle == product_code` 做精确匹配
- 已有相同 `shopifyid` 记为 `unchanged`
- 已有不同 `shopifyid` 记为 `conflict`
- 本地不存在产品时记为 `unmatched`
- 店小秘重复 `handle` 且 ID 不同记为 `remote_conflict`

- [ ] **Step 2: 运行测试确认红灯**

Run: `pytest tests/test_shopifyid_dianxiaomi_sync.py -q`
Expected: 失败，因为工具模块与函数尚未存在

- [ ] **Step 3: 用最小实现让测试通过**

在 `tools/shopifyid_dianxiaomi_sync.py` 实现纯函数：
- `build_payload(page_no: int) -> dict[str, object]`
- `extract_page_summary(payload: dict) -> tuple[int, int, int]`
- `extract_products(payload: dict) -> list[dict]`
- `build_remote_handle_map(rows: list[dict]) -> tuple[dict[str, str], list[dict]]`
- `plan_backfill_updates(remote_map, local_products) -> dict`

要求：
- 不在纯函数里做 IO
- 输出结构能直接供 CLI 主流程使用

- [ ] **Step 4: 再跑测试确认绿灯**

Run: `pytest tests/test_shopifyid_dianxiaomi_sync.py -q`
Expected: 新测试全部通过

- [ ] **Step 5: 提交本任务**

```bash
git add tools/shopifyid_dianxiaomi_sync.py tests/test_shopifyid_dianxiaomi_sync.py
git commit -m "feat(tools): add shopifyid dianxiaomi sync core logic"
```

### Task 3: 接入浏览器上下文抓店小秘接口

**Files:**
- Modify: `tools/shopifyid_dianxiaomi_sync.py`
- Test: `tests/test_shopifyid_dianxiaomi_sync.py`

- [ ] **Step 1: 先补浏览器与分页抓取的测试桩**

在 `tests/test_shopifyid_dianxiaomi_sync.py` 新增：
- mock Playwright context / page
- 验证脚本会访问 `https://www.dianxiaomi.com/web/shopifyProduct/online`
- 验证第一页拿到 `totalSize=404, totalPage=5`
- 验证后续页按 `pageNo=2..5` 继续抓

- [ ] **Step 2: 运行测试确认红灯**

Run: `pytest tests/test_shopifyid_dianxiaomi_sync.py -q`
Expected: 新增浏览器抓取测试失败

- [ ] **Step 3: 实现浏览器抓取与用户登录等待**

实现内容：
- 常量：
  - `CHROME_USER_DATA_DIR = Path(r"C:\chrome-shopifyid-diaoxiaomi")`
  - `ONLINE_URL = "https://www.dianxiaomi.com/web/shopifyProduct/online"`
  - `API_URL = "https://www.dianxiaomi.com/api/shopifyProduct/pageList.json"`
- Playwright 持久化浏览器上下文
- 首次进入页面后提示用户登录并按回车继续
- 用页面内 `fetch` 或 `context.request` 在同一登录态下 POST `pageList.json`
- 根据 `totalPage` 拉完全部分页

- [ ] **Step 4: 再跑测试确认绿灯**

Run: `pytest tests/test_shopifyid_dianxiaomi_sync.py -q`
Expected: 抓取流程测试通过

- [ ] **Step 5: 提交本任务**

```bash
git add tools/shopifyid_dianxiaomi_sync.py tests/test_shopifyid_dianxiaomi_sync.py
git commit -m "feat(tools): fetch dianxiaomi online shopify products via browser session"
```

### Task 4: 回填数据库与生成结果日志

**Files:**
- Modify: `tools/shopifyid_dianxiaomi_sync.py`
- Test: `tests/test_shopifyid_dianxiaomi_sync.py`

- [ ] **Step 1: 先补数据库写入与日志测试**

新增测试覆盖：
- 工具只更新命中的 `media_products`
- 冲突项不覆盖
- 输出统计汇总
- 结果 JSON 写入 `output/shopifyid_dianxiaomi_sync/`

- [ ] **Step 2: 运行测试确认红灯**

Run: `pytest tests/test_shopifyid_dianxiaomi_sync.py -q`
Expected: 日志和写库相关测试失败

- [ ] **Step 3: 实现最小写库主流程**

在脚本中补充：
- 读取本地产品：
  - `SELECT id, product_code, shopifyid FROM media_products WHERE deleted_at IS NULL`
- 按 Task 2 的回填计划执行更新
- 更新 SQL：
  - `UPDATE media_products SET shopifyid=%s WHERE id=%s`
- 生成结果 JSON 文件
- stdout 打印：
  - `totalSize`
  - `totalPage`
  - `fetched`
  - `matched`
  - `updated`
  - `unchanged`
  - `unmatched`
  - `conflict`
  - `remote_conflict`

- [ ] **Step 4: 再跑测试确认绿灯**

Run: `pytest tests/test_shopifyid_dianxiaomi_sync.py -q`
Expected: 数据库与日志测试通过

- [ ] **Step 5: 提交本任务**

```bash
git add tools/shopifyid_dianxiaomi_sync.py tests/test_shopifyid_dianxiaomi_sync.py
git commit -m "feat(tools): backfill media_products.shopifyid from dianxiaomi data"
```

### Task 5: 增加双击启动脚本

**Files:**
- Create: `tools/shopifyid_dianxiaomi_sync.bat`

- [ ] **Step 1: 写 bat 启动脚本**

文件内容：

```bat
@echo off
cd /d %~dp0\..
python tools\shopifyid_dianxiaomi_sync.py %*
pause
```

- [ ] **Step 2: 运行静态检查**

Run: `type tools\\shopifyid_dianxiaomi_sync.bat`
Expected: 输出脚本内容

- [ ] **Step 3: 提交本任务**

```bash
git add tools/shopifyid_dianxiaomi_sync.bat
git commit -m "feat(tools): add double-click launcher for shopifyid sync"
```

### Task 6: 最终验证

**Files:**
- Verify: `tests/test_appcore_medias.py`
- Verify: `tests/test_shopifyid_dianxiaomi_sync.py`
- Verify: `tools/shopifyid_dianxiaomi_sync.py`

- [ ] **Step 1: 运行 Python 语法检查**

Run:
`python -m py_compile appcore/medias.py tools/shopifyid_dianxiaomi_sync.py tests/test_shopifyid_dianxiaomi_sync.py`

Expected: 无输出，退出码 0

- [ ] **Step 2: 运行目标测试**

Run:
`pytest tests/test_appcore_medias.py tests/test_shopifyid_dianxiaomi_sync.py -q`

Expected: 目标测试全部通过

- [ ] **Step 3: 真实跑一次工具**

Run:
`python tools/shopifyid_dianxiaomi_sync.py`

Expected:
- 打开或复用 `C:\chrome-shopifyid-diaoxiaomi`
- 输出 `totalSize=404`
- 输出 `totalPage=5`
- 生成一份结果日志

- [ ] **Step 4: 抽样验证数据库**

手工检查：
- 抽查 3-5 个命中商品，确认 `media_products.shopifyid` 已写入
- 抽查冲突/未匹配项，确认未被误覆盖

- [ ] **Step 5: 提交最终整合**

```bash
git add appcore/medias.py db/migrations/2026_04_24_media_products_shopifyid.sql tools/shopifyid_dianxiaomi_sync.py tools/shopifyid_dianxiaomi_sync.bat tests/test_appcore_medias.py tests/test_shopifyid_dianxiaomi_sync.py docs/superpowers/specs/2026-04-24-shopifyid-dianxiaomi-sync-design.md docs/superpowers/plans/2026-04-24-shopifyid-dianxiaomi-sync.md
git commit -m "feat(tools): sync shopifyid from dianxiaomi online products"
```
