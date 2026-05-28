# 素材 AI 评估分国家进度设计

## 背景

素材 AI 评估已经改为按国家逐个调用模型，但弹窗仍然只展示一个全局“正在请求中”。运营无法知道德国、法国、意大利等国家分别处于排队、请求、完成还是失败。同时评估请求报文里的短片预览在生产环境返回 500，原因是后端把 `instance/eval_clips/...` 相对路径直接传给 Flask `send_file()`，Flask 按 `web/instance/...` 查找，找不到真实文件。

## 目标

- 评估短片预览接口必须返回真实 mp4，并支持浏览器 Range 请求。
- 手动 AI 评估默认改为异步 run：POST 立即返回 `run_id` 和 `status_url`。
- 后端在每个国家开始、完成、失败时更新进度。
- 弹窗展示国家卡片：排队中、进行中、已完成、报错。
- 单个国家失败不阻断其他国家；最终结果标记为需人工复核或部分完成。

## 后端设计

新增 `material_evaluation_runs` 表保存一次手动评估 run 的整体状态、进度 JSON、结果 JSON 和错误信息。`/medias/api/products/<pid>/evaluate` 默认创建 run 并启动后台线程，兼容 `?sync=1` 保留原同步响应。新增 `/medias/api/products/<pid>/evaluate/status?run_id=...` 给弹窗轮询。

`material_evaluation.evaluate_product_if_ready()` 增加可选 `progress_callback`。分国家调用时：

- 初始化所有国家为 `queued`。
- 当前国家进入 `running`。
- 成功后写 `completed`，带评分、结论、耗时。
- 失败后写 `failed`，带错误摘要，并继续后续国家。
- 所有国家结束后发出 `completed` 或 `partially_completed`。

## 前端设计

素材管理和推送管理复用同一套弹窗逻辑。弹窗顶部保留总状态和耗时；下方新增国家进度条/卡片区。卡片展示国家名、语言、状态、耗时、评分或错误。请求报文和结果 tab 保持原有结构，结果 tab 在 run 完成后切换到最终评估表。

## 验证

- 路由测试覆盖相对路径 mp4 + Range 206。
- 服务测试覆盖异步 start/status payload。
- 评估测试覆盖单国家失败继续后续国家。
- 静态资源测试覆盖 medias.js 和 pushes.js 的轮询与国家卡片。
