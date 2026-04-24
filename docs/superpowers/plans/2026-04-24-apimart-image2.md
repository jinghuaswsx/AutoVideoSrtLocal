# APIMART GPT-Image-2 图片翻译通道 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在图片翻译模块新增 APIMART 作为第五个生成通道，允许管理员在 Settings 页切换到 `apimart` 通道后，由 `gpt-image-2` 完成图生图翻译。

**Architecture:** 在 `gemini_image.py` 内新增 `_generate_via_apimart()` 私有函数（提交任务 → 轮询 → 下载），并在 `generate_image()` 的通道分发块里追加 `elif channel == "apimart"` 分支。`config.py` 统一读取环境变量 `APIMART_IMAGE_API_KEY`；`image_translate_settings.py` 和 `gemini_image.py` 的常量各自注册 `"apimart"`。

**Tech Stack:** Python `requests`（已有依赖）、APIMART REST API（异步：POST → GET 轮询）

---

## 文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| Modify | `config.py:60` | 新增 `APIMART_IMAGE_API_KEY` 读取 |
| Modify | `appcore/image_translate_settings.py:17-23` | `CHANNELS` + `CHANNEL_LABELS` 追加 `"apimart"` |
| Modify | `appcore/gemini_image.py:25-34` | 追加 `APIMART_IMAGE_API_KEY` 导入 |
| Modify | `appcore/gemini_image.py:62-78` | `IMAGE_MODELS_BY_CHANNEL` 追加 `"apimart"` |
| Modify | `appcore/gemini_image.py:228-235` | `_channel_provider()` 追加 `"apimart"` |
| Modify | `appcore/gemini_image.py:514↓` | 新增 `_generate_via_apimart()` 函数（在 `_generate_via_seedream` 之后） |
| Modify | `appcore/gemini_image.py:615-628` | `generate_image()` 分发块追加 `elif channel == "apimart"` |
| Modify | `tests/test_gemini_image.py` | 新增 apimart 通道测试 |
| Modify | `tests/test_image_translate_settings.py` | 新增 apimart 注册断言 |
| Modify | `.env` | 新增 `APIMART_IMAGE_API_KEY=<key>` |

---

## Task 1：config.py 注册 APIMART_IMAGE_API_KEY

**Files:**
- Modify: `config.py:60`
- Modify: `.env`

- [ ] **Step 1：在 `config.py` 第 60 行（`OPENROUTER_API_KEY` 所在行）下方添加**

```python
# APIMART 图片生成
APIMART_IMAGE_API_KEY = _env("APIMART_IMAGE_API_KEY")
```

完整上下文（config.py 第 59-62 行改后）：
```python
# OpenRouter Claude
OPENROUTER_API_KEY = _env("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = _env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# APIMART 图片生成
APIMART_IMAGE_API_KEY = _env("APIMART_IMAGE_API_KEY")
```

- [ ] **Step 2：在 `.env` 文件末尾追加**

```
APIMART_IMAGE_API_KEY=sk-z1UeouLW8hAJ5KVxzXlOJv2FDlhMOrJrxQdwwitN9cKqMY2X
```

- [ ] **Step 3：验证 config 可正常导入**

```bash
python -c "from config import APIMART_IMAGE_API_KEY; print('OK:', bool(APIMART_IMAGE_API_KEY))"
```

预期输出：`OK: True`

- [ ] **Step 4：Commit**

```bash
git add config.py .env
git commit -m "feat(config): add APIMART_IMAGE_API_KEY env var"
```

---

## Task 2：注册 apimart 通道常量

**Files:**
- Modify: `appcore/image_translate_settings.py:17-23`
- Modify: `appcore/gemini_image.py:25-34` (import 块)
- Modify: `appcore/gemini_image.py:62-78` (IMAGE_MODELS_BY_CHANNEL)
- Modify: `appcore/gemini_image.py:228-235` (_channel_provider)
- Test: `tests/test_image_translate_settings.py`
- Test: `tests/test_gemini_image.py`

- [ ] **Step 1：写失败测试（image_translate_settings）**

