# 给 Claude Code 的接手指令

把下面整段直接发给 Claude Code 即可。

```text
你现在接手 AutoVideoSrtLocal 里的 Shopify 图片本地化工具开发。请用中文工作，并基于当前本地代码继续，不要从头重做。

仓库工作目录：
G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish

先读这些文件，再开始动手：
1. G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\docs\superpowers\notes\2026-04-24-shopify-image-localizer-handoff.md
2. G:\Code\AutoVideoSrtLocal\.worktrees\codex-shopify-image-localizer-design\docs\superpowers\specs\2026-04-24-shopify-image-localizer-design.md
3. G:\Code\AutoVideoSrtLocal\.worktrees\codex-shopify-image-localizer-design\docs\superpowers\plans\2026-04-24-shopify-image-localizer-implementation.md
4. G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\web\routes\openapi_materials.py
5. G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\appcore\medias.py
6. G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\gui.py
7. G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\controller.py
8. G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\session.py
9. G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\ez_flow.py
10. G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\translate_flow.py
11. G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\browser\orchestrator.py
12. G:\Code\AutoVideoSrtLocal\.worktrees\merge-master-publish\tools\shopify_image_localizer\build_exe.py

这个模块的目标不是做 Web 页面，而是继续完善一个本地 EXE 工具。当前已经完成了：
- OpenAPI：/openapi/medias/shopify-image-localizer/languages 和 /bootstrap
- Shopify ID 只从 media_products.shopifyid 取；缺失时接口会返回中文提示
- 本地 GUI、下载、落盘、manifest、持久化浏览器、登录检测都已打通
- EXE 打包已可用，打包后的默认配置走正式环境 http://172.30.254.14
- 浏览器目录固定为 C:\chrome-shopify-image
- 样例商品是 dino-glider-launcher-toy-rjc，Shopify 商品 ID 是 8552296546477，目标语言先用 de

你接手后的核心任务不是重写架构，而是把 Shopify 真实页面流程做实。当前最大缺口是：
- EZ 和 Translate and Adapt 的 DOM 选择器还是通用 img[src] 启发式
- 上传动作还是通用 file input / button 启发式
- 还没有真正按插件页面补齐语言切换、保存、确认等动作
- orchestrator 当前是 dual_page_serial，不是真并发

你的优先级请按下面顺序执行：
1. 直接基于现有工具做一次真实 smoke，优先用样例商品 dino-glider-launcher-toy-rjc / de
2. 如果需要登录 Shopify，就复用 C:\chrome-shopify-image 并等待人工登录
3. 观察 EZ Product Image 页面真实 DOM，收紧轮播图图位选择器，补齐必要的点击、上传、保存逻辑
4. 观察 Translate and Adapt 页面真实 DOM，收紧详情图/编辑区选择器，补齐替换和保存逻辑
5. 保持结果判定诚实：做不到就返回 partial 或 failed，不要误报 done
6. 保持服务端 API 契约不变，不要把自动化挪到服务端，不要要求服务端先拆 carousel/detail
7. 完成后重新打包 EXE，并给出可以让我直接实跑的产物路径

重要边界：
- 不要改成 Web 工具
- 不要新增本地 MySQL 依赖
- 不要把 product_code 改成别的主键
- 不要动“缺 Shopify ID 就提示用户去产品编辑页底部填写”的约束
- 不要优先去补大量测试，先以真实页面实跑为主，必要时只补低成本纯函数测试

建议你每完成一个关键节点，就明确告诉我：
- 改了哪些文件
- 当前真实可跑到哪一步
- 卡住点是接口、登录、DOM 还是上传动作
- 下一步准备怎么收敛

如果你发现当前本地代码和 origin/master 不一致，优先以这个本地 worktree 的代码为准继续，因为这里有尚未完全推送/整理完的 Shopify localizer 最新上下文。
```

