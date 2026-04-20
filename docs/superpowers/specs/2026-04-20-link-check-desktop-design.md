# 链接检查桌面子项目设计

日期：2026-04-20

## 背景

当前仓库里的链接检查能力已经具备以下核心链路：

1. 抓取 Shopify 商品页图片
2. 将页面图片与参考图做相似度匹配
3. 对已匹配图片执行二值快检
4. 对图片执行同图 LLM 判断
5. 对图片执行语言与文案质量分析
6. 汇总为 `pass / review / replace` 结论

但现有实现主要服务于 Web 页面任务，页面抓取以 `requests.Session` 为主。在实际店铺中，部分链接存在首访重定向、地区锁定和会话状态机，导致“请求式抓页”和“真实浏览器访问”的结果不一致。用户希望新增一个独立的 Windows 桌面子项目，以可见浏览器真实访问目标页，完成目标页锁定、图片抓取、本地落盘、参考图拉取、图片比对和大模型分析，并可打包为 `exe`。

本次目标不是重做一套新的链接检查算法，而是做一个最小闭环的桌面工具，在浏览器抓页侧补齐真实访问能力，同时尽量复用现有判断逻辑。

## 目标

本次设计目标如下：

1. 新增一个 Windows Python 桌面子项目，可打包为 `exe`
2. 桌面程序以弹窗方式输入目标页 URL
3. 通过服务端专用接口，将目标 URL 映射为内部 `media_products.id`
4. 自动识别目标语种
5. 从服务端素材库拉取该产品该语种的参考图
6. 启动可见 Edge 浏览器访问目标页
7. 若首访发生重定向或未锁定目标语种，则在同一浏览器上下文中再次访问原始目标 URL 进行二次锁定
8. 仅在页面锁定成功后才抓取和下载页面图片
9. 使用与现有模块一致的图片比对、二值快检、同图判断和语言分析逻辑
10. 将本次任务全部落盘到 `exe` 同级目录下的 `img/<product_id>-<YYYYMMDDHHMMSS>/`
11. 先跑通最小闭环，不做复杂任务管理或服务端结果回传

## 非目标

本次明确不做：

1. 不做多任务队列
2. 不做任务历史列表
3. 不做桌面任务结果回传服务端
4. 不做服务端 `projects` 持久化接入
5. 不做复杂登录体系
6. 不做 API Key 加密、配置中心或密钥托管，首版按用户要求明文写入代码
7. 不做内嵌浏览器
8. 不做自动更新
9. 不做结果缩略图墙或完整报告 UI

## 方案对比

### 方案 1：`Tkinter + Playwright(可见 Edge) + 专用 OpenAPI`

桌面端使用 `Tkinter` 构建最小弹窗界面，使用 `Playwright` 调起本机已安装的 Edge，可见窗口运行。服务端新增专用 bootstrap 接口，负责把目标 URL 映射为 `media_products.id`、目标语种和参考图清单。桌面端只负责本地下载参考图、浏览器锁页、抓图、比对、LLM 分析和结果落盘。

优点：

1. 最贴合“Windows Python 程序 + 可编译 exe + 可见浏览器 + 最小闭环”目标
2. 客户端逻辑清晰，服务端改动最小且集中
3. 打包风险低于 `PySide6 + Qt WebEngine`

缺点：

1. 首版 UI 较朴素
2. 依赖目标机器安装可用 Edge

### 方案 2：`PySide6 + Qt WebEngine + 专用 OpenAPI`

桌面端使用更完整的原生 GUI，并内嵌 WebEngine 作为浏览器。

优点：

1. 长期形态更完整
2. 浏览器和界面都在一个窗口中

缺点：

1. 最小闭环成本高
2. 打包复杂度高
3. 首版排错成本高

### 方案 3：客户端拼接现有多个 OpenAPI，本地自行做产品映射

桌面程序不新增专用接口，而是拼现有 `/openapi/materials` 与其他接口，本地自己完成 URL 到产品 ID 的映射。

优点：

1. 表面上服务端改动少

缺点：

1. 客户端复杂度高
2. URL 到产品的匹配规则分散
3. 容易造成桌面端与服务端映射逻辑漂移

结论：采用方案 1。

## 总体设计

整体链路如下：

