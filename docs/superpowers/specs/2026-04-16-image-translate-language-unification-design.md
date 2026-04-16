# 图片翻译语种来源统一设计

**日期**: 2026-04-16  
**状态**: 待评审  
**范围**: 图片翻译用户侧、图片翻译后台 prompt 配置、图片翻译相关接口校验  

## 1. 背景

`media_languages` 已经是系统里的小语种配置主表，素材管理模块和后台语种设置都围绕它工作。  
但图片翻译模块目前仍然存在“界面部分动态、后端部分静态”的分裂状态：

- 新建图片翻译任务页的目标语言 pills 通过 `/api/languages` 读取，实际来自 `media_languages`
- `/api/image-translate/system-prompts` 仍然用 `appcore.image_translate_settings.SUPPORTED_LANGS` 做静态校验
- 后台 `/admin/api/image-translate/prompts` 也仍然使用静态语种集合
- 后台图片翻译 prompt 配置页面的语言 pills 和语言标签是前端写死的

这会导致两个问题：

- 后台新增或停用小语种后，图片翻译模块不会完全同步，用户页、后台页、接口校验可能出现不一致
- `media_languages` 无法真正成为系统级唯一语种来源，后续模块复用时还会继续复制静态常量

## 2. 目标

- 让 `media_languages` 成为图片翻译模块的唯一目标语言来源
- 统一用户页、后台 prompt 配置页、图片翻译接口的语言集合与校验逻辑
- 保留现有 6 个内置语种 `de/fr/es/it/ja/pt` 的专用默认 prompt 质量
- 支持后台后续新增启用语种后，图片翻译模块无需改代码即可显示并允许使用
- 在系统里只启用 `en` 或没有可用目标语种时，前后端都有明确空态

## 3. 非目标

- 不改图片翻译任务执行流程、模型调用方式、TOS 上传下载流程
- 不改 `media_languages` 表结构
- 不扩展到文案创作、文案翻译、文本翻译、视频翻译等其他模块
- 不在本次引入新的 prompt 编辑版本管理能力

## 4. 设计结论

采用“全模块动态化 + 内置 prompt 保留 + 新语种通用兜底 prompt”的方案。

### 4.1 唯一语种来源

图片翻译模块统一通过 `media_languages` 读取启用语种，规则如下：

- 数据来源：`media_languages`
- 过滤条件：`enabled=1`
- 排序：沿用 `sort_order ASC, code ASC`
- 图片翻译可选目标语言：在启用语种基础上排除 `en`

这套规则同时用于：

- 用户侧目标语言列表
- `/api/image-translate/system-prompts`
- 后台 `/admin/api/image-translate/prompts`
- prompt 保存接口的语言合法性校验

### 4.2 默认 prompt 策略

`appcore.image_translate_settings` 不再把静态语种常量当成“支持语言真相来源”，而只保留两类默认 prompt 能力：

- 内置专用默认 prompt：`de/fr/es/it/ja/pt`
- 通用兜底默认 prompt：任意启用但未内置的其他语种

设计意图：

- 已有 6 个语种继续使用当前专门优化过的 prompt，不影响现有效果
- 新增语种首次使用时也能立即跑通，不会因为没写进常量而报错

### 4.3 通用兜底 prompt

当目标语言不是内置 6 个语种时：

- `cover` 返回一条通用封面图翻译 prompt
- `detail` 返回一条通用详情图翻译 prompt
- prompt 文本在生成时直接注入当前语种的 `name_zh` 和 `code`
- 首次读取时仍写入 `system_settings`，后续管理员可以在后台继续覆盖编辑

示例约束：

- 明确把图片中的英文翻译成当前目标语言
- 保持原图布局、字体风格、颜色、文本层级和非文本视觉元素不变
- 文本变长时允许轻微缩小字体，但不允许溢出
- `cover` 与 `detail` 保持不同的语气和任务说明