在 `tests/test_image_translate_settings.py` 末尾追加：

```python
def test_apimart_channel_registered():
    from appcore import image_translate_settings as its
    assert "apimart" in its.CHANNELS
    assert "apimart" in its.CHANNEL_LABELS
    assert its.CHANNEL_LABELS["apimart"] == "APIMART (GPT-Image-2)"
```

- [ ] **Step 2：写失败测试（gemini_image）**

在 `tests/test_gemini_image.py` 末尾追加：

```python
def test_apimart_channel_registered_in_image_models():
    from appcore import gemini_image
    assert "apimart" in gemini_image.IMAGE_MODELS_BY_CHANNEL
    models = gemini_image.IMAGE_MODELS_BY_CHANNEL["apimart"]
    assert len(models) == 1
    assert models[0][0] == "gpt-image-2"


def test_apimart_channel_provider():
    from appcore import gemini_image
    assert gemini_image._channel_provider("apimart") == "apimart"
```

- [ ] **Step 3：运行测试确认失败**

```bash
python -m pytest tests/test_image_translate_settings.py::test_apimart_channel_registered tests/test_gemini_image.py::test_apimart_channel_registered_in_image_models tests/test_gemini_image.py::test_apimart_channel_provider -v
```

预期：3 个 FAILED

- [ ] **Step 4：修改 `image_translate_settings.py` 第 17-23 行**

```python
CHANNELS: tuple[str, ...] = ("aistudio", "cloud", "openrouter", "doubao", "apimart")
CHANNEL_LABELS: dict[str, str] = {
    "aistudio": "Google AI Studio",
    "cloud": "Google Cloud (Vertex AI)",
    "openrouter": "OpenRouter",
    "doubao": "豆包",
    "apimart": "APIMART (GPT-Image-2)",
}
```

- [ ] **Step 5：修改 `gemini_image.py` 第 25-34 行（import 块）**

在现有 `from config import (` 块里追加 `APIMART_IMAGE_API_KEY,`：

```python
from config import (
    APIMART_IMAGE_API_KEY,
    DOUBAO_LLM_API_KEY,
    DOUBAO_LLM_BASE_URL,
    GEMINI_AISTUDIO_API_KEY,
    GEMINI_CLOUD_API_KEY,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    USD_TO_CNY,
    VOLC_API_KEY,
)
```

- [ ] **Step 6：修改 `gemini_image.py` 第 62-78 行（IMAGE_MODELS_BY_CHANNEL）**

在 `"doubao"` 条目后追加 `"apimart"` 条目：

```python
IMAGE_MODELS_BY_CHANNEL: dict[str, list[tuple[str, str]]] = {
    "aistudio": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "cloud": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "openrouter": [
        ("gemini-3.1-flash-image-preview", "Nano Banana 2（快速）"),
        ("gemini-3-pro-image-preview", "Nano Banana Pro（高保真）"),
    ],
    "doubao": [
        ("doubao-seedream-5-0-260128", "Seedream 5.0（豆包）"),
    ],
    "apimart": [
        ("gpt-image-2", "GPT-Image-2"),
    ],
}
```

- [ ] **Step 7：修改 `gemini_image.py` 第 228-235 行（_channel_provider）**

```python
def _channel_provider(channel: str) -> str:
    if channel == "doubao":
        return "doubao"
    if channel == "openrouter":
        return "openrouter"
    if channel == "cloud":
        return "gemini_vertex"
    if channel == "apimart":
        return "apimart"
    return "gemini_aistudio"
```

- [ ] **Step 8：运行测试确认通过**

```bash
python -m pytest tests/test_image_translate_settings.py::test_apimart_channel_registered tests/test_gemini_image.py::test_apimart_channel_registered_in_image_models tests/test_gemini_image.py::test_apimart_channel_provider -v
```

预期：3 个 PASSED

- [ ] **Step 9：Commit**

```bash
git add appcore/image_translate_settings.py appcore/gemini_image.py tests/test_image_translate_settings.py tests/test_gemini_image.py
git commit -m "feat(image-translate): register apimart channel constants"
```

