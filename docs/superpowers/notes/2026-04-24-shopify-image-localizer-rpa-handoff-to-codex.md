# Shopify 图片本地化 RPA — 给 Codex 的接手说明

更新时间：2026-04-24
工作目录：`G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish`
样例：`product_code = dino-glider-launcher-toy-rjc`，`shopify_product_id = 8552296546477`，目标语言 `it`

---

## 0. 给 Codex 的指令（直接照做）

> 你接手这个 Shopify 图片本地化工具的 IT (意大利语) 自动替换。前置工作（OpenAPI、下载、配对、Chrome 启动器、pyautogui RPA 流程）**已经基本跑通**，但有 2 个硬骨头需要你解决：
>
> 1. **服务端 `bootstrap` 接口对 lang=it 周期性返回 `localized images not ready`**，状态在 ready ↔ not-ready 之间无规律切换。需要你在服务端找到原因（很可能是某个后台 task 周期性 soft-delete + 重新生成 it 文件）并修，或在客户端做更稳的等待重试。文件：[appcore/medias.py:280-335](appcore/medias.py#L280-L335) 和 [web/routes/openapi_materials.py](web/routes/openapi_materials.py)。
> 2. **当前用户 EZ 上 slot 0 / slot 1 已被占位 (de 图作 italian)**，需要你在 RPA 流程里加 “italian region 已存在则覆盖” 的分支：dialog 弹出后判断 italian region 是否已经显示，如果是 → 不点 Add Language，直接在 italian region 内点 “Replace media” / “Add media”（行为可能不同，需要你截图确认实际 UI）→ 上传新文件 → save。当前代码只 handle “italian 还没添加” 的情况。文件：[tools/shopify_image_localizer/rpa/ez_pyautogui.py](tools/shopify_image_localizer/rpa/ez_pyautogui.py)。
>
> **不要**重做这些方向（已经验证全部走不通）：
> - Playwright `launch_persistent_context` 启动 Chromium → Shopify App Bridge 反调试，EZ iframe 加载到一半被移除
> - Playwright `connect_over_cdp` 接 7777 端口 → 同上
> - Playwright pipe 模式 + `ignore_default_args=["--enable-automation"]` + `navigator.webdriver` stealth → 同上
> - Chrome MV3 Extension `--load-extension=...` → Chrome 137+ 默认禁 unpacked extension，`extensions.ui.developer_mode` Preferences 修改被 Secure Preferences MAC 重置，命令行没有 override flag
> - `--disable-web-security` / `--disable-features=ThirdPartyStoragePartitioning` 等绕开 COEP → 都触发 Shopify 反调试
>
> **唯一被验证 work 的路线**：subprocess 启动**完全干净的 Chrome**（无任何 automation flag）+ pyautogui OS 级鼠标自动化 + cv2 模板匹配定位按钮 + win32clipboard 粘贴文件路径到系统文件对话框。`replace_one_at` 已经实测能完成完整一轮“点 Edit Translations → Add Language → 选 Italian → Add media → 粘路径 + Enter → Save”，**EZ 主页能看到 Italian 标签出现**。
>
> 工作流程入口：`python -m tools.shopify_image_localizer.rpa.run_it`，按 ESC 中断。

---

## 1. 项目背景（30 秒版本）

工具叫 **Shopify Image Localizer**，本地 EXE，给 Shopify 商品的 EZ Product Translate 应用做图片自动替换。

输入：`product_code` + 目标语言（如 `it`）。

工具应该：
1. 调本项目 OpenAPI 拿英文参考图 + 本地化图列表（hash 配对到 Shopify 上的 9 张图）
2. 启动 Chrome（用专属 profile `C:\chrome-shopify-image`）打开 EZ Product Translate 页面
3. 自动点击每张图 → Edit Translations → 添加目标语言 → 上传对应本地图 → 保存

完成 9 张图自动替换。

---

## 2. 已完成 ✅

### 2.1 服务端 OpenAPI（已部署到 172.30.254.14）

- `GET /openapi/medias/shopify-image-localizer/languages`
- `POST /openapi/medias/shopify-image-localizer/bootstrap`（**有不稳定 bug，见 §6**）

文件：
- [appcore/medias.py:280-335](appcore/medias.py#L280-L335) `list_reference_images_for_lang` / `list_shopify_localizer_images`
- [web/routes/openapi_materials.py:132-195](web/routes/openapi_materials.py#L132-L195)

接口验收命令：
```bash
curl -X POST 'http://172.30.254.14/openapi/medias/shopify-image-localizer/bootstrap' \
  -H 'X-API-Key: $OPENAPI_MEDIA_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"product_code":"dino-glider-launcher-toy-rjc","lang":"it"}'
```

### 2.2 GIF 过滤

接口已在 `list_shopify_localizer_images` 包装层过滤 `.gif` 后缀（[appcore/medias.py:329-345](appcore/medias.py#L329-L345)）；客户端在 [tools/shopify_image_localizer/controller.py:11-30](tools/shopify_image_localizer/controller.py#L11-L30) 也做了 `_drop_gifs` 兜底。

> 注意：服务端的 GIF 过滤改动**还在本地 worktree**，**没有 deploy 到 172.30.254.14**。客户端兜底已生效。

### 2.3 Hash 配对算法

**关键发现**：每张本地化图的 filename 里**嵌入了对应英文原图的 MD5 hash**，例如：
- en `detail-1`：`20260419_9030241b_from_url_en_00_7d013e06ebecfb92a8351736827cc30d.jpg`
- it 对应：`20260424_39cdb4d5_20260419_9030241b_from_url_en_00_7d013e06ebecfb92a8351736827cc30d.png`
- Shopify 第 1 张图 src：`https://cdn.shopify.com/.../files/7d013e06ebecfb92a8351736827cc30d.jpg`

三者尾部 hash 完全一致 → 直接按 hash 字符串配对，9/9 准确，不需要图像识别 / phash。

实现：[tools/shopify_image_localizer/rpa/run_it.py:42-53](tools/shopify_image_localizer/rpa/run_it.py#L42-L53)

### 2.4 Chrome 启动器（无 CDP）

[tools/shopify_image_localizer/browser/session.py](tools/shopify_image_localizer/browser/session.py) 提供：
- `find_chrome_executable()` — 找 chrome.exe
- `detect_window_bounds()` — 副屏 / 主屏自适应
- `detect_system_proxy()` — 自动探测 Clash 7890 / V2Ray 端口（用户机器有 Clash TUN）
- `start_chrome(user_data_dir, initial_urls, ...)` — subprocess.Popen detached，**无任何 automation flag**，`--start-maximized`
- `is_chrome_running_for_profile()` / `open_urls_in_chrome()` / `kill_chrome_for_profile()`

**关键约束**（已踩坑）：
- 不能加 `--remote-debugging-port`、`--remote-debugging-pipe`、`--disable-blink-features=AutomationControlled`、`--disable-web-security`、`--disable-features=ThirdPartyStoragePartitioning,...`、`--enable-automation`、`--test-type` 任何一个 → 都会触发 Shopify App Bridge 反调试，EZ iframe 不加载

### 2.5 RPA 模板匹配 + 流程

[tools/shopify_image_localizer/rpa/ez_pyautogui.py](tools/shopify_image_localizer/rpa/ez_pyautogui.py)：

- `find_edit_button_centers()` — cv2 模板匹配定位 EZ 主页所有 “+ Edit Translations” 按钮的物理坐标，模板在 [tools/shopify_image_localizer/rpa/templates/btn_edit_translations.png](tools/shopify_image_localizer/rpa/templates/btn_edit_translations.png)
- `replace_one_at(button_x, button_y, local_image_path, language='italian')` — 单个 slot 完整流程
- `replace_many_dynamic(pairs, language)` — 多 slot 循环 + 自动 scroll
- `AbortSignal` + `check_abort()` + `abortable_sleep()` — 按 ESC 即刻中断
- `find_chrome_window()` — 多种 title fallback + Chrome class name 匹配（处理 chrome 窗口 title 被改为图片 hash 的情况）

**已校准的坐标**（chrome maximized 主屏 3840×2160，DPI 100%）：
```python
ADD_LANGUAGE_DROPDOWN = (1977, 1535)   # Italian 未添加时下拉位置
LANGUAGE_OPTION_X = 1900               # Italian 列 x
LANGUAGE_OPTION_Y["italian"] = 860     # Italian 行 y（下拉是向上展开）
ADD_MEDIA_BUTTON = (1995, 1395)        # Italian region 内的 Add media 按钮
SAVE_BUTTON = (2517, 1772)             # Italian 添加后 Save 位置
CANCEL_BUTTON = (2425, 1775)
```

> 这套坐标只在 chrome 主屏 maximized 3840×2160 时有效。其他 DPI / 分辨率需要重新校准。校准方法：`PIL.ImageGrab.grab()` 截屏 + 加 50 px grid 标线，用眼测量元素中心。

### 2.6 ESC 中断机制

`pyautogui.FAILSAFE = True`（鼠标拖角触发）+ `win32api.GetAsyncKeyState(VK_ESCAPE)` 每 50ms 检查一次，按 ESC 立即 `raise AbortSignal`。`abortable_sleep` 把所有 `time.sleep` 拆成 50ms tick。

### 2.7 端到端入口

[tools/shopify_image_localizer/rpa/run_it.py](tools/shopify_image_localizer/rpa/run_it.py)：

```bash
python -m tools.shopify_image_localizer.rpa.run_it
```

执行步骤：
1. ensure Chrome（in profile `C:\chrome-shopify-image`），打开 EZ URL
2. retry it bootstrap 直到 ready，立即下载 it 图
3. hash 配对 Shopify product.images（公开 JSON：`https://0ixug9-pv.myshopify.com/products/dino-glider-launcher-toy-rjc.json`） ↔ 本地 it
4. `replace_many_dynamic` 跑所有 pair

---

## 3. 当前实测状态

**最近一次实跑（2026-04-24 16:45 左右）**：

- ✅ Chrome 启动 + EZ 加载正常
- ✅ cv2 找到 9 个 Edit Translations 按钮（每行 5 个 + 4 个，间距 255 物理 px）
- ✅ slot 0 完整 RPA 跑通：dialog → Add Language → Italian → Add media → 文件对话框 paste 路径 + Enter → Save → **EZ 主页第 1 张图下出现 "Italian" 标签**（实测确认 save 真生效）
- ✅ slot 1 用 de 占位也成功（同样流程）
- ⚠️ slot 2-8 失败：`EZ Product Translate Chrome window not found`
  - 原因：RPA paste 文件路径时 chrome 窗口 title 被改为文件 hash 字符串，`find_chrome_window` 旧版只匹配 "EZ Product Translate" 找不到
  - **已修**（[ez_pyautogui.py:104-128](tools/shopify_image_localizer/rpa/ez_pyautogui.py#L104-L128) 加了多重 fallback + Chrome class name 匹配），未在最新 chrome 状态再实测

---

## 4. 文件结构

```
tools/shopify_image_localizer/
├── api_client.py              # OpenAPI client (fetch_languages / fetch_bootstrap)
├── browser/
│   ├── __init__.py            # 暴露 run_shopify_localizer
│   ├── session.py             # Chrome 启动 + window 管理（无 CDP）
│   ├── orchestrator.py        # 半自动 orchestrator（早期版本，不再主路径）
│   ├── ez_flow.py / translate_flow.py  # 早期 Playwright 路线（已废弃，但代码留着）
├── chrome_ext/                # 早期 Extension 路线（已废弃，可删）
│   ├── manifest.json
│   ├── background.js
├── controller.py              # 端到端 controller（半自动版本，包含 _drop_gifs 兜底）
├── diag/                      # 各类诊断脚本（dom_probe, recorder, ext_smoke, pipe_spike, interactive_probe）
├── downloader.py              # 图片下载
├── ext_bridge.py              # 早期 HTTP polling bridge（Extension 路线，已废弃）
├── gui.py                     # tkinter GUI（半自动版本，配对清单 + 复制路径）
├── locales.py                 # ISO 代码 → 英文语言名映射
├── main.py                    # GUI 入口
├── matcher.py                 # 早期 link_check 配对（hash 配对取代后基本不用）
├── rpa/                       # ★ 当前主路径
│   ├── ez_pyautogui.py        # 核心 RPA 实现
│   ├── run_it.py              # 端到端 runner
│   └── templates/
│       └── btn_edit_translations.png  # cv2 模板
├── settings.py
└── storage.py
```

---

## 5. 路线决策记录（避免 Codex 重蹈覆辙）

| 路线 | 结果 | 原因 |
|------|------|------|
| Playwright `launch_persistent_context` 启动 Chromium | ❌ | EZ freshify.click iframe 加载后被 React 主动移除 |
| Playwright `connect_over_cdp` 7777 端口 | ❌ | 同上，Shopify App Bridge 检测 CDP 标志 |
| Playwright pipe 模式（`ignore_default_args` + stealth `navigator.webdriver`） | ❌ | 同上，pipe 模式仍触发检测 |
| Chrome 加 `--disable-web-security` 禁 COEP | ❌ | 同上，且 Chrome 137+ 弹"不受支持的命令行标记"警告条 |
| Chrome 加 `--disable-features=ThirdPartyStoragePartitioning,...` | ❌ | iframe 仍被移除 |
| Chrome 加 `--disable-quic` / `--disable-features=AsyncDns` | ❌ | 不解决问题 |
| Chrome MV3 Extension `--load-extension=...` | ❌ | Chrome 137+ 默认禁 unpacked extension，`extensions.ui.developer_mode` Preferences 修改被 Secure Preferences MAC 重置 |
| Extension + chrome.debugger.attach + DOM.setFileInputFiles | 🚧 | 理论可行但 extension load 不上 |
| **subprocess 启动干净 Chrome + pyautogui RPA + cv2 + win32clipboard** | ✅ | 当前主路径，已验证完整跑通 1 张 |

**Chrome 干净启动参数**（必须保持精简）：
```
--user-data-dir=C:\chrome-shopify-image
--no-first-run
--no-default-browser-check
--start-maximized
--proxy-server=http://127.0.0.1:7890       # 自动探测，用户机器有 Clash
--proxy-bypass-list=127.0.0.1;localhost;172.30.254.14;<local>
```

不能加任何 `--remote-debugging-*`、`--enable-automation`、`--disable-blink-features`、`--disable-web-security`、`--disable-features` 等。

---

## 6. 已知问题

### 6.1 服务端 it bootstrap 接口周期性 not ready ⚠️ **最大阻塞**

观察到的模式：
- 短窗口 ready：5-15 秒内连续 200 OK，返回 15 张 it 图
- 长 not-ready 期：5+ 分钟连续 409 `localized images not ready`
- 切换没有规律（不是简单的 cache TTL）

更严重：**有时 bootstrap 200 OK 返回 signed URL，但 TOS 上对应的物理文件已经 404**。说明服务端有后台 task 在删 + 重传 it 文件，造成接口数据 + TOS 文件 inconsistent。

排查方向（建议 Codex 做）：
- `media_product_detail_images` 表的 `product_id=319 AND lang='it' AND deleted_at IS NULL` 是不是被某个后台 task 周期性 soft-delete + 重新生成
- TOS object lifecycle 是不是有规则在自动删除 it 文件
- 一键翻译回填是不是循环触发

数据 dump 在：`tmp_probe/bootstrap_it_*.json`（多个时间戳的 response 留档）

### 6.2 已被 placeholder 污染的 EZ slot

slot 0、slot 1 已经被 RPA 验证流程时上传了 de 图作 italian 翻译。Shopify 上现在显示这两张图的 italian 翻译 = 德语图（不对的）。

需要 Codex 决定：
- A. 在 EZ 上手动删除这两张 placeholder
- B. 在 RPA 代码里加 "italian region 已存在则覆盖" 的分支，跑全 9 张时自动覆盖

### 6.3 Chrome window title 会被改

paste 文件路径到系统文件对话框时，chrome 窗口 title 会被同步成文件 hash 字符串。我已经在 `find_chrome_window` 加了 fallback（Chrome class name 匹配），但**没有在最新状态再次实测**。

### 6.4 坐标硬编码到 maximized 3840×2160 主屏

[ez_pyautogui.py 第 32-72 行](tools/shopify_image_localizer/rpa/ez_pyautogui.py#L32-L72) 那一组坐标只在用户当前显示器分辨率 + DPI 下有效。换电脑要重新校准。

更好的方案（建议）：所有 dialog 元素都用 cv2 模板匹配 + 多个模板（Add Language、Italian option、Add media、Save 各一个 png 模板）。当前只有 "Edit Translations" 按钮一个模板。

### 6.5 Italian 选项位置 hardcoded "Italian = 第 3 项"

`LANGUAGE_OPTION_Y["italian"] = 860` 假设 dropdown popup 第 3 项是 Italian。如果 store 启用的语言变了（add/remove enabled languages），这个 Y 会偏。

更好做法：用 cv2 OCR / 模板匹配在 dropdown 里找 "Italian" 文字位置。但 tesseract 没装，要先安装；或者预先 crop "Italian" 文字图作模板。

### 6.6 EZ 视觉顺序 vs Shopify product.images 顺序

当前假设两者一致（按行从左到右、从上到下 = position 0..N-1）。如果 EZ 按其他规则排序（如 created_at），配对会错位。

更稳的做法（建议）：每点 Edit Translations 弹 dialog 后，dialog 标题文字 `Add or Update a translation for: <hash>.jpg` 包含图的 hash，用 OCR 提取 hash 后与本地 it 图按 hash 做最终配对。

### 6.7 翻页 / scroll 还没实测

`replace_many_dynamic` 写了 `pyautogui.scroll(-N)` 滚动逻辑，但这个产品只有 9 张图都在 viewport 内，scroll 路径**没实测过**。30+ 张图时是否真的能正确处理需要验证 + 调试。

---

## 7. 下一步建议（按优先级）

### P0 — it 接口稳定（阻塞所有真实数据测试）

修服务端 bootstrap + TOS 数据 inconsistency。这件事不修，RPA 永远拿不到稳定的 it 图。

### P1 — 跑通真实 it 9 张

it 接口稳定后立即跑 `python -m tools.shopify_image_localizer.rpa.run_it`，观察 9 张是否全成功。

### P2 — 处理 placeholder slot 0/1

清掉之前 de 占位 italian 翻译，或加覆盖分支。

### P3 — Chrome window title fallback 实测

重做一次完整 9 张跑，确认 `find_chrome_window` fallback 真能在 title 被改后找到 chrome。

### P4 — 给 dialog 元素加 cv2 模板（替代硬编码坐标）

为 "Add Language"、"Italian"、"Add media"、"Save"、"Cancel" 各 crop 一个 png 模板，运行时模板匹配定位，告别硬编码坐标。这样换电脑或换分辨率不用重测。

### P5 — OCR dialog title 做 hash 验证

防止 EZ 视觉顺序和 Shopify position 不一致导致配对错位。

### P6 — TAA (Translate and Adapt) 分支

当前 RPA 只做了 EZ。TAA 是另一条独立链路，dialog 流程不同，未实现。

### P7 — 重新打包 EXE

[tools/shopify_image_localizer/build_exe.py](tools/shopify_image_localizer/build_exe.py) + [packaging/shopify_image_localizer.spec](tools/shopify_image_localizer/packaging/shopify_image_localizer.spec) 是旧版（半自动 GUI 版本），还没把 RPA 模块 + cv2 模板包进去。RPA 流程稳定后需要更新打包脚本。

---

## 8. 调试 / 实验产物（tmp_probe/）

- `bootstrap_it_*.json` — 多次 bootstrap 接口响应留档
- `it_pairs.json`、`de_pairs.json` — 已配对好的 (slot, local_path) 列表
- `*_thumb.png` — 各种状态截图（chrome 窗口、dialog、dropdown 展开）
- `dlg_full.png` / `dlg_real.png` / `it_added.png` — dialog 各种状态全图，用于校准坐标
- `save_cancel_grid.png` / `dropdown_higher_grid.png` 等 — 加了 grid 标尺的元素定位图

这些都是临时产物，不要 commit。

---

## 9. 给 Codex 的一句话总结

**RPA 流程已经能完整跑完 1 张图（实测 EZ 上看到 italian 标签），剩下两个事情**：(a) 服务端 it bootstrap 接口稳定（最难，要查后台 task），(b) RPA 加 “italian 已存在则覆盖” 分支（不难，用前面坐标校准方法量出 “Replace media” 按钮位置即可）。

不要重做 Playwright / Extension 路线，那两条死了。

主路径文件：[tools/shopify_image_localizer/rpa/ez_pyautogui.py](tools/shopify_image_localizer/rpa/ez_pyautogui.py) + [tools/shopify_image_localizer/rpa/run_it.py](tools/shopify_image_localizer/rpa/run_it.py)。

入口：`python -m tools.shopify_image_localizer.rpa.run_it`，按 ESC 中断。