这样可以避免新增语种时出现“前端看得到，但 prompt 接口拿不到”的断裂。

## 5. 后端方案

### 5.1 `appcore.image_translate_settings`

新增一个围绕图片翻译的语言读取层，职责拆分如下：

- `list_image_translate_languages()`
  - 读取启用语种
  - 过滤掉 `en`
  - 返回 `{code, name_zh, sort_order}` 列表

- `is_image_translate_language_supported(code)`
  - 基于上面的动态语种列表做校验

- `get_prompt(preset, lang)`
  - 先校验 `preset`
  - 再校验 `lang` 是否在图片翻译可用语言中
  - 若 `system_settings` 已有值，返回已有值
  - 若没有值：
    - 内置 6 语种：写入对应专用默认 prompt
    - 其他启用语种：生成并写入通用兜底 prompt

- `get_prompts_for_lang(lang)`
  - 返回 `{cover, detail}`

- `list_all_prompts()`
  - 遍历当前图片翻译可用语言，返回 `{lang: {cover, detail}}`

这里不再暴露“静态支持语种常量”给路由层做合法性判断。

### 5.2 `web/routes/image_translate.py`

调整点：

- 去掉对 `SUPPORTED_LANGS` 的依赖
- `/api/image-translate/system-prompts`
  - 语言校验改为走 `image_translate_settings` 的动态校验
- `/api/image-translate/upload/complete`
  - 目标语言合法性仍基于 `media_languages.enabled=1`
  - 保持 `en` 不允许作为图片翻译目标语言
- `_target_language_name(code)`
  - 继续从 `media_languages` 查询中文名，作为任务快照写入 `state_json`

用户页的目标语言 pills 继续从 `/api/languages` 获取即可，因为这个接口本身已经来自 `media_languages`。

### 5.3 `web/routes/admin.py`

后台图片翻译 prompt 管理接口统一改为动态语言：

- `GET /admin/api/image-translate/prompts`
  - 返回 `languages` 时改为动态列表
  - 返回 `prompts` 时只包含当前图片翻译可用语言

- `GET /admin/api/image-translate/prompts?lang=xx`
  - `lang` 合法性改为动态校验

- `POST /admin/api/image-translate/prompts`
  - 保存前的 `lang` 校验改为动态校验

这样后台语言 pills 与可保存语言集合保持一致。

## 6. 前端方案

### 6.1 用户侧图片翻译页

现有新建任务页已经通过 `/api/languages` 拉目标语言，主要保留并补足空态：

- 仍然只展示启用且非 `en` 的语言
- 如果返回为空：
  - 不默认选中语言
  - 提示“暂无可用目标语言，请先到系统设置启用小语种”
  - 禁用提交任务

prompt 加载逻辑不变，仍然在切换目标语言时请求 `/api/image-translate/system-prompts?lang=...`。

### 6.2 后台图片翻译 prompt 配置页

后台页面去掉前端硬编码：

- 去掉写死的 `SUPPORTED = ["de","fr","es","it","ja","pt"]`
- 去掉写死的 `LANG_LABELS`
- 改为完全使用 `/admin/api/image-translate/prompts` 返回的 `languages`
- 每个语言 pill 的显示名优先使用接口返回的 `name_zh`

接口建议从原来的：

```json
{
  "languages": ["de", "fr"],
  "presets": ["cover", "detail"],
  "prompts": {
    "de": {"cover": "...", "detail": "..."}
  }
}
```

调整为：

```json
{
  "languages": [
    {"code": "de", "name_zh": "德语"},
    {"code": "fr", "name_zh": "法语"}
  ],
  "presets": ["cover", "detail"],
  "prompts": {
    "de": {"cover": "...", "detail": "..."},
    "fr": {"cover": "...", "detail": "..."}
  }
}
```

这样后台无需自己维护语言映射。

### 6.3 后台空态

当没有可用目标语种时：