---

## Task 3：实现 `_generate_via_apimart()`

**Files:**
- Modify: `appcore/gemini_image.py` （在 `_generate_via_seedream` 函数之后新增）
- Test: `tests/test_gemini_image.py`

- [ ] **Step 1：写失败测试**

在 `tests/test_gemini_image.py` 末尾追加：

```python
def test_generate_via_apimart_success():
    import time
    from unittest.mock import patch, MagicMock, call
    from appcore import gemini_image

    submit_mock = MagicMock()
    submit_mock.status_code = 200
    submit_mock.json.return_value = {
        "code": 200,
        "data": [{"status": "submitted", "task_id": "task_test_abc"}],
    }

    poll_mock = MagicMock()
    poll_mock.status_code = 200
    poll_mock.json.return_value = {
        "code": 200,
        "data": {
            "status": "completed",
            "result": {"images": [{"url": ["https://example.com/img.png"]}]},
        },
    }

    img_dl_mock = MagicMock()
    img_dl_mock.status_code = 200
    img_dl_mock.content = b"PNG-BYTES"

    def fake_get(url, **kwargs):
        if "tasks" in url:
            return poll_mock
        return img_dl_mock

    with patch("appcore.gemini_image.requests.post", return_value=submit_mock), \
         patch("appcore.gemini_image.requests.get", side_effect=fake_get), \
         patch("appcore.gemini_image.time.sleep"):
        result_bytes, result_mime, raw = gemini_image._generate_via_apimart(
            "翻译这张图",
            b"RAW-IMAGE",
            "image/jpeg",
            api_key="test-key",
        )

    assert result_bytes == b"PNG-BYTES"
    assert result_mime == "image/png"
    assert raw == poll_mock.json.return_value


def test_generate_via_apimart_task_failed():
    from unittest.mock import patch, MagicMock
    from appcore import gemini_image

    submit_mock = MagicMock()
    submit_mock.status_code = 200
    submit_mock.json.return_value = {
        "code": 200,
        "data": [{"status": "submitted", "task_id": "task_fail"}],
    }

    poll_mock = MagicMock()
    poll_mock.status_code = 200
    poll_mock.json.return_value = {
        "code": 200,
        "data": {
            "status": "failed",
            "error": {"message": "content policy violation"},
        },
    }

    with patch("appcore.gemini_image.requests.post", return_value=submit_mock), \
         patch("appcore.gemini_image.requests.get", return_value=poll_mock), \
         patch("appcore.gemini_image.time.sleep"):
        with pytest.raises(gemini_image.GeminiImageError, match="content policy violation"):
            gemini_image._generate_via_apimart(
                "prompt",
                b"RAW",
                "image/png",
                api_key="key",
            )
```

- [ ] **Step 2：运行测试确认失败**

```bash
python -m pytest tests/test_gemini_image.py::test_generate_via_apimart_success tests/test_gemini_image.py::test_generate_via_apimart_task_failed -v
```

预期：2 个 FAILED（`_generate_via_apimart` 不存在）

- [ ] **Step 3：在 `gemini_image.py` 中，在 `_generate_via_seedream` 函数结束后（`return image_bytes, "image/png", resp_json` 行之后）新增如下代码**

首先在文件顶部的 `import` 区域（第 8-16 行附近）添加 `import time`：

```python
import base64
from decimal import Decimal
import io
import logging
import math
import re
import time
from typing import Any
```

然后在 `_generate_via_seedream` 函数结束后（约第 580 行后）插入：

