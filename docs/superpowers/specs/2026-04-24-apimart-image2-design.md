# APIMART GPT-Image-2 接入图片翻译模块 — 设计文档

**日期**: 2026-04-24  
**状态**: 已审批，待实施  
**范围**: 在图片翻译模块新增 APIMART 作为第五个生成通道

---

## 背景

图片翻译模块（`appcore/gemini_image.py`）采用通道（channel）分发架构，目前支持四个通道：

| channel | 提供商 | 模型 |
|---------|--------|------|
| `aistudio` | Google AI Studio | gemini-3.1-flash-image-preview / gemini-3-pro-image-preview |
| `cloud` | Google Cloud Vertex AI | 同上 |
| `openrouter` | OpenRouter | Gemini 系列 + OpenAI Image 2 |
| `doubao` | 豆包 ARK | doubao-seedream-5-0-260128 |

APIMART 提供 `gpt-image-2` 模型，兼容 OpenAI Images 协议，支持图生图，采用**异步任务**模式（提交 → 轮询）。API 已验证联通（实测出图约 37 秒）。

---

## 设计决策

- **分辨率**：固定 `1k`（1024×1024 for 1:1），不暴露给用户
- **size 参数**：固定 `"auto"`，由 APIMART 自动匹配输入图比例
- **API Key**：存储在 `.env`，环境变量名 `APIMART_IMAGE_API_KEY`
- **方案**：内联进 `gemini_image.py`（与 doubao/openrouter 保持一致模式，零新文件）
- **不走** `llm_client` / `llm_bindings` / `llm_use_cases`（图片生成 API 与 LLM chat/generate 接口不兼容）

---

## 改动范围

### 1. 环境变量（`.env`）

新增：
```
APIMART_IMAGE_API_KEY=<your_key>
```

### 2. `appcore/image_translate_settings.py`

三处扩展，仅追加，不修改已有条目：

```python
CHANNELS = ("aistudio", "cloud", "openrouter", "doubao", "apimart")

CHANNEL_LABELS = {
    "aistudio": "Google AI Studio",
    "cloud": "Google Cloud (Vertex AI)",
    "openrouter": "OpenRouter",
    "doubao": "豆包",
    "apimart": "APIMART (GPT-Image-2)",          # 新增
}

IMAGE_MODELS_BY_CHANNEL = {
    "aistudio":    [...],
    "cloud":       [...],
    "openrouter":  [...],
    "doubao":      [...],
    "apimart": [("gpt-image-2", "GPT-Image-2")], # 新增，固定一个条目
}
```

Settings 页面通道下拉自动显示新选项（前端读 `CHANNELS` + `CHANNEL_LABELS`），**无需改 HTML/JS**。

### 3. `appcore/gemini_image.py`

#### 3a. 新增常量

```python
APIMART_BASE_URL = "https://api.apimart.ai"
_APIMART_POLL_INTERVAL = 5      # 秒
_APIMART_POLL_TIMEOUT = 120     # 秒
_APIMART_INITIAL_WAIT = 15      # 秒，首次轮询前等待
```

#### 3b. 新增私有函数 `_generate_via_apimart()`

```
签名：_generate_via_apimart(
    prompt: str,
    source_image: bytes,
    source_mime: str,
    *,
    api_key: str,
) -> tuple[bytes, str, Any]
```

执行流程：

1. 将 `source_image` 编码为 `data:{source_mime};base64,...` URI
2. POST `{APIMART_BASE_URL}/v1/images/generations`
   - Headers: `Authorization: Bearer {api_key}`, `Content-Type: application/json`
   - Body:
     ```json
     {
       "model": "gpt-image-2",
       "prompt": "<prompt>",
       "n": 1,
       "size": "auto",
       "resolution": "1k",
       "image_urls": ["data:image/...;base64,..."]
     }
     ```
3. 校验响应 `code == 200`，取 `data[0].task_id`
4. `time.sleep(_APIMART_INITIAL_WAIT)`（15 秒首次等待）
5. 轮询 `GET {APIMART_BASE_URL}/v1/tasks/{task_id}`，间隔 `_APIMART_POLL_INTERVAL`
   - `status == "completed"` → 下载 `data.result.images[0].url[0]`，返回 `(bytes, "image/png", raw)`
   - `status == "failed"` → 抛 `RuntimeError(data.error.message)`
   - 超过 `_APIMART_POLL_TIMEOUT` → 抛 `TimeoutError("APIMART task timed out: {task_id}")`
6. 图片下载失败（非 200）→ 抛 `RuntimeError("APIMART image download failed: {status_code}")`

返回值与 `_generate_via_openrouter()` / `_generate_via_doubao()` 保持相同结构。

#### 3c. 在 `generate_image()` 分发块新增分支

在现有 `elif channel == "doubao":` 之后追加：

```python
elif channel == "apimart":
    api_key = os.environ["APIMART_IMAGE_API_KEY"]
    img_bytes, mime, raw = _generate_via_apimart(
        prompt, source_image, source_mime, api_key=api_key
    )
```

---

## 错误处理

| 场景 | 行为 |
|------|------|
| `APIMART_IMAGE_API_KEY` 缺失 | `KeyError` 自然上抛，runtime 层捕获并写入任务日志 |
| 提交请求非 200 | `RuntimeError("APIMART submit failed: {status} {body}")` |
| 任务 `status=failed` | `RuntimeError(error.message)` |
| 轮询超时 >120s | `TimeoutError("APIMART task timed out: {task_id}")` |
| 结果图片下载失败 | `RuntimeError("APIMART image download failed: {status_code}")` |

所有异常均由 `image_translate_runtime.py` 现有错误捕获链处理，**不需要改 runtime 层**。

---

## 不在范围内

- `llm_use_cases.py` / `llm_bindings.py` / `llm_client.py` — 不改
- `image_translate_runtime.py` — 不改
- 前端 HTML/JS — 不改
- 数据库 migration — 不需要
- 分辨率用户配置项 — 固定 1k，不暴露

---

## 测试要点

1. 手动在 Settings 切换到 `apimart` 通道，上传一张含文字的商品图，验证翻译后图片输出正常
2. `APIMART_IMAGE_API_KEY` 未设置时，任务日志中出现明确错误信息
3. Settings 页通道下拉出现 "APIMART (GPT-Image-2)" 选项
