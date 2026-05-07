# 素材管理产品库回填 Shopify ID 设计稿

- 日期：2026-04-24
- 作者：Codex
- 背景：店小秘 Shopify 在线商品库接口 `https://www.dianxiaomi.com/api/shopifyProduct/pageList.json` 已确认能直接返回分页商品数据，其中 `shopifyProductId` 是需要回填到素材库的 Shopify 商品 ID，`handle` 对应我们系统里的 `media_products.product_code`。

## 1. 目标

新增 `media_products.shopifyid` 字段，并提供一个放在 `tools/` 下、可双击执行的一次性同步工具：

1. 启动专用 Chrome 目录 `C:\chrome-shopifyid-diaoxiaomi`
2. 打开 `https://www.dianxiaomi.com/web/shopifyProduct/online`
3. 用户如未登录则手动登录
4. 工具直接调用店小秘接口分页抓取全部在线 Shopify 商品
5. 仅当 `handle == media_products.product_code` 完全一致时，回填 `media_products.shopifyid = shopifyProductId`
6. 未匹配项跳过；冲突项不覆盖，写入日志

## 2. 已确认接口事实

根据用户提供的真实响应样本：

- 请求地址：`POST https://www.dianxiaomi.com/api/shopifyProduct/pageList.json`
- 查询条件：`listingStatus=Active`、`dxmState=online`、`pageSize=100`
- 分页字段位于 `data.page`
- 总量字段不是 `total`，而是：
  - `data.page.totalSize`
  - `data.page.totalPage`
  - `data.page.pageNo`
  - `data.page.pageSize`
- 商品数组位于 `data.page.list`
- 单条商品关键信息：
  - `handle`
  - `shopifyProductId`
  - `title`
  - `shopId`

当前样本中：

- `totalSize = 404`
- `totalPage = 5`
- `pageSize = 100`

## 3. 数据模型

### 3.1 数据库字段

在 `media_products` 增加：

- 字段名：`shopifyid`
- 类型：`VARCHAR(32) NULL`

设计理由：

- Shopify 商品 ID 虽然当前表现为纯数字，但本质是外部平台标识，按字符串保存更稳妥
- 现有 `dianxiaomi_rankings.product_id` 也是字符串语义
- 本次需求只要求“回填并持久化”，不要求数值运算

### 3.2 唯一性策略

本次不对 `media_products.shopifyid` 加唯一索引。

原因：

- 需求核心匹配主键是 `product_code`
- 真实历史数据可能存在重复、脏数据或平台侧异常，先允许存储，避免迁移时被卡死
- 工具层会在写入前做冲突检查和日志输出，优先保证同步过程可执行

## 4. 工具方案

### 4.1 放置位置

- `tools/shopifyid_dianxiaomi_sync.py`
- `tools/shopifyid_dianxiaomi_sync.bat`

### 4.2 执行方式

用户双击 `.bat` 后：

1. 进入仓库根目录
2. 调用 Python 脚本
3. 脚本拉起带专用 profile 的浏览器
4. 在同一浏览器上下文内直接请求店小秘接口
5. 完成抓取、匹配、回填和日志输出

### 4.3 浏览器与登录态策略

不采用“复制 Chrome Cookies DB 再脱机请求”的方案。

原因：

- `C:\chrome-shopifyid-diaoxiaomi\Default\Network\Cookies` 在浏览器打开时会被强锁，实测会触发 `WinError 32`
- 这种方式对运行时状态依赖强，容易失败

改为：

- 使用 Playwright 启动可视化持久化浏览器上下文，直接绑定 `C:\chrome-shopifyid-diaoxiaomi`
- 页面和接口请求都在同一浏览器上下文中完成
- 登录态天然由浏览器上下文持有，不需要单独解析或复制 Cookies

## 5. 匹配与回填规则

### 5.1 精确匹配

店小秘商品：

- `handle`

素材库商品：

- `media_products.product_code`

只有当二者完全一致时，才允许更新：

```text
media_products.shopifyid = shopifyProductId
```

### 5.2 跳过条件

以下情况直接跳过，不回填：

1. `handle` 为空
2. `shopifyProductId` 为空
3. 本地不存在同名 `product_code`
4. 店小秘中同一个 `handle` 出现多个不同 `shopifyProductId`
5. 本地已有 `shopifyid`，且与新值不一致

### 5.3 已有值策略

- 本地 `shopifyid` 为空：允许写入
- 本地 `shopifyid` 与店小秘值相同：记为已一致，跳过更新
- 本地 `shopifyid` 与店小秘值不同：记为冲突，输出日志，不自动覆盖

## 6. 日志与结果输出

工具执行后输出：

- 店小秘在线商品总数
- 实际抓取页数
- 抓取商品数
- 命中 `product_code` 数
- 新回填数
- 已一致数
- 未匹配数
- 冲突数
- 错误数

同时在本地生成 JSON 日志文件，建议放在：

- `output/shopifyid_dianxiaomi_sync/`

日志至少包含：

- `handle`
- `shopifyProductId`
- `media_product_id`
- `product_code`
- `status`
- `message`

## 7. 后端代码改动范围

本次不新增后台页面入口，不修改前端 UI。

需要改动的仅是：

- DB migration
- `appcore/medias.py` 中产品字段的更新/查询辅助
- 可能的测试文件
- `tools/` 独立工具

## 8. 非目标

- 不新增后台按钮
- 不新增定时任务
- 不把店小秘商品全量导入业务表
- 不做 DOM 表格爬虫
- 不处理非在线商品
- 不基于标题模糊匹配

## 9. 风险

### 9.1 店小秘接口参数变动

缓解：

- 将 payload 常量集中在工具脚本顶部
- 日志记录原始响应摘要与 HTTP 状态

### 9.2 专用 profile 被占用

缓解：

- 工具启动时优先复用自身拉起的浏览器上下文
- 对“profile 已被其他 Chrome 进程占用”给出明确错误提示

### 9.3 数据冲突

缓解：

- 冲突不自动覆盖
- 明确落日志，后续人工复核

## 10. 文件清单

- `db/migrations/2026_04_24_media_products_shopifyid.sql`
- `appcore/medias.py`
- `tests/test_appcore_medias.py`
- `tools/shopifyid_dianxiaomi_sync.py`
- `tools/shopifyid_dianxiaomi_sync.bat`
- `tests/test_shopifyid_dianxiaomi_sync.py`

## 11. 验证策略

1. 单元测试：
   - 回填匹配规则
   - 冲突/跳过规则
   - 分页汇总逻辑
2. 语法检查：
   - Python 文件可导入
3. 真实运行验证：
   - 用 `C:\chrome-shopifyid-diaoxiaomi` 登录态跑一次
   - 确认输出 `404 / 5 页`
   - 抽查若干条 `media_products.shopifyid` 已写入
