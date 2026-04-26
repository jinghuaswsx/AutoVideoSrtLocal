# 多语言 ASR 重构 + 语言污染清理（实施计划）

- 日期：2026-04-26
- 设计文档：[`docs/superpowers/specs/2026-04-26-multilingual-asr-refactor-design.md`](../specs/2026-04-26-multilingual-asr-refactor-design.md)
- 分支：`feature/multilingual-asr-refactor`

## 修订记录（2026-04-26）

砍掉 Cohere（不输出时间戳，无法用）。简化方案：zh→豆包，其他→Scribe v2 强制 language。语言污染兜底改为「删除 + 时间合并」。/settings UI 延后。

**已完成（保留）**：Task 1（包骨架）/ Task 2（Doubao）/ Task 3（Scribe）。

**取消的 task**：Task 4（Cohere）、Task 9（settings UI）、Task 11（Cohere 集成）。

**剩余实施**：
- Task 5（极简版 REGISTRY，只注册 doubao + scribe）
- Task 6（purify：检测 + 删除 + 时间合并；无 fallback 重转）
- Task 7（Router 极简：硬编码路由表，无 settings 覆盖）
- Task 8（pipeline 接入）
- Task 10（全量回归）

下方旧任务划分 §Task 4 / §Task 9 / §Task 11 不再执行。

## 任务划分

每个任务原则上是一个独立 commit，文件级显式列出。

---

### Task 1：依赖与基础结构

**目标**：加 fast-langdetect 依赖；建 `appcore/asr_providers/` 包骨架。

**文件**：
- 修改 `requirements.txt` → 加 `fast-langdetect>=0.2.0,<1.0`
- 新建 `appcore/asr_providers/__init__.py`（空 + 后续 REGISTRY 导出）
- 新建 `appcore/asr_providers/base.py` → BaseASRAdapter / Utterance / WordTimestamp / ASRCapabilities

**验证**：
- `python -c "from appcore.asr_providers.base import BaseASRAdapter, Utterance, ASRCapabilities; print('OK')"`
- `python -c "from fast_langdetect import detect; print(detect('hola mundo'))"`（先 `pip install -r requirements.txt`）

---

### Task 2：迁移豆包 → DoubaoAdapter

**目标**：把 `pipeline/asr.py` 现有逻辑封装为 `DoubaoAdapter`；保留 `pipeline/asr.py` 旧接口作 thin wrapper（为不破坏未涉及调用点）。

**文件**：
- 新建 `appcore/asr_providers/doubao.py`：DoubaoAdapter 类，内部调用 `pipeline/asr.py` 的低层函数（_submit / _poll / _parse），或把它们整体迁过来
- 修改 `pipeline/asr.py` → 仅保留旧函数签名作为 wrapper，内部调用 DoubaoAdapter
- 新建 `tests/test_asr_providers_doubao.py`：mock requests，验证：
  - `transcribe(local_path, language=None)` 调用 storage.upload + 提交 + 轮询 + parse
  - 输出 Utterance 格式正确
  - `capabilities.supports_force_language == False`

**验证**：
- `python -m pytest tests/test_asr_providers_doubao.py tests/test_asr_normalize.py -q`
- 现有调用 `pipeline.asr.transcribe(audio_url)` 仍能工作（旧测试通过）

---

### Task 3：迁移 ElevenLabs → ScribeAdapter

**目标**：把 `pipeline/asr_scribe.py` 现有逻辑封装为 `ScribeAdapter`；旧函数保留 wrapper。`model_id` 通过构造参数注入。

**文件**：
- 新建 `appcore/asr_providers/scribe.py`：ScribeAdapter 类
- 修改 `pipeline/asr_scribe.py` → 旧 `transcribe_local_audio` 保留为 wrapper
- 修改 `tests/test_asr_scribe.py` → 加 ScribeAdapter 直接测试用例（保留旧 case）

**验证**：
- `python -m pytest tests/test_asr_providers_scribe.py tests/test_asr_scribe.py -q`
- `capabilities.supports_force_language == True`、`capabilities.supported_languages == frozenset(["*"])`

---

### Task 4：新增 CohereAdapter

**目标**：新建 Cohere Transcribe 调用，对齐 Utterance 输出。