- prompt 编辑区不渲染语言 pills
- 两个 textarea 置空并禁用
- 显示提示“当前没有启用的小语种可供图片翻译使用，请先在上方素材语种配置中启用语种”

## 7. 数据流

### 7.1 用户侧新建任务

1. 页面加载时请求 `/api/languages`
2. 前端把启用语种中过滤后的非 `en` 项渲染为目标语言 pills
3. 选择语言后，请求 `/api/image-translate/system-prompts?lang=...`
4. 后端基于动态语言集合返回该语种的 `cover/detail` prompt
5. 用户提交任务时，`upload/complete` 再基于 `media_languages` 校验目标语言是否合法

### 7.2 后台 prompt 配置

1. 页面加载时请求 `/admin/api/image-translate/prompts`
2. 后端动态返回当前可用语种及对应 prompt
3. 前端渲染 pills、标签和编辑区
4. 保存时调用 `POST /admin/api/image-translate/prompts`
5. 后端动态校验该语种当前仍为可用目标语言，校验通过后写入 `system_settings`

## 8. 错误处理

### 8.1 用户侧

- 请求 prompt 时如果传入禁用语种或不存在语种，接口返回 400
- 如果页面加载后系统语种被改动，提交时以后端校验为准，不信任前端旧状态
- 如果没有可用目标语种，前端展示空态，不允许继续提交

### 8.2 后台

- 如果管理员试图保存已停用语种的 prompt，接口返回 400
- 如果语种列表为空，页面进入空态而不是显示残留旧 pills
- 现有已写入 `system_settings` 但当前已停用语种的 prompt 不主动删除，只是不再暴露在编辑列表中

## 9. 测试策略

### 9.1 `tests/test_image_translate_settings.py`

新增或调整覆盖：

- 动态语言列表来自 `media_languages`
- `get_prompt` 对启用语种放行，对未知或停用语种拒绝
- 内置语种缺省时写入专用默认 prompt
- 非内置启用语种缺省时写入通用兜底 prompt
- `list_all_prompts()` 只返回当前图片翻译可用语言

### 9.2 `tests/test_image_translate_routes.py`

新增或调整覆盖：

- `/api/image-translate/system-prompts` 对动态启用语种返回 prompt
- 对停用/未知语种返回 400
- `/api/image-translate/upload/complete` 对启用语种放行，对 `en` 和停用语种拒绝

### 9.3 `tests/test_admin_image_translate_routes.py`

新增或调整覆盖：

- 后台接口返回的 `languages` 来自动态语种列表，而不是静态常量
- `GET /admin/api/image-translate/prompts?lang=...` 对动态语种可用
- `POST /admin/api/image-translate/prompts` 对动态语种可保存
- 非法或停用语种返回 400

### 9.4 前端回归

至少验证：

- 用户侧在存在多种启用语种时能正确渲染目标语言 pills
- 用户侧在无可用目标语种时显示空态并禁用提交
- 后台 prompt 页语言 pills 与后台语种配置保持一致
- 后台新增一个启用语种后，图片翻译页和后台 prompt 页都能立即看到

## 10. 风险与取舍

### 10.1 风险

- 新增语种的通用兜底 prompt 质量不一定达到现有 6 个专用 prompt 的水平
- 后台停用某个语种后，旧任务详情仍会保留该语种的快照名称，这是预期行为

### 10.2 取舍

本次优先保证“系统配置一致性”和“新增语种无需改代码即可可用”。  
对于新增语种的翻译质量，采用“先通用兜底、后后台覆盖”的策略，避免把运营配置能力重新绑回开发发版。

## 11. 实施结果预期

完成后，图片翻译模块会与 `media_languages` 完整打通：

- 用户侧目标语言始终来自后台小语种配置
- 后台 prompt 配置页的语言列表始终与系统启用语种一致
- 图片翻译相关接口不再维护第二套静态语种集合
- 新增启用语种后，用户页和后台页都能立即使用
- 现有 6 个内置语种仍保留当前专用 prompt 质量
