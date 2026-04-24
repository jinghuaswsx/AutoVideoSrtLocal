# Shopify 图片本地化工具接手说明

更新时间：2026-04-24  
适用对象：接手继续开发本模块的工程师 / Claude Code  
当前代码基线：`G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish`

## 1. 模块目标

这个模块的目标，是把 Shopify 图片本地化里最重复的人工操作，收敛成一个运行在 Windows 本机上的 EXE 工具。

目标流程是：

1. 用户输入 `product_code`
2. 用户选择目标语言
3. 工具先从服务端接口拿到 Shopify 商品 ID、英文参考图、目标语言图片
4. 图片下载到 EXE 同级目录
5. 工具启动持久化浏览器目录 `C:\chrome-shopify-image`
6. 如果 Shopify 未登录，停在浏览器里等待用户手动登录
7. 登录恢复后继续执行
8. 同一次任务里同时处理两条链路
   - `EZ Product Image` 里的轮播图本地化
   - `Translate and Adapt` 里的详情图/图文块图片替换

这里的关键业务约束是：

- 这是 `tools` 目录下的本地 EXE 工具，不是服务端软件
- 服务端只提供数据接口，不负责替用户远程跑浏览器
- 服务端当前返回的是“英文参考图 + 目标语言混合图”，不会提前拆成轮播图和详情图
- 图片归类和配对必须在 Shopify 实页里反推

## 2. 当前已完成内容

### 2.1 服务端接口

已落地的 OpenAPI：

- `GET /openapi/medias/shopify-image-localizer/languages`
- `POST /openapi/medias/shopify-image-localizer/bootstrap`

主要实现位置：

- [web/routes/openapi_materials.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\web\routes\openapi_materials.py)
- [appcore/medias.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\appcore\medias.py)
- [web/app.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\web\app.py)

当前接口行为：

- `languages` 返回 `items`
- `bootstrap` 入参是 `product_code + lang`
- `bootstrap` 返回：
  - `product.id`
  - `product.product_code`
  - `product.name`
  - `product.shopify_product_id`
  - `language.code`
  - `language.name_zh`
  - `language.shop_locale`
  - `language.folder_code`
  - `reference_images[]`
  - `localized_images[]`

当前 Shopify ID 解析规则：

- 只认 `media_products.shopifyid`
- 如果为空，接口返回 `409`
- 返回的中文提醒是：请先到产品编辑页最底部填写 Shopify ID 后，再执行图片本地化工具

当前图片 URL 规则：

- `openapi_materials.py` 已统一回到 `tos_clients.generate_signed_media_download_url(object_key)`
- 不再有重复 helper 覆盖问题

### 2.2 本地 EXE 工具

源码目录：

- [tools/shopify_image_localizer](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer)

主要文件职责：

- [main.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\main.py)
  - 程序入口
- [gui.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\gui.py)
  - `Tkinter` 800x800 窗口
  - `product_code` 输入框
  - 语言下拉
  - 高级设置区
  - 状态和日志展示
- [controller.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\controller.py)
  - 串起“拉接口 -> 下载 -> 浏览器自动化 -> manifest”
- [settings.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\settings.py)
  - 源码默认连测试环境
  - 打包后默认连正式环境
  - 默认浏览器目录 `C:\chrome-shopify-image`
- [storage.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\storage.py)
  - 本地目录结构、日志、manifest
- [api_client.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\api_client.py)
  - 调 `languages` 和 `bootstrap`
- [downloader.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\downloader.py)
  - 逐张下载图片
- [matcher.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\matcher.py)
  - 用现有 `link_check_desktop.image_compare.find_best_reference` 做配对
- [browser/session.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\session.py)
  - 持久化浏览器、登录检测、目标页打开、页面截图、可见图片抓取、文件上传
- [browser/ez_flow.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\ez_flow.py)
  - EZ Product Image 流程
- [browser/translate_flow.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\translate_flow.py)
  - Translate and Adapt 流程
- [browser/orchestrator.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\orchestrator.py)
  - 浏览器整体编排
- [build_exe.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\build_exe.py)
  - PyInstaller 打包

### 2.3 当前打包和本地交付状态

当前打包命令：

```powershell
python -m tools.shopify_image_localizer.build_exe
```

当前打包脚本特性：