**文件**：
- 新建 `appcore/asr_providers/cohere.py`：CohereAdapter 类
  - 调 `https://api.cohere.com/v2/transcribe`（**实施时**先用 `WebFetch` 确认确切 endpoint + body schema；docs 页面：`https://docs.cohere.com/reference/transcribe`）
  - 输入：本地文件 → multipart 或 base64（取决于 API）
  - 必填参数：`model="cohere-transcribe-03-2026"`，`language="es"`（force language）
  - 输出解析：把 segments/words 转为 Utterance 列表
- `appcore/llm_provider_configs.py` 加 `cohere_asr` 到 `_KNOWN_PROVIDERS` + `PROVIDER_CREDENTIAL_MAP`
- 新建 `tests/test_asr_providers_cohere.py`：mock requests，验证 force language 透传 + 响应解析

**验证**：
- `python -m pytest tests/test_asr_providers_cohere.py tests/test_llm_providers_dao.py -q`
- 真实 API 集成测试**等用户给 key 后**做（Task 11）

---

### Task 5：ASR Provider Registry

**目标**：在 `appcore/asr_providers/__init__.py` 注册三个 adapter，提供 `build_adapter(provider_code, model_id=None)` 工厂。

**文件**：
- 修改 `appcore/asr_providers/__init__.py`:
  ```python
  REGISTRY: dict[str, type[BaseASRAdapter]] = {
      "doubao_asr": DoubaoAdapter,
      "elevenlabs_tts": ScribeAdapter,
      "cohere_asr": CohereAdapter,
  }

  def build_adapter(provider_code: str, model_id: str | None = None) -> BaseASRAdapter:
      cls = REGISTRY.get(provider_code)
      if cls is None:
          raise ValueError(f"Unknown ASR provider_code: {provider_code}")
      return cls(model_id=model_id)
  ```
- 加 `tests/test_asr_providers_registry.py`：build_adapter 三种 provider 都能造出实例

**验证**：
- `python -m pytest tests/test_asr_providers_registry.py -q`

---

### Task 6：语言污染检测 + 切片

**目标**：实现 `appcore/asr_purify.py`：detect_language / _slice_audio / purify_language / _merge_adjacent。

**文件**：
- 新建 `appcore/asr_purify.py`
- 新建 `tests/test_asr_purify.py`：
  - `detect_language("hola mundo, esto es una prueba") == ("es", >0.5)`
  - `detect_language("OK") is None`（太短）
  - `_too_short_to_judge(<8 字符或<1.5s utt) == True`
  - `purify_language(...)` mock fallback adapter，验证：
    - 全主语言段 → 原样返回
    - 含污染段 → 触发 fallback，文本被替换
    - fallback 二次仍污染 → 该段被删除 + 时间合并到前段
    - fallback 抛错 → 该段被删除
- 新建 `tests/test_asr_purify_slice.py`：mock subprocess，验证 ffmpeg 命令拼接 + tempfile 清理

**验证**：
- `python -m pytest tests/test_asr_purify.py tests/test_asr_purify_slice.py -q`

---

### Task 7：ASR Router

**目标**：实现 `appcore/asr_router.py`：DEFAULT_ROUTE_TABLE + load_route_config + resolve_route + transcribe(audio, source_language)。

**文件**：
- 新建 `appcore/asr_router.py`
- 修改 `appcore/settings.py`（或 `appcore/system_settings.py`，先确认存放位置）：加 `get_asr_route_config()` / `save_asr_route_config()`，存 `system_settings.asr_route_config`
- 新建 `tests/test_asr_router.py`:
  - DEFAULT_ROUTE_TABLE 各语言映射正确
  - settings 覆盖能合并进 effective config
  - resolve_route("zh") 返回 (DoubaoAdapter, CohereAdapter)
  - resolve_route("es") 返回 (CohereAdapter, ScribeAdapter)
  - resolve_route("xx-未知语言") 走兜底
  - transcribe(...) mock primary + fallback，verify purify_language 被调用

**验证**：
- `python -m pytest tests/test_asr_router.py -q`

---

### Task 8：接入 pipeline runtime

**目标**：把 `runtime_omni / runtime / runtime_multi / runtime_de / runtime_fr` 里 `_step_asr` 内部调 `pipeline.asr` / `pipeline.asr_scribe` 的地方，改为调 `asr_router.transcribe(...)`。