```text
输入目标 URL
  -> 调服务端 bootstrap 接口
  -> 返回 product_id / target_language / reference_images
  -> 创建本地任务目录 img/<product_id>-<timestamp>/
  -> 下载参考图到 reference/
  -> 启动可见 Edge
  -> 第一次打开 target_url
  -> 若被重定向或未锁定目标语种，则在同一 browser context 中再次打开原始 target_url
  -> 页面锁定成功后提取页面图片 URL
  -> 下载页面图片到 site/
  -> 执行参考图匹配 / 二值快检 / 同图判断 / 语言分析
  -> 写出 task.json / page_info.json / result.json
  -> 在弹窗界面显示简要结论
```

## 一、服务端专用 bootstrap 接口

新增接口：

```text
POST /openapi/link-check/bootstrap
```

认证方式沿用现有 OpenAPI：

```text
X-API-Key: <固定明文 key>
```

### 请求体

```json
{
  "target_url": "https://newjoyloo.com/de/products/sonic-lens-refresher-rjc?variant=123"
}
```

### 服务端处理顺序

1. 标准化 URL，保留 `variant` 等查询参数，去除 fragment
2. 通过现有 `appcore.link_check_locale.detect_target_language_from_url()` 识别目标语种
3. 将 URL 映射为内部 `media_products.id`
4. 拉取该产品、该语种的参考图列表
5. 若未识别出语种、未匹配到产品或没有参考图，则直接返回错误，不让客户端继续运行

### 产品 ID 映射优先级

产品 ID 指内部 `media_products.id`，不是 Shopify 原始 product id。

服务端匹配优先级如下：

1. 优先匹配 `media_products.localized_links_json` 中该语种配置的完整链接
2. 再匹配忽略 query 后的链接
3. 再从 URL 的 `/products/<handle>` 中提取 handle
4. 若 handle 带 `-rjc` 后缀，则先去掉后缀再尝试匹配
5. 用处理后的 handle 匹配 `media_products.product_code`

不做模糊猜测和近似匹配。匹配不上就返回失败。

### 参考图来源

只返回目标语种的参考图：

1. `media_product_covers` 中该语种的主图
2. `media_product_detail_images` 中该语种的详情图

不在第一版中自动回退到英文参考图，以避免“德语页面拿英文图做基准”导致判定偏差。

### 成功响应

```json
{
  "product": {
    "id": 12345,
    "product_code": "sonic-lens-refresher",
    "name": "Sonic Lens Refresher"
  },
  "target_language": "de",
  "target_language_name": "德语",
  "matched_by": "localized_links_exact",
  "normalized_url": "https://newjoyloo.com/de/products/sonic-lens-refresher-rjc?variant=123",
  "reference_images": [
    {
      "id": "cover-de",
      "kind": "cover",
      "filename": "cover_de.jpg",
      "download_url": "https://signed.example.com/...",
      "expires_in": 3600
    },
    {
      "id": "detail-1001",
      "kind": "detail",
      "filename": "detail_01.jpg",
      "download_url": "https://signed.example.com/...",
      "expires_in": 3600
    }
  ]
}
```

### 失败响应

第一版仅定义以下几类：

1. `400 invalid target_url`
2. `404 product not found`
3. `409 language not detected`
4. `409 references not ready`

## 二、桌面端浏览器锁页流程

### 技术选型

1. 桌面程序：`Tkinter`
2. 浏览器自动化：`Playwright`
3. 浏览器通道：优先使用本机 `Microsoft Edge`
4. 运行模式：可见窗口，不使用 headless
5. 打包工具：`PyInstaller`

### 为什么使用可见 Edge

本次最小闭环的难点在于部分站点存在真实用户态的首访状态机。使用真实浏览器可比纯 `requests.Session` 更接近实际用户访问路径，也更符合用户“通过浏览器访问页面”的要求。

### 锁页规则

桌面端必须在同一浏览器上下文中执行如下顺序：

1. 第一次 `page.goto(target_url)`
2. 记录第一次最终 URL 与页面 `html lang`
3. 若已满足目标语种，则锁页成功
4. 若未满足，则在同一 `browser context` 中再次 `page.goto(target_url)`
5. 再次记录最终 URL 与 `html lang`
6. 若第二次仍未锁定到目标语种，则本次任务失败，不进入图片下载

页面锁定成功的判定条件：

1. `document.documentElement.lang` 命中目标语种，或
2. 最终 URL 路径明确落在目标语种前缀，且页面语言检查不冲突

### 页面级记录

锁页后写出 `page_info.json`，至少包含：

```json
{
  "requested_url": "...",
  "first_final_url": "...",
  "second_final_url": "...",
  "final_url": "...",
  "html_lang": "de",
  "locked": true,
  "image_urls": ["..."]
}
```

