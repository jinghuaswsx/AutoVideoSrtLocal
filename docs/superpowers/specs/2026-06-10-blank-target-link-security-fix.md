# Blank Target Link Security Fix

## Context

2026-06-10 发布前全量 pytest 在最新 `master` 上触发 `tests/test_blank_target_security.py::test_blank_target_links_use_noopener_noreferrer` 失败。

失败链接位于 `web/templates/medias_mingkong_pairing_workbench.html`，新增的素材管理搜索入口使用 `target="_blank"`，但缺少 `rel="noopener noreferrer"`。

## Requirement

- 所有 `target="_blank"` 链接必须同时包含 `noopener` 和 `noreferrer`。
- 只修复失败链接，不调整页面结构和其他交互。

## Verification

- `python -m pytest tests/test_blank_target_security.py -q`
- 发布前全量 pytest