```python
_APIMART_BASE_URL = "https://api.apimart.ai"
_APIMART_POLL_INTERVAL = 5    # 秒
_APIMART_POLL_TIMEOUT = 120   # 秒
_APIMART_INITIAL_WAIT = 15    # 秒，提交后首次等待


def _generate_via_apimart(
    prompt: str,
    source_image: bytes,
    source_mime: str,
    *,
    api_key: str,
) -> tuple[bytes, str, Any]:
    if not api_key:
        raise GeminiImageError(
            "APIMART API key 未配置（请在 .env 中设置 APIMART_IMAGE_API_KEY）"
        )
    mime = source_mime or "image/png"
    b64 = base64.b64encode(source_image).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    payload = {
        "model": "gpt-image-2",
        "prompt": prompt,
        "n": 1,
        "size": "auto",
        "resolution": "1k",
        "image_urls": [data_url],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        submit_resp = requests.post(
            f"{_APIMART_BASE_URL}/v1/images/generations",
            headers=headers,
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        raise GeminiImageRetryable(f"APIMART 提交请求失败：{e}") from e

    try:
        submit_json = submit_resp.json()
    except Exception:
        submit_json = {}

    if submit_resp.status_code != 200 or submit_json.get("code") != 200:
        message = str(submit_json) or f"HTTP {submit_resp.status_code}"
        if submit_resp.status_code in {429, 500, 502, 503, 504}:
            raise GeminiImageRetryable(
                f"APIMART 提交失败（HTTP {submit_resp.status_code}）：{message}"
            )
        raise GeminiImageError(f"APIMART 提交失败：{message}")

    task_id = ((submit_json.get("data") or [{}])[0]).get("task_id")
    if not task_id:
        raise GeminiImageError("APIMART 未返回 task_id")

    time.sleep(_APIMART_INITIAL_WAIT)

    deadline = time.monotonic() + _APIMART_POLL_TIMEOUT
    while True:
        try:
            poll_resp = requests.get(
                f"{_APIMART_BASE_URL}/v1/tasks/{task_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15,
            )
            poll_json = poll_resp.json()
        except requests.RequestException as e:
            raise GeminiImageRetryable(f"APIMART 轮询失败：{e}") from e
        except Exception:
            poll_json = {}

        data = poll_json.get("data") or {}
        status = data.get("status", "")

        if status == "completed":
            image_url_field = ((data.get("result") or {}).get("images") or [{}])[0].get("url") or []
            image_url = image_url_field[0] if isinstance(image_url_field, list) and image_url_field else image_url_field
            if not image_url:
                raise GeminiImageError("APIMART 任务完成但未返回图片 URL")
            try:
                img_resp = requests.get(image_url, timeout=30)
            except requests.RequestException as e:
                raise GeminiImageRetryable(f"APIMART 图片下载失败：{e}") from e
            if img_resp.status_code != 200:
                raise GeminiImageError(
                    f"APIMART 图片下载失败（HTTP {img_resp.status_code}）"
                )
            return img_resp.content, "image/png", poll_json

        if status == "failed":
            error_msg = (data.get("error") or {}).get("message") or "unknown error"
            raise GeminiImageError(f"APIMART 任务失败：{error_msg}")

        if time.monotonic() > deadline:
            raise GeminiImageRetryable(
                f"APIMART 任务超时（>{_APIMART_POLL_TIMEOUT}s，task_id={task_id}）"
            )

        time.sleep(_APIMART_POLL_INTERVAL)
```

- [ ] **Step 4：运行测试确认通过**

```bash
python -m pytest tests/test_gemini_image.py::test_generate_via_apimart_success tests/test_gemini_image.py::test_generate_via_apimart_task_failed -v
```

预期：2 个 PASSED

- [ ] **Step 5：Commit**

```bash
git add appcore/gemini_image.py tests/test_gemini_image.py
git commit -m "feat(image-translate): implement _generate_via_apimart with async polling"
```

---

## Task 4：接入 `generate_image()` 分发块

**Files:**
- Modify: `appcore/gemini_image.py:615-628` （generate_image 内 try 块）
- Test: `tests/test_gemini_image.py`

- [ ] **Step 1：写失败测试**

在 `tests/test_gemini_image.py` 末尾追加：

