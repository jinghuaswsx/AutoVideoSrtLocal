# 2026-04-21 pytest Baseline Failures

本清单记录 `feature/video-translate-av-sync` 在 Phase 8 回归中确认的 26 个 C 类 baseline 失败。

- 归因结论: 与本次视频翻译音画同步 v2 的 Phase 1-7 改动无直接回归关系。
- 处理策略: 本 PR 不修复，下批次按模块分流处理。

| test_id | 根因一句话 | 建议修复方向 | 工作量 S/M/L |
|---|---|---|---|
| `tests/test_appcore_task_state_db.py::test_create_persists_to_db` | `task_state.create()` 落库时把 `expires_at` 固定写成 `NULL`，与测试期待不一致 | 明确 `create()` 是否应立即写入保留期限；若要保留，补 `set_expires_at` 或调整插入 SQL/测试 | S |
| `tests/test_appcore_task_state_db.py::test_create_link_check_persists_to_db_with_null_expires_at` | `display_name` 持久化后的字符与测试断言不一致，属于旧链路文案/编码漂移 | 统一 `create_link_check()` 的 `display_name` 规范，确认数据库连接字符集和测试断言文案 | S |
| `tests/test_compose.py::test_compose_hard_uses_filename_quoted_subtitle_filter_on_windows` | 测试 mock 了 `subprocess.run`，实现却走 `subprocess.call`，导致真实 ffmpeg 访问不存在文件 | 统一 compose 层 ffmpeg 调用入口，测试与实现都走 `_run_ffmpeg` 或同一个 subprocess API | S |
| `tests/test_link_check_gemini.py::test_analyze_image_passes_media_and_schema` | 业务代码传入的 `service` 已改为 use case code，测试仍断言老值 `gemini` | 明确 link-check 新的 LLM 调用契约，更新测试断言或兼容旧字段映射 | S |
| `tests/test_media_tos_bucket_migration.py::test_collect_media_object_references_deduplicates_keys` | `collect_media_object_references()` 已新增 `media_product_detail_images` 查询，测试 mock 未覆盖 | 给迁移测试补齐新表 mock，或让代码对缺表/未命中查询更宽容 | S |
| `tests/test_pipeline_runner.py::test_step_alignment_auto_confirms_when_interactive_review_disabled` | Fake `VoiceLibrary.recommend_voice()` 仍是旧签名，运行时调用已变成 `(user_id, text)` | 统一 `voice_library` 推荐接口签名，并同步测试 fake 实现 | S |
| `tests/test_pipeline_runner.py::test_step_alignment_waits_when_interactive_review_enabled` | 同一处 `recommend_voice()` 签名漂移导致等待态测试失败 | 同上，收敛接口签名并批量更新相关测试桩 | S |
| `tests/test_pipeline_runner.py::test_step_translate_waits_when_interactive_review_enabled` | 测试期待 `translate artifact.layout == "variant_compare"`，当前 artifact 结构已变化 | 明确翻译预览 artifact 的标准结构，再统一前端和测试断言 | M |
| `tests/test_pipeline_runner.py::test_step_translate_populates_both_variants` | 旧测试仍假设基础翻译步骤默认生成 `normal + hook_cta` 两个变体 | 重新确认当前多变体策略；若已降为单变体，更新测试；若仍需双变体，补回实现 | M |
| `tests/test_pipeline_runner.py::test_step_export_populates_variant_capcut_download_urls` | 导出 artifact 当前不是 `variants` map 结构，测试仍按旧多变体格式断言 | 梳理 `export` 产物协议，统一导出页、artifact builder 和测试结构 | M |
| `tests/test_pipeline_runner.py::test_step_export_passes_user_jianying_root_to_capcut_export` | 当前导出链路只覆盖 `normal` 或结构已简化，测试仍要求两个变体都透传 root | 重新定义 CapCut 导出是否仍支持多变体；按结论更新实现或测试 | M |
| `tests/test_pipeline_runner.py::test_step_export_passes_display_name_to_capcut_export` | 与上一条相同，测试对多变体 `draft_title` 透传的预期过旧 | 同步 CapCut 导出参数契约，并批量收敛导出相关测试 | M |
| `tests/test_preview_artifacts.py::test_preview_artifact_builders_cover_all_pipeline_steps` | `build_alignment_artifact()` 当前只输出 `segments`，测试仍要求 `scene_cuts` 在第一个 item | 固化 preview artifact schema，统一 builder、前端渲染和测试样例 | S |
| `tests/test_subtitle_removal_routes.py::test_subtitle_removal_source_artifact_returns_404_for_other_users_task` | 字幕移除路由已改为全局可见，测试仍按“跨用户 404”断言 | 决定字幕移除任务是否应该全局可见；若设计已改，更新权限测试；若未改，补权限校验 | M |
| `tests/test_title_translate_routes.py::test_dashboard_sidebar_places_title_translate_below_fr` | 侧栏 HTML 已不包含测试寻找的 `fr-translate` 锚点，菜单结构发生漂移 | 重新梳理后台侧栏信息架构，更新模板或测试定位方式 | S |
| `tests/test_translate_lab_e2e.py::test_full_pipeline_integration` | `runtime_v2` 的 translate lab 集成链路没有发出 `lab_pipeline_done` 事件 | 单独排查 translate lab v2 的事件发射与尾步骤完成条件 | M |
| `tests/test_tts_duration_loop.py::TestLanguageSpecificRunners::test_de_runner_does_not_override_step_tts` | 测试用函数对象 identity 直接比较，受模块重载/导入路径影响不稳定 | 改成行为级断言，或规范 runtime 模块加载方式，避免比较函数对象地址 | S |
| `tests/test_tts_duration_loop.py::TestLanguageSpecificRunners::test_fr_runner_does_not_override_step_tts` | 同样是函数对象 identity 比较不稳定 | 同上，改测试为行为验证或统一模块加载 | S |
| `tests/test_voice_library.py::test_list_voices_filters_by_user_id` | `VoiceLibrary.list_voices()` 现在默认追加 `language` 参数，测试仍断言老参数列表 | 固化 `voice_library` 查询 API，统一默认语言语义并同步测试 | S |
| `tests/test_web_routes.py::test_subtitle_removal_pages_render` | 字幕移除上传页缺少测试要求的 DOM 钩子，如 `srUploadInput` | 对照页面契约补模板钩子，或更新测试以匹配新上传页实现 | M |
| `tests/test_web_routes.py::test_subtitle_removal_detail_shell_renders_bottom_compare_previews` | 字幕移除详情页不再输出测试要求的底部双列 compare 样式串 | 明确详情页 compare 布局是否仍保留，按最终设计同步模板和测试 | M |
| `tests/test_web_routes.py::test_create_app_triggers_subtitle_removal_recovery` | `create_app()` 启动阶段没有再触发字幕移除恢复逻辑 | 检查 app 启动注册顺序和 recovery 挂载点，补回或更新测试 | S |
| `tests/test_web_routes.py::test_alignment_route_compiles_script_segments` | 对齐接口产出的 `alignment artifact` 结构与测试取值路径不一致 | 与 `build_alignment_artifact()` 一起统一结构，修复相关路由测试 | S |
| `tests/test_web_routes.py::test_segments_route_updates_translate_artifact` | `translate artifact` 中不再有测试期待的 `content` 字段 | 统一翻译 artifact item schema，更新路由构造和测试断言 | S |
| `tests/test_web_routes.py::test_voice_routes_support_crud` | `voice` 路由会传 `language=` 给 `ensure_defaults()`，测试 fake 还是旧签名 | 统一 `voice` 路由和 `VoiceLibrary` fake 的方法签名 | S |
| `tests/test_web_routes.py::test_deploy_route_copies_variant_capcut_project` | 变体部署接口返回 404，现有测试构造的 task ownership / exports 状态与路由预期不一致 | 梳理 `/deploy/capcut` 的权限与变体导出前置条件，补测试夹具或修正路由判定 | M |
