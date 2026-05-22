# AI精细评估商品链接检测设计

最后更新：2026-05-22

## 背景

选品中心的视频卡片发起 `精细AI评估` 时，当前实现直接使用卡片上的 `mk_product_link || product_url`。如果这个链接已经下架、404、403 或网络不可达，后续商品事实整理和国家评估会拿到失效落地页，影响 AI 判断。

## 目标

1. `精细AI评估` 的第一步必须先检测商品链接可访问性。
2. 如果当前链接不可访问，服务端按明空商品详情里的 `product_links` 顺序逐个探活，选择第一个可访问链接。
3. 找到替换链接后，评估 run 使用替换后的链接，前端同步更新当前视频卡片显示链接和按钮数据。
4. 进度 UI 增加独立的“商品链接检测”卡片，展示当前链接、候选数量、最终使用链接、HTTP 状态和错误摘要。
5. 若所有候选链接都不可访问，停止创建评估任务，并在同一张卡片显示失败原因。

## 设计

- 新增 `web.services.fine_ai_product_link_check`，只负责候选链接归一化、顺序探活和结果建模。
- 复用 `appcore.link_availability.probe`，保持 HEAD 优先、405/501 回退 GET、5 秒超时、跟随重定向的既有规则。
- `POST /xuanpin/api/fine-ai-evaluation` 在缓存视频和创建 run 前执行链接检测。当前链接可用时不请求明空详情；当前链接不可用且存在 `mk_product_id` 时，读取明空详情的 `product_links` 作为候选。
- `FineAiEvaluationService.create_external_link_run` 接收 `link_check_result`，把结果写入 `metadata.link_check`，并让 `progress.steps[0]` 成为已完成的 `product_link_check` 卡片。
- 前端 `mkiFineAiStartingProgress` 先显示 `product_link_check` 运行中。创建成功后读取返回的 `link_check`，更新卡片链接 DOM、`data-mki-product-link` 和外部评估链接。

## 非目标

- 不新增数据库 schema。
- 不改变普通 `AI评估`。
- 不改变明空日快照任务的采集顺序。
- 不在 Windows 本机连接 MySQL 做验证。

## 验收

- 当前链接 200 时，直接用于 AI 精细评估。
- 当前链接失败、明空候选第二个链接 200 时，run 的 `product_snapshot.product_url` 和 `metadata.external_product_link` 使用第二个链接。
- 前端模板包含 `product_link_check` 首步、`mk_product_id` 传参、卡片链接同步函数。
- 路由测试不访问真实明空后台或外部网络，全部通过 monkeypatch 验证。