并将最终锁定页的 HTML 保存到 `page.html`。

## 三、页面图片提取与下载

### 图片提取

桌面端不重新设计一套图片识别规则，而是尽量复用现有 `appcore.link_check_fetcher` 中已经验证过的提取思路。提取优先级如下：

1. 变体主图
2. 轮播图
3. 详情图

### 图片下载

图片 URL 提取后，下载到本地任务目录下的 `site/` 子目录。页面图片下载完成前，参考图已先下载到 `reference/` 子目录。

目录结构如下：

```text
LinkCheckDesktop.exe
img/
  12345-20260420230518/
    task.json
    page.html
    page_info.json
    reference/
      cover_de_01.jpg
      detail_de_01.jpg
    site/
      site_001.jpg
      site_002.jpg
    compare/
      result.json
```

## 四、本地落盘规则

程序根目录定义为 `exe` 所在目录。每次新任务都在同级创建：

```text
img/<product_id>-<YYYYMMDDHHMMSS>/
```

示例：

```text
img/12345-20260420230518/
```

### 落盘文件职责

1. `task.json`
   - 输入 URL
   - 识别结果
   - 本地目录路径
   - 执行时间戳

2. `page.html`
   - 最终锁定页源码

3. `page_info.json`
   - 页面锁定过程与图片 URL 清单

4. `reference/`
   - 服务端素材库参考图

5. `site/`
   - 目标页抓取的页面图片

6. `compare/result.json`
   - 单图结果和任务汇总结果

### 写权限策略

第一版不做目录切换兜底。如果 `exe` 所在目录不可写，程序直接提示：

```text
当前目录不可写，请将 exe 放到可写目录后再运行
```

## 五、判断链路复用策略

桌面端必须严格沿用当前链接检查模块的判定顺序：

```text
页面图
  -> 最佳参考图匹配
  -> 二值快检
  -> 同图 LLM 判断
  -> 语言与文案 LLM 分析
  -> 汇总结论
```

### 直接复用的现有模块

1. `appcore.link_check_compare`
   - `find_best_reference()`
   - `run_binary_quick_check()`

2. `appcore.link_check_locale`
   - 目标语种识别与展示辅助

3. `appcore.link_check_runtime`
   - 汇总思路与最终计数规则

4. 现有同图判断和图片分析逻辑

### 单图判定顺序

1. 调用 `find_best_reference(site_image, reference_images)`
2. 若 `reference_match.status == matched`
   - 调用 `run_binary_quick_check()`
   - 若二值结果为 `pass`，直接判：
     - `decision = pass`
     - `decision_source = binary_quick_check`
   - 若二值结果为 `fail`，直接判：
     - `decision = replace`
     - `decision_source = binary_quick_check`
3. 若未被二值直接分流
   - 执行同图 LLM 判断，记录结果
   - 再执行语言与文案 LLM 判断，给出 `pass / review / replace`

### LLM 调用约束

新代码一律遵守仓库约定，走 `appcore.llm_client`，不新增直接 `OpenAI()` 或旧式 `appcore.gemini` 直调。

因此本次需要对桌面端涉及到的图片分析入口做轻量适配：

1. `link_check.analyze` 改为通过 `llm_client.invoke_generate(...)` 执行
2. 若同图判断需要新增 use case，则新增如 `link_check.same_image`

目标是保证：

1. 桌面端结论与现有模块一致
2. 新增代码符合仓库 LLM 统一调用规范

## 六、结果结构

桌面端最终输出的 `compare/result.json` 尽量贴近当前任务结构：

```json
{
  "product_id": 12345,
  "target_language": "de",
  "target_language_name": "德语",
  "summary": {
    "pass_count": 3,
    "replace_count": 1,
    "review_count": 1,
    "reference_matched_count": 4,
    "binary_checked_count": 4,
    "same_image_llm_done_count": 4,
    "overall_decision": "unfinished"
  },
  "items": [
    {
      "id": "site-001",
      "kind": "carousel",
      "source_url": "...",
      "local_path": "site/site_001.jpg",
      "reference_match": {},
      "binary_quick_check": {},
      "same_image_llm": {},
      "analysis": {},
      "status": "done",
      "error": ""
    }
  ]
}
```

## 七、桌面子项目结构

在仓库中新增独立子项目目录：

```text
link_check_desktop/
  README.md
  requirements.txt
  main.py
  gui.py
  controller.py
  browser_worker.py
  bootstrap_api.py
  storage.py
  result_schema.py
  packaging/
    link_check_desktop.spec
```

