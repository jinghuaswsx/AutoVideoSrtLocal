# 豆包翻译模型接入 & 配置页面按流程重组

**日期**: 2026-04-03  
**状态**: 设计完成，待实施

## 背景

当前翻译功能仅支持通过 OpenRouter 调用 Claude Sonnet。需要：
1. 新接入豆包 2.0 Pro 作为可选翻译模型
2. 每个用户可独立配置和选择自己的翻译大模型
3. 设置页面从"按模型名分块"改为"按流程步骤分块"

## 设计决策

- **接口方式**: 豆包走 OpenAI 兼容接口（`chat/completions`），复用现有 OpenAI SDK，无需新增 HTTP 调用逻辑
- **API Key**: 豆包 ASR 和豆包翻译使用独立的 key，分别配置
- **模型选择层级**: 设置页面配默认模型，任务工作台翻译时可临时覆盖
- **方案**: 最小改动方案，不引入 Provider 抽象层

## 一、配置页面重组

### 分块结构

将现有按模型名分块改为按 pipeline 流程步骤分块：

| 区块 | 标题 | 说明 | 包含服务 |
|------|------|------|---------|
| 1 | 第一步：语音识别 | 将视频中的中文语音转为文字 | 豆包 ASR（API Key / App ID / Cluster） |
| 2 | 第二步：翻译与本土化 | 将中文内容翻译为地道的英文 | 默认模型下拉 + OpenRouter（3字段）+ 豆包翻译（3字段） |
| 3 | 第三步：配音合成 | 生成英文配音音频 | ElevenLabs（API Key） |
| 4 | 第四步：导出 | 导出到剪映项目 | 剪映项目根目录 |

### 翻译与本土化区块详情

包含一个**默认翻译模型**下拉框，选项：
- Claude Sonnet (OpenRouter) — 默认
- 豆包 2.0 Pro

每个模型服务有三个配置项：
- **API Key** — 留空保持当前配置
- **请求 URL** — 预填默认值，用户可自行修改
- **模型 ID** — 预填默认值，用户可自行修改

默认值：

| 服务 | 请求 URL | 模型 ID |
|------|----------|---------|
| OpenRouter | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-4-5` |
| 豆包翻译 | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-2-0-pro-260215` |

## 二、数据存储

复用现有 `api_keys` 表，不改 schema。新增服务行：

| service | key_value | extra_config |
|---------|-----------|-------------|
| `doubao_asr` | ASR 的 key | `{"app_id":"...", "cluster":"..."}` — 不变 |
| `openrouter` | OpenRouter key | `{"base_url":"...", "model_id":"..."}` — extra_config 新增字段 |
| `doubao_llm` | 豆包翻译 key（新增） | `{"base_url":"...", "model_id":"..."}` |
| `elevenlabs` | ElevenLabs key | — 不变 |
| `translate_pref` | `openrouter` 或 `doubao`（新增） | — 存默认模型偏好 |

`base_url` 和 `model_id` 有默认值回退，用户不填则用默认。

## 三、translate.py 调用逻辑

### _get_client 改造

```python
def _get_client(provider: str, user_id: int) -> tuple[OpenAI, str]:
    if provider == "doubao":
        key = resolve_key(user_id, "doubao_llm", "DOUBAO_LLM_API_KEY")
        extra = resolve_extra(user_id, "doubao_llm")
        base_url = extra.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
        model = extra.get("model_id", "doubao-seed-2-0-pro-260215")
    else:  # openrouter
        key = resolve_key(user_id, "openrouter", "OPENROUTER_API_KEY")
        extra = resolve_extra(user_id, "openrouter")
        base_url = extra.get("base_url", "https://openrouter.ai/api/v1")
        model = extra.get("model_id", "anthropic/claude-sonnet-4-5")
    
    return OpenAI(api_key=key, base_url=base_url), model
```

### 关键变更

- `_get_client` 不再缓存全局单例，按 provider + user_id 动态创建
- `_model_name()` 函数废弃，model 从 extra_config 或默认值获取
- `response_format`（JSON Schema 结构化输出）对两个模型都保留
- `extra_body.plugins`（response-healing）仅在 OpenRouter 时传递
- `generate_localized_translation()` 新增 `provider` 和 `user_id` 参数

## 四、任务工作台模型选择

### 前端

在翻译/重新翻译面板中，提示词选择旁新增模型下拉框：
- 选项：`Claude Sonnet (OpenRouter)` / `豆包 2.0 Pro`
- 默认选中用户设置页配的默认模型
- 选择结果随 retranslate 请求一起提交

### 后端

- `POST /api/tasks/<id>/retranslate` 新增参数 `model_provider`（`"openrouter"` / `"doubao"`）
- `PipelineRunner._step_translate()` 从用户偏好读取默认模型
- 两处都传递 provider 给 `generate_localized_translation()`

## 五、涉及文件

| 文件 | 改动 |
|------|------|
| `pipeline/translate.py` | `_get_client` 改造，废弃 `_model_name()`，函数签名新增 provider/user_id |
| `pipeline/runtime.py` | `_step_translate()` 读取用户模型偏好并传递 |
| `config.py` | 新增豆包默认 base_url 和 model_id 常量 |
| `appcore/api_keys.py` | 无改动，复用现有函数 |
| `web/routes/settings.py` | SERVICES 列表新增 `doubao_llm`，处理 base_url/model_id/translate_pref |
| `web/templates/settings.html` | 重组为四个流程区块，新增翻译模型下拉和豆包配置字段 |
| `web/routes/task.py` | retranslate 端点接收 model_provider 参数 |
| `web/templates/_task_workbench_scripts.html` | 翻译面板新增模型下拉，doRetranslate() 传递 model_provider |
