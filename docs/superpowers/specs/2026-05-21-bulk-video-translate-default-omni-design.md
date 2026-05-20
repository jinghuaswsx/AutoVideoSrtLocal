# 批量素材视频翻译默认切换全能视频翻译设计

最后更新：2026-05-21

## 背景

素材管理的「翻译」按钮会通过 `web.services.media_product_translate.start_product_translation`
创建 `bulk_translate` 父任务，再由 `appcore.bulk_translate_runtime` 按计划创建各类子任务。
旧设计在 `docs/superpowers/specs/2026-04-22-medias-translation-orchestration-design.md`
中规定视频子任务复用 `multi_translate` 流程；后续
`docs/superpowers/specs/2026-05-07-omni-translate-merge-design.md` 引入了
`omni_translate` 的全能视频翻译能力，但当时保留了 `multi_translate` 的生产入口。

本次需求更新为：素材管理以及复用同一批量素材翻译创建逻辑的其他模块，新建视频翻译任务时默认切换为全能视频翻译。

## 范围

- 新建的 `bulk_translate` 视频计划项（`kind=video` / `kind=videos`）创建 `omni_translate` 子项目。
- 任务中心等模块如果通过 `media_product_translate.start_product_translation` 或同一个
  `bulk_translate_runtime.create_bulk_translate_task` 入口创建素材视频翻译任务，自动使用同一切换。
- 父任务、计划项、内容类型仍保持 `bulk_translate` / `videos`，只替换视频子任务 project type 和 runner。
- 历史已创建的 `multi_translate` 子任务不迁移；轮询、回填和详情展示继续兼容既有 `multi_translate` 子任务。
- 单独的 `/multi-translate` 页面和手动创建多语种视频翻译任务不在本次范围内。

## 行为

1. 新建素材视频翻译子任务时，`projects.type` 写入 `omni_translate`。
2. 子任务 state 保持素材回填所需字段：
   - `target_lang`
   - `source_language="en"`
   - `user_specified_source_language=True`
   - `subtitle_*`
   - `medias_context.parent_task_id/product_id/source_raw_id/target_lang`
3. 子任务启动使用 `OmniTranslateRunner`。`plugin_config` 不额外写入子任务 state，由
   `OmniTranslateRunner._resolve_plugin_config()` 沿用全站默认 preset，再回退内置默认配置。
4. 子任务进入选音色时，父任务仍标记计划项为 `awaiting_voice` 并进入人工等待。
5. 任务详情页和投影层识别 `omni_translate` 的选音色入口，跳转到 `/omni-translate/<task_id>`。
6. 子任务完成后，父任务沿用现有视频结果回填逻辑读取 `hard_video` / `final_video` 等结果路径并写回素材库。

## 验证

- `tests/test_bulk_translate_runtime.py` 覆盖新建视频子任务写入 `omni_translate` 并启动 omni runner。
- `tests/test_runner_dispatch.py` 覆盖 omni runner 注册、清理和未注册报错。
- `tests/test_bulk_translate_detail_assets.py` 覆盖任务详情页 `omni_translate` 选音色跳转。
- 运行相关 pytest、Python 编译检查和 `git diff --check`。