```python
def test_generate_image_apimart_channel_dispatches_correctly():
    from unittest.mock import patch, MagicMock
    from appcore import gemini_image

    fake_img_bytes = b"APIMART-PNG"
    fake_raw = {"data": {"status": "completed"}}

    with patch.object(gemini_image, "_resolve_channel", return_value="apimart"), \
         patch.object(gemini_image, "APIMART_IMAGE_API_KEY", "test-key"), \
         patch.object(
             gemini_image, "_generate_via_apimart",
             return_value=(fake_img_bytes, "image/png", fake_raw),
         ) as m_gen, \
         patch.object(gemini_image.ai_billing, "log_request") as m_log:
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"RAW",
            source_mime="image/jpeg",
            model="gpt-image-2",
            user_id=7,
            project_id="proj-99",
        )

    assert out == fake_img_bytes
    assert mime == "image/png"
    m_gen.assert_called_once()
    call_kwargs = m_gen.call_args
    assert call_kwargs.kwargs["api_key"] == "test-key"
    log_kwargs = m_log.call_args.kwargs
    assert log_kwargs["provider"] == "apimart"
    assert log_kwargs["model"] == "gpt-image-2"
    assert log_kwargs["success"] is True
    assert log_kwargs["units_type"] == "images"
```

- [ ] **Step 2：运行测试确认失败**

```bash
python -m pytest tests/test_gemini_image.py::test_generate_image_apimart_channel_dispatches_correctly -v
```

预期：FAILED（apimart 分支不存在，走到 gemini else 分支）

- [ ] **Step 3：修改 `generate_image()` 分发块**

找到 `generate_image()` 函数内 `try:` 块起始处（约第 615 行），将当前的：

```python
    try:
        if channel == "doubao":
            api_key, base_url = _resolve_doubao_credentials(user_id)
            image_bytes, mime, resp = _generate_via_seedream(
                prompt=prompt,
                source_image=source_image,
                source_mime=source_mime,
                model_id=model_id,
                api_key=api_key,
                base_url=base_url,
            )
            input_tokens = output_tokens = None
            response_cost_cny = None
        else:
```

改为：

```python
    try:
        if channel == "doubao":
            api_key, base_url = _resolve_doubao_credentials(user_id)
            image_bytes, mime, resp = _generate_via_seedream(
                prompt=prompt,
                source_image=source_image,
                source_mime=source_mime,
                model_id=model_id,
                api_key=api_key,
                base_url=base_url,
            )
            input_tokens = output_tokens = None
            response_cost_cny = None
        elif channel == "apimart":
            image_bytes, mime, resp = _generate_via_apimart(
                prompt,
                source_image,
                source_mime,
                api_key=APIMART_IMAGE_API_KEY,
            )
            input_tokens = output_tokens = None
            response_cost_cny = None
        else:
```

`else:` 之后的所有代码（openrouter / cloud / aistudio）**保持完全不变**。

- [ ] **Step 4：运行测试确认通过**

```bash
python -m pytest tests/test_gemini_image.py::test_generate_image_apimart_channel_dispatches_correctly -v
```

预期：PASSED

- [ ] **Step 5：运行完整 gemini_image 测试套件，确认没有回归**

```bash
python -m pytest tests/test_gemini_image.py tests/test_image_translate_settings.py -v
```

预期：全部 PASSED

- [ ] **Step 6：Commit**

```bash
git add appcore/gemini_image.py tests/test_gemini_image.py
git commit -m "feat(image-translate): wire apimart channel into generate_image dispatch"
```

---

## Task 5：手动验收

- [ ] **Step 1：重启服务**

```bash
python app.py
```

- [ ] **Step 2：进入 Settings 页面，切换图片翻译通道为 "APIMART (GPT-Image-2)"，保存**

预期：通道下拉出现 "APIMART (GPT-Image-2)" 选项，保存成功。

- [ ] **Step 3：上传一张含英文文字的商品图，发起图片翻译任务**

预期：任务完成，输出图片中文字已翻译，图片正常显示。

- [ ] **Step 4：检查任务日志（ai_billing 表），确认 `provider=apimart`、`model=gpt-image-2`、`success=1`**

```sql
SELECT use_case_code, provider, model, success, request_units, units_type
FROM ai_billing
ORDER BY id DESC LIMIT 5;
```

预期：最新一条 `provider=apimart`，`units_type=images`，`success=1`。
