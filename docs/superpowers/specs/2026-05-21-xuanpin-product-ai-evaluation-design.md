# 选品卡片产品 AI 评估设计

最后更新：2026-05-21

## 范围

本次只覆盖选品中心的明空选品、视频素材库和昨天消耗前 100 素材卡片。目标是在“加入素材库”和“创建小语种翻译任务”两个决策点触发产品 AI 评估，并把评估结论回显到小语种任务创建弹窗里，辅助管理员判断哪些国家值得本土化后投放 Meta。

## 评估输入与模型

- use case 继续复用 `material_evaluation.evaluate`，避免拆出第二套产品评估链路。
- 通道固定为 OpenRouter，模型为 `google/gemini-3.5-flash`。
- 输入包括明空商品链接（卡片标题跳转或标题下方产品链接，不使用尚未生成的 Shopify/本地站点链接）、商品主图、指定英文视频素材的前 30 秒 480p LLM 优化片段。
- 评估国家固定为德国、法国、意大利、西班牙、日本、美国，对应语种代码为 `DE`、`FR`、`IT`、`ES`、`JA`、`EN`。
- 输出保持结构化 JSON，国家项包含 `score`、`is_suitable`、`decision`、`recommendation`、`summary`、`reason`、`suggestions` 等字段。`recommendation` 只取 `做` 或 `不做`，便于前端直接映射 ✅/❌。

## 触发点

1. 明空卡片点击“加入素材库”并成功写入 `media_products` / `media_items` 后，服务端异步触发该产品评估，优先使用刚入库的 `media_item_id`。
2. 卡片或入库进度弹窗点击“创建小语种翻译任务”并成功创建父任务后，服务端异步触发评估。若已有评估结果则不强制重评；若正在评估则复用运行中的任务。
3. 卡片新增独立“AI评估”按钮。已入库且有本地产品 ID 时可点击，按钮会同步执行一次人工重评并打开“请求 / 结果”双 Tab 弹窗。

## 前端表现

- 卡片按钮区增加 AI 评估按钮。未入库时禁用并提示先加入素材库。
- AI 评估弹窗复用 `eval_country_table.js` 的结果表，新增请求 Tab 展示商品链接、商品主图、视频预览、system/user prompt、response schema 和请求 JSON；结果 Tab 展示结构化国家评分。
- 小语种任务弹窗加载产品详情后，从 `ai_evaluation_detail.countries` 建立语种索引。每个语言胶囊下方显示 `AI建议：✅` 或 `AI建议：❌`；鼠标悬停展示该国家评分、推荐结论和核心摘要。没有结果时显示 `AI建议：暂无`，评估中不阻塞创建任务。

## 错误与降级

- 自动触发评估只记录和排队，不阻塞入库或任务创建。
- 若缺商品链接、主图或英文视频，评估任务返回现有 `missing_*` 状态并在产品评估详情里保留失败信息。
- 请求预览和完整报文接口继续省略大体积 base64；点击完整报文时才加载完整 payload。

## 验收

- `material_evaluation.evaluate` 默认绑定和运行时请求均为 OpenRouter + `google/gemini-3.5-flash`。
- 请求预览展示 30 秒 480p LLM 视频片段信息，并能按卡片 `media_item_id` 选择对应视频。
- `/mk-import/video`、`/tasks/api/parent`、`/tasks/api/import-and-create` 成功后都会触发异步评估。
- 选品素材卡片包含独立 AI 评估按钮和双 Tab 弹窗逻辑。
- 小语种语言胶囊显示 AI 建议标记和 tooltip 摘要。