- 打包后的 `dist/ShopifyImageLocalizer/shopify_image_localizer_config.json` 默认写入正式环境地址
- 源码运行时默认还是测试环境地址
- 会生成：
  - `dist/ShopifyImageLocalizer/ShopifyImageLocalizer.exe`
  - `dist/ShopifyImageLocalizer/run_shopify_image_localizer.bat`
  - `dist/ShopifyImageLocalizer-portable.zip`

本机已经准备过的目录：

- `C:\shopify_image`
- `C:\chrome-shopify-image`

## 3. 当前确认可用的数据样例

样例商品：

- `product_code = dino-glider-launcher-toy-rjc`
- `shopify_product_id = 8552296546477`
- `lang = de`

Shopify 直达地址：

- EZ Product Image  
  [https://admin.shopify.com/store/0ixug9-pv/apps/ez-product-image-translate/product/8552296546477](https://admin.shopify.com/store/0ixug9-pv/apps/ez-product-image-translate/product/8552296546477)

- Translate and Adapt  
  [https://admin.shopify.com/store/0ixug9-pv/apps/translate-and-adapt/localize/product?highlight=handle&id=8552296546477&shopLocale=de](https://admin.shopify.com/store/0ixug9-pv/apps/translate-and-adapt/localize/product?highlight=handle&id=8552296546477&shopLocale=de)

生产接口烟测结论：

- `languages` 可返回启用语言列表
- `bootstrap` 能返回：
  - `shopify_product_id`
  - `localized_images`
  - `reference_images`

## 4. 最近关键提交

当前发布 worktree 上和本模块强相关的提交，按时间从旧到新：

- `0725251 feat: add shopify image localizer desktop tool`
- `794ee6b fix: improve shopify localizer runtime flow`
- `0fa6ce1 fix: wire signed download urls for shopify localizer bootstrap`
- `fdfa9fb fix: default packaged localizer to production`
- `136dfc7 fix: harden shopify localizer flow status`

其中最后一笔 `136dfc7` 的意义：

- 不再把“零上传、零分配、全部失败”误判成 `done`
- flow 会根据 `captured_slots / assigned / uploads / review / conflicts` 给出更保守的状态
- orchestrator 聚合状态也改成了 `done / partial / failed`

## 5. 当前真实实现状态

### 5.1 已经落地的能力

- GUI 和配置文件已经可以跑起来
- 语言下拉可从服务端接口初始化
- `bootstrap` 和图片下载链路已经打通
- 本地目录、日志、manifest 已经打通
- Playwright 持久化浏览器已接入
- Shopify 登录失效时可停住等待用户手动登录
- 登录恢复后可继续
- 已经能直接打开两个目标 Shopify 页面
- 已经有“抓页面可见图 -> 和英文参考图配对 -> 试图上传本地图”的通用流程

### 5.2 还没有真正收死的地方

这部分是 Claude Code 接手后最该优先解决的。

#### 1. DOM 选择器还是通用启发式

当前 `EZ_IMAGE_SELECTORS` 和 `TRANSLATE_IMAGE_SELECTORS` 仍然偏泛：

- `main img[src]`
- `[class*='Polaris'] img[src]`
- `img[src]`

这意味着：

- 可能抓到页面里无关图片
- 可能错过真正的业务图位
- 在不同插件版本下稳定性不足

#### 2. 上传动作还是通用启发式

当前 `upload_file_to_page()` 的逻辑是：

- 优先找 `input[type='file']`
- 不行再尝试点名字里带 `add/upload/replace/image/photo/media` 的按钮

这只是基础骨架，不代表已经真正适配了 EZ 和 Translate and Adapt 的具体交互。

#### 3. 还没有插件级“语言切换 / 保存 / 确认”特化逻辑

用户真正业务描述里提到的动作，包括：

- 进入正确语言上下文
- 在插件内选中语言
- 添加后保存
- 在右侧翻译编辑区替换图

这些在当前代码里还没有明确的插件级步骤实现，更多是通用文件上传骨架。

#### 4. 当前 orchestrator 不是“真并发”

`browser/orchestrator.py` 现在返回的 `mode` 是：

- `dual_page_serial`

也就是：

- 在同一个持久化浏览器上下文里开了两个 page
- 但执行顺序是先 EZ，再 Translate
- 没有做真正并发，也没有做“冲突时自动回退串行”的完整策略

#### 5. 还缺一次带真实登录态的 Shopify 实跑收尾

当前代码已经非常接近可用，但最后一公里仍然是：

- 用真实 Shopify 登录态跑一遍
- 抓到两个插件的实际 DOM
- 收紧选择器
- 补上保存/确认动作
- 对失败截图和日志做回看

## 6. 推荐 Claude Code 接手后的优先顺序

### 第一优先级

先把两条 Shopify 流做成“页面级可真实完成任务”的实现，而不是再扩抽象层。

建议顺序：

1. 用现成 EXE 或源码模式启动工具
2. 让用户手动登录 Shopify
3. 针对 `dino-glider-launcher-toy-rjc / de` 做一次完整 smoke
4. 观察：
   - EZ 页面哪些图片是真正轮播图位
   - Translate and Adapt 页面哪些图片是真正详情编辑块
   - 上传后是否需要点击保存、确认、发布
5. 把 DOM 选择器从通用 `img[src]` 收紧到业务区块

### 第二优先级

把结果判定做得更细一点，但不要脱离真实页面：

- `done`
- `partial`
- `failed`
- `needs_review`

同时把 `manifest.json` 里的结构再补完整：

- 每个 slot 对应的参考图 ID
- 选中的 localized 图 ID
- 上传是否成功
- 页面 URL
- 前后截图路径

### 第三优先级

再考虑这些增强，而不是现在先做：

- 真正的双窗口并发 + 冲突回退
- 更漂亮的 GUI
- 更复杂的人工复核界面
- 更多测试覆盖

## 7. 明确约束

接手时请遵守这些边界，不要偏题：

- 不要把它改造成 Web 页面
- 不要把浏览器自动化挪到服务端
- 不要要求服务端先把轮播图和详情图分组
- 不要改掉 `product_code + lang` 这组主入参
- 不要让工具依赖本地 MySQL
- 不要默认走测试环境打包成 EXE
- 如果接口拿不到 `shopify_product_id`，必须明确提示用户去产品编辑页底部填写 Shopify ID

## 8. Claude Code 首读文件清单

建议 Claude Code 接手时先读这些文件：

1. [docs/superpowers/specs/2026-04-24-shopify-image-localizer-design.md](G:\Code\AutoVideoSrtLocal\.worktrees\codex-shopify-image-localizer-design\docs\superpowers\specs\2026-04-24-shopify-image-localizer-design.md)
2. [docs/superpowers/plans/2026-04-24-shopify-image-localizer-implementation.md](G:\Code\AutoVideoSrtLocal\.worktrees\codex-shopify-image-localizer-design\docs\superpowers\plans\2026-04-24-shopify-image-localizer-implementation.md)
3. [web/routes/openapi_materials.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\web\routes\openapi_materials.py)
4. [appcore/medias.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\appcore\medias.py)
5. [tools/shopify_image_localizer/gui.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\gui.py)
6. [tools/shopify_image_localizer/controller.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\controller.py)
7. [tools/shopify_image_localizer/browser/session.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\session.py)
8. [tools/shopify_image_localizer/browser/ez_flow.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\ez_flow.py)
9. [tools/shopify_image_localizer/browser/translate_flow.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\translate_flow.py)
10. [tools/shopify_image_localizer/browser/orchestrator.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\orchestrator.py)
11. [tools/shopify_image_localizer/build_exe.py](G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\build_exe.py)

## 9. 建议的接手验收口径

Claude Code 接手后，不要先追求“代码看起来完整”，而是先追求下面这个真实结果：

对于样例商品 `dino-glider-launcher-toy-rjc / de`：

1. GUI 可以正常启动
2. 语言列表可加载
3. 点开始后能下载图片到本地
4. 浏览器能复用 `C:\chrome-shopify-image`
5. 未登录时能等登录
6. 登录后能自动继续
7. 两个 Shopify 页面都能准确定位到目标图片区域
8. 至少能成功完成一轮真实上传
9. `manifest.json`、`run.log`、截图都足够排错
10. 如果某一步做不到，结果必须诚实显示 `partial` 或 `failed`

## 10. 交接结论

这个模块已经完成了：

- 服务端数据契约
- 本地 EXE 外壳
- 下载和落盘链路
- 持久化浏览器和登录检测
- 通用配对和通用上传骨架
- 正式环境默认打包配置
- 基础状态判定修补

现在最需要的不是重写架构，而是：

- 带真实 Shopify 登录态把 EZ 和 Translate and Adapt 两条流做实
- 把通用启发式收紧成插件级 DOM 逻辑
- 完成最后一轮人工 smoke 和打包交付