### 文件职责

1. `main.py`
   - 程序入口

2. `gui.py`
   - `Tkinter` 弹窗界面

3. `controller.py`
   - 串联完整任务流程

4. `browser_worker.py`
   - Playwright 浏览器锁页与抓图

5. `bootstrap_api.py`
   - 调用服务端 bootstrap 接口

6. `storage.py`
   - 管理 `img/<product_id>-<timestamp>/` 目录与落盘

7. `result_schema.py`
   - 规范 `task.json`、`page_info.json`、`result.json` 结构

8. `packaging/link_check_desktop.spec`
   - `PyInstaller` 打包配置

## 八、服务端改动边界

服务端本次只做两类改动：

1. 在现有 Flask OpenAPI 中新增 `POST /openapi/link-check/bootstrap`
2. 在 `appcore.medias` 与相关路由中补齐桌面端所需的产品匹配与参考图序列化能力

第一版不做：

1. 桌面任务回写 `projects`
2. 桌面任务结果上传
3. 新的网页管理页
4. 新的权限体系

## 九、打包方案

打包工具使用 `PyInstaller`，优先输出单目录版而不是单文件版。

原因：

1. `Playwright` 及其浏览器依赖更适合单目录打包
2. `Pillow`、`scikit-image` 等图像依赖的打包风险更低
3. 最小闭环优先稳定运行，不追求首版极致分发形式

打包产物示例：

```text
dist/
  LinkCheckDesktop/
    LinkCheckDesktop.exe
    _internal/...
```

运行时以 `LinkCheckDesktop.exe` 所在目录为根目录，并在同级生成 `img/`。

## 十、测试策略

### 1. 服务端单元测试

新增或修改以下能力的测试：

1. 给定目标 URL 能解析到 `media_products.id`
2. 能正确识别语种
3. 能返回该语种参考图列表
4. 未匹配到产品时返回 `404`
5. 未识别出语种或参考图缺失时返回 `409`

### 2. 桌面端核心流程测试

新增桌面子项目的单元测试，重点覆盖：

1. 本地目录命名是否正确
2. bootstrap 响应转任务对象是否正确
3. `result.json` 结构是否正确
4. controller 各阶段失败是否正确落盘并提示

浏览器和网络请求在单测中尽量 mock，不在单测里真实起浏览器。

### 3. 手工验收

至少手工验证以下场景：

1. 正常 URL，首访即锁定成功
2. 首访被重定向，二次访问后锁定成功
3. URL 能匹配到产品，但该语种没有参考图
4. URL 匹配不到任何产品
5. 可见 Edge 正常启动
6. `exe` 运行后能在同级生成 `img/<product_id>-<timestamp>/`

## 十一、验收标准

满足以下条件即视为 MVP 跑通：

1. 能在 Windows 上启动桌面程序
2. 输入目标页 URL 后能调用 bootstrap 接口
3. 能返回 `media_products.id`、目标语种和参考图列表
4. 能启动可见 Edge，并完成首访和必要的二次访问
5. 页面锁定成功后能抓取并下载页面图片
6. 能下载服务端参考图
7. 能完成参考图匹配、二值快检、同图判断和语言分析
8. 能生成 `task.json`、`page_info.json`、`result.json`
9. 界面上能展示产品 ID、语种、抓图数量和最终摘要
10. 能成功打包为可运行的 Windows `exe`

## 十二、风险与边界

### 1. 产品映射风险

若店铺实际 URL 与 `localized_links_json`、`product_code` 不一致，则 bootstrap 映射会失败。第一版不做模糊匹配，直接报错。

### 2. 浏览器依赖风险

若目标机器未安装可用 Edge，或 Playwright 通道不可用，则桌面程序直接提示依赖缺失。

### 3. 同目录写权限风险

若用户把 `exe` 放在不可写目录，如 `Program Files`，程序会直接失败并提示移动到可写目录。

### 4. 结果不回传风险

第一版结果仅保存在本地目录，不进入服务端项目体系。优点是最小闭环快，缺点是服务端看不到桌面端执行结果。这一风险在本次范围内接受。

## 十三、实现边界

本次实现全部在隔离 worktree 中进行：

```text
.worktrees/link-check-desktop
```

改动范围包含：

1. 服务端：
   - `web/routes/openapi_materials.py`
   - `appcore/medias.py`
   - 对应测试

2. 桌面子项目：
   - `link_check_desktop/` 目录
   - 打包与运行说明