**文件**：
- 修改 `appcore/runtime_omni.py`：`_step_asr` 路由分发逻辑 → `asr_router.transcribe(audio_path, source_language)`
- 修改 `appcore/runtime.py::_step_asr`：同上
- 修改 `appcore/runtime_multi.py`：同上
- 修改 `appcore/runtime_de.py` / `runtime_fr.py`：第二语言 ASR（173 行附近的 transcribe_local_audio）走 router；source_language 传 "de" / "fr"

**验证**：
- `python -m pytest tests/test_asr_*.py tests/test_pipeline_*.py tests/test_runtime_*.py -q`
- 所有 ASR 相关测试通过
- 手工 smoke：跑一个简短中文/英文/西语视频任务（先用现有路由表，主 ASR 仍是豆包/Cohere）

---

### Task 9：/settings ASR 路由配置 UI

**目标**：在 `/settings` 加「ASR 路由」tab，表格化编辑。

**文件**：
- 修改 `web/routes/settings.py`：加 `tab=asr_routing` 路由 + GET 渲染 + POST 保存
- 新建 `web/templates/settings_asr_routing.html`（或在现有 settings 模板加 partial）
- 后端 schema 校验：language code 合法（ISO-639-1 或 "auto"）；provider_code 在 REGISTRY 内
- 新建 `tests/test_settings_asr_routing_routes.py`：GET 渲染 / POST 保存 / 恢复默认 / 校验失败

**UI 验收**（参考 `CLAUDE.md` Frontend Design System，全程零紫色）：
- 卡片 `--radius-lg` + `1px solid --border` 白底
- 表格行高 40-44px
- 主按钮 `--accent` 海洋蓝、文字按钮 hover `--bg-muted`
- "新增语言" / "保存" / "恢复默认" 三按钮组

**验证**：
- `python -m pytest tests/test_settings_asr_routing_routes.py -q`
- 本地启动服务 → 浏览器访问 `/settings?tab=asr_routing` → 添加/修改/恢复默认 → 数据库 `system_settings.asr_route_config` 正确更新

---

### Task 10：旧测试回归 + 全量验证

**目标**：跑全量测试，确保没破坏其他模块。

**命令**：
```bash
python -m pytest -q tests/ 2>&1 | tail -50
```

**修复**：任何破坏旧测试的地方就地修复（应该都是 import path 或返回值结构小调整）。

---

### Task 11：服务器集成与 Cohere 真 API 验证（等 key）

**目标**：用户给 Cohere key 后，在服务器上做集成验证。

**步骤**：
1. 用户提供 Cohere API key
2. SSH 服务器，进入 `/settings` 配置 `cohere_asr.api_key`
3. 跑一个西语短视频（带轻微噪声，预期豆包会污染中文那种）：
   - 不开 cohere（只豆包）→ 看 ASR 输出 utterances 含中文
   - 切 cohere primary → 看 ASR 输出无中文
4. 验证 `/settings?tab=asr_routing` 切换 binding 实时生效
5. 监控 `/var/log/autovideosrt-test.log` 中 `[ASR-Purify]` 日志，确认触发次数符合预期

**服务器 pytest**：
```bash
ssh -i C:/Users/admin/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 \
  'cd /opt/autovideosrt-test && git pull && \
   /opt/autovideosrt/venv/bin/pip install -r requirements.txt && \
   systemctl restart autovideosrt-test && sleep 3 && \
   /opt/autovideosrt/venv/bin/python -m pytest tests/test_asr_*.py tests/test_settings_asr_routing_routes.py -q 2>&1 | tail -30'
```

---

## 约束与原则

- **零紫色**：UI 严格遵循 Frontend Design System，hue 200-240
- **Token-first**：所有颜色/尺寸走 CSS 变量
- **Adapter 输出格式严格不变**：Utterance 形如 `{text, start_time, end_time, words: [{text, start_time, end_time, confidence}]}`，pipeline 下游零改动
- **commits**：每个 Task 一个 commit；message 中文 + 简短描述

## Done 判定

- ✅ 全部任务 commits 落到 `feature/multilingual-asr-refactor`
- ✅ `python -m pytest -q tests/` 全绿
- ✅ Cohere adapter 单测通过（API 集成验证延后到拿到 key）
- ✅ /settings 「ASR 路由」tab 在浏览器中正常展示和保存
- ✅ 旧 `pipeline.asr.transcribe(audio_url)` 等 wrapper 仍可用
- ⏳ 等 Cohere key → Task 11 集成验证 → merge to master → 部署
