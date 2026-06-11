# 2026-06-11 Release Test Gate

Docs-anchor: docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md#full-suite-gate

## Context

发布前按全量门禁运行 `pytest -q`，广告预警分支的 focused tests 已通过，但全量套件暴露出既有 baseline 失败。用户已授权先修复这些阻断项，再合并 `master` 并发布生产。

## Scope

- 修复测试隔离，禁止 copywriting 单元测试触发本机 MySQL。
- 对齐 subtitle removal runner 测试到当前 `appcore.subtitle_removal_runtime` 运行门禁。
- 对齐 runtime multi-ASR 测试 patch 目标到当前 `_pipeline_runner` 导入点。
- 修复 link-check 路由中绕过 service response builder 的 JSON 响应。

## Verification

- 先逐个运行失败测试文件。
- 修复后重新运行 `pytest -q` 发布门禁。
