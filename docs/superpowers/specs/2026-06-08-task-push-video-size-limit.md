# 任务中心与推送管理视频大小上限

- **日期**：2026-06-08
- **上位锚点**：
  - `AGENTS.md`：文档驱动代码、任务中心/推送管理主题指引、focused pytest 验证规则
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-04-18-push-management-design.md`
  - `docs/superpowers/specs/2026-06-08-targeted-pytest-verification.md`

## 背景

推送端对视频素材大小有硬限制，超过 100 MB 的视频不应进入推送。任务中心生成或手动上传的视频如果超过限制，后续即使文案、封面、链接都齐全，也会在推送阶段失败，返工成本较高。

## 规则

1. 推送视频素材大小上限为 `100 * 1024 * 1024` bytes，界面统一显示为一位小数 MB，例如 `60.3 MB`。
2. 任务中心父任务生成视频后，如果结果超过 100 MB，必须卡住流程：
   - 先进入现有原始视频审核节点，再复用“打回”逻辑回到 `raw_in_progress`。
   - 打回原因提示视频实际大小、100 MB 上限，并建议将码率改到 3000，确保视频控制在 100 MB 以内。
3. 任务中心手动上传结果视频也遵守同一规则：
   - 后端保存并计算真实文件大小后判断，不能只依赖前端文件对象大小。
   - 超限时复用“打回”逻辑，不自动通过审核入库。
4. 任务中心子任务提交最终翻译素材时，也必须检测目标语种视频大小：
   - 准备齐全并进入现有翻译审核节点后，若目标语种视频超过 100 MB，立即复用子任务“打回”逻辑回到 `assigned`。
   - 提交接口需要把实际大小和码率 3000 的处理建议返回给用户。
5. 推送管理点击“推送”打开弹窗时，右侧“推送前质量检查”必须展示视频大小判断：
   - 未超限显示通过和实际大小。
   - 超限显示失败，提醒管理员该视频超过 100 MB，并展示实际大小。
6. 推送管理真正执行推送时必须后端硬拦截超限视频，避免绕过前端。
7. 推送管理列表的素材文件名下方直接显示视频大小，样式为蓝色、加粗、较大字号，便于管理员扫描。

## 2026-06-09 回归修正

AutoPush / OpenAPI 也是素材推送执行链路的一部分，不能只在主 Web `/pushes/api/items/<id>/push` 拦截。

- `/openapi/push-items/by-keys` 和旧 `/openapi/materials/<product_code>/push-payload` 在生成可执行 payload 前必须拒绝超限素材。
- `/openapi/push-items/<item_id>/mark-pushed` 在写成功状态前必须再次拒绝超限素材，避免旧 payload 或外部脚本绕过。
- AutoPush 本地代理 `/api/push-items/<item_id>/push` 和旧 `/api/push/medias` 在 POST 下游前必须根据上游 item 元数据或 payload `videos[].size` 做兜底拦截。
- 拦截统一返回 `video_too_large`、HTTP 413，并带实际大小、100 MB 上限和建议码率。

## 不做范围

- 不改变 500 MB 手动上传接口的传输保护上限；100 MB 是推送可用性上限。
- 不自动转码或压缩视频，只给出码率 3000 的处理建议。
- 不新增数据库字段，继续使用 `media_items.file_size` 和任务事件中的 `new_size` / `result_size`。

## 验证

1. `python3 scripts/pytest_related.py --base origin/master --run`
2. 如 selector 未覆盖，补跑：
   - `pytest tests/test_task_raw_video_processing.py tests/test_tasks_routes.py tests/test_appcore_pushes.py tests/test_pushes_routes.py tests/test_pushes_ui_assets.py -q`
3. 手工检查 `/pushes/`：素材文件名下方显示蓝色加粗视频大小；点击推送弹窗后右侧质量检查可见大小判断。
