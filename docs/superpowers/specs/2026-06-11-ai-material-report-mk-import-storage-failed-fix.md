# AI 素材报告加入素材库 storage_failed 修复

日期：2026-06-11

## 背景

移动端在素材管理的 AI 素材报告里，点击建议视频卡片的 `加入素材库` 后，右下角只提示 `storage_failed`，素材没有成功写入素材库。

## 根因

`AI素材军师` 和 `投放素材AI分析` 的项目报告都复用 `/mk-import/video` 做明空素材入库，但报告里的 `action_items.payload.mk_video_metadata` 只带了 `video_name`、`video_path` 和封面路径等展示字段，没有补齐入库服务必需的 `filename` 与 `mp4_url`。

`appcore.mk_import.import_mk_video()` 收不到 `filename` 时会抛 `StorageError("filename missing in mk_video_metadata")`，前端通用 fetch 又优先显示 `error` 字段，导致用户只看到 `storage_failed`。

## 修复要求

- AI 报告新生成的 `import_mk_video` action 必须补齐 `/mk-import/video` 需要的明空视频入库字段：
  - `filename`
  - `mp4_url`
  - `cover_url`
  - `video_path`
  - `cover_path` / `video_image_path`
  - `duration_seconds`
  - `mk_product_id` / `mk_id` / `mk_product_name`
- 历史项目报告读取时，必须按 `mingkong_materials` 回填旧 action payload，避免旧报告仍然失败。
- 前端错误提示优先显示服务端 `detail`，便于定位真实入库失败原因。

## 验证

- `.venv/bin/python scripts/pytest_related.py --base origin/master --run`
- `.venv/bin/python -m pytest tests/test_mk_import_routes.py tests/test_mk_import_response_service.py -q`
- `.venv/bin/python -m py_compile appcore/ai_material_strategist.py appcore/ad_material_ai_analysis.py`
- `node --check web/static/ai_material_strategist.js`
- `node --check web/static/ad_material_ai_analysis.js`
