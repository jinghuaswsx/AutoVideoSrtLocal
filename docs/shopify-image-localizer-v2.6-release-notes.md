# Shopify Image Localizer v2.6 Release Notes

发布日期：2026-04-30
分支：`feature/shopify-localizer-streaming-progress`
基线：master `b1b782e merge: adjust mk copywriting UI`，rebase 至包含 v2.5 hotfix

> 本次工作横跨多轮迭代（中途本地构建过 v2.1/2.2/2.3/2.4 用于内部测试，**均未走完整发布流程**）。这次是首次合入 master 并完整发布的版本，以避免别的会话无法拿到这些改动。

## 一句话概括

把"运行摘要"改成左右双栏（左侧实时步骤+计时，右侧原摘要）；补全所有耗时阶段（视觉识别、文件名匹配、Chrome 启动等）的中文进度日志；浏览器在每一阶段自动停留到对应业务页（EZ / TAA / Storefront / google.com）；任务结束/失败/取消增加醒目弹窗；UI 多处文案与布局微调。

## 用户可见的变化

### GUI 布局
- 窗口启动时尺寸为屏幕宽 × 80%、高 × 80%，水平 / 垂直居中显示。
- 顶部红色登录提示文案缩短为「第一次用或者店铺登录掉线，先点左侧按钮」，字体放大到 27pt 加粗，**单行展示，不够时末尾用省略号 `…` 截断**（绑定 `<Configure>` 事件 + `tkinter.font.Font.measure` 二分查找）。
- 原"运行摘要"区域拆为左右两栏：
  - 左：**实时进度** Treeview（时间 / 步骤 / 耗时 三列），最后一行的耗时格每秒刷新；上方一行小字 `当前：xxx · 总耗时 mm:ss`。
  - 右：保留原"运行摘要"（项目 / 结果 两列），任务结束时填充。
- 下方"实时日志" Text 控件保留不动。

### 任务结束反馈（解决「不知道是不是结束了」的问题）
- 三种结束路径都加显眼日志 + 居中弹窗：
  - 成功：日志 `================ 任务已结束（执行完成）— 详情请看运行摘要 ================` + 摘要新增「任务状态=已完成」行 + `messagebox.showinfo("任务结束", ...)`。
  - 用户取消：同上模式，弹「任务已停止」。
  - 异常失败：同上模式，弹窗带原因 `messagebox.showerror`。

### 进度日志（解决「卡住了还是在跑？」的问题）
- 修复 `download_localized` 错位 print：之前在下载**之前**就声称"已下载 N 张"。改为下载前 print「开始下载 N 张...」，下载后 print「本地化图片下载完成，共 N 张」。
- 在 `pair_carousel_images`（确定性配对算法）前后补「轮播图：正在按文件名/哈希比对位置…」/「轮播图：文件名匹配完成，共 N 对」。
- 视觉兜底每个阶段都有日志：
  - `download_visual_*` 前：「开始下载 K 个位置的参考图」
  - `build_visual_*_pair_plan` 前：**「视觉识别中——正在用图像比对算法生成配对方案（耗时较长，请耐心等待）」**
  - 视觉识别完：「得到 N 对候选；等待用户确认配对」
  - 等用户确认完：「视觉兜底已确认 N 对」
- `ez_cdp.py` 内 30+ 处 `_run_step` / `_log` 全部中文化：
  - `_run_step` 模板 `START / END / failed` → `开始 / 完成 / 失败`
  - `[carousel]` → `[轮播图]`、`[slot N]` → `[位置 N]`
  - `open EZ page` → `打开 EZ 页面`、`wait EZ iframe and image buttons` → `等待 EZ iframe 与图片按钮` 等
  - 保留专名：EZ / Translate and Adapt / CDP / Chrome / iframe / Shopify / token / URL

### 浏览器停留页面（解决「在干什么就该停在那个页面」的问题）
- 启动 Chrome 时初始 tab 改为 `https://www.google.com`（之前会用业务 URL，导致打开两个一模一样的 EZ 页 + 触发频率限制）。
- `ez_cdp.ensure_cdp_chrome` 新增 `STARTUP_URL` 模块常量并作为 `initial_url` 默认值，三处调用点不再传业务 URL。
- 新增 `_preload_chrome_tab_to_url` helper：连接现有 Chrome → 复用首个 tab → bring_to_front → goto 目标 URL，**失败不阻断主流程**。
- 在轮播图段开头预热 Chrome → EZ 页（让用户视觉看到接下来要操作 EZ）。
- 在详情图段（fetch_storefront 之后、视觉识别之前）预热 Chrome → TAA 页。

## 文件改动清单

### 桌面工具核心
| 文件 | 改动概要 |
|---|---|
| `tools/shopify_image_localizer/version.py` | `2.5` → `2.6` |
| `tools/shopify_image_localizer/gui.py` | 窗口尺寸算法、登录提示文案+省略号截断、左右分栏 `_build_summary`、`_format_elapsed`、`_progress_*` 方法群（start/record_step/finish/tick）、`_is_meaningful_step` 防御过滤、三种结束路径的 messagebox 与醒目日志 |
| `tools/shopify_image_localizer/rpa/ez_cdp.py` | 新增 `STARTUP_URL` 常量；`ensure_cdp_chrome.initial_url` 加默认值；`_run_step` 模板中文化；30+ 处 `_log` / `_run_step` 标签中文化（保留专名） |
| `tools/shopify_image_localizer/rpa/run_product_cdp.py` | 删除 line 999 的 `print(json.dumps(result, indent=2))` JSON dump（这是 GUI 进度面板被 JSON 报文淹没的根因）；26 处英文 `print("[detail]/[carousel]/[bootstrap]/[result]/[download]/[blocked] ...")` 全部中文化；新增 `_preload_chrome_tab_to_url` helper；轮播图与详情图段开头分别预热 Chrome |

### 测试
| 文件 | 改动概要 |
|---|---|
| `tests/test_shopify_image_localizer_gui.py` | `_make_app` 增加 `messagebox.{showinfo,showerror,showwarning}` 的 monkeypatch，避免任务结束弹窗阻塞测试；登录提示文案断言更新；`packed_widgets.index(app.summary_tree)` → `packed_widgets.index(app.progress_summary_pane)` |
| `tests/test_shopify_image_localizer_batch_cdp.py` | `[carousel] START open EZ page` 等英文断言全部更新为中文（`[轮播图] 开始：打开 EZ 页面` 等），保持与 ez_cdp 中文化对齐 |

### 文档
| 文件 | 改动 |
|---|---|
| `docs/shopify-image-localizer-v2.6-release-notes.md` | 本文件 |

## 完整发布流程（按 CLAUDE.md，**这次会真正全部走完**）

1. 编译 exe + 打 portable zip：`python -m tools.shopify_image_localizer.build_exe --version 2.6` → `G:\ShopifyRelease\ShopifyImageLocalizer-2.6\` + `ShopifyImageLocalizer-portable-2.6.zip`
2. SCP 上传 zip 到 `root@172.30.254.14:/opt/autovideosrt/web/static/downloads/tools/`
3. 通过 `appcore.shopify_image_localizer_release.set_release_info(...)` 写入数据库 `system_settings.shopify_image_localizer_release` JSON：
   ```python
   set_release_info(
       version="2.6",
       released_at="2026-04-30 HH:MM:SS",
       download_url="/static/downloads/tools/ShopifyImageLocalizer-portable-2.6.zip",
       filename="ShopifyImageLocalizer-portable-2.6.zip",
       release_note="...",
   )
   ```
4. HTTP HEAD 验证：`http://172.30.254.14/static/downloads/tools/ShopifyImageLocalizer-portable-2.6.zip` 返回 200。
5. 素材管理页 `/medias` 顶部「下载自动换图工具」按钮的版本号、发布时间应自动更新（来自数据库 JSON，不在前端硬编码）。

## 已知遗留事项

- 服务器上残留 v2.4 zip（`ShopifyImageLocalizer-portable-2.4.zip`），未被任何下载链接引用，可保留作历史记录或单独清理。数据库已通过 v2.6 重新覆盖，前端只会展示 v2.6。
- 中途的 v2.1/2.2/2.3/2.4 都只在本地构建过用于迭代测试，**没有 commit 到 GitHub**——这是本次纠正的根本问题。后续每次构建发布前**必须先 commit master 再打包**，否则其他会话拿不到改动。
