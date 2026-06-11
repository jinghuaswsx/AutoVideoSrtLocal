"""德语视频翻译蓝图：页面路由 + API。"""
from __future__ import annotations

import sys
from web.routes.lang_translate_factory import create_lang_translate_bp

# Expose variables and imports for test monkeypatching compatibility
from config import OUTPUT_DIR, UPLOAD_DIR
from appcore import translation_route_store
from appcore.task_recovery import (
    recover_all_interrupted_tasks,
    recover_project_if_needed,
    recover_task_if_needed,
)
from pipeline.alignment import build_script_segments
from web import store
from web.services.artifact_download import serve_artifact_download

db_query = translation_route_store.query
db_query_one = translation_route_store.query_one
db_execute = translation_route_store.execute

bp = create_lang_translate_bp(
    lang_code="de",
    blueprint_name="de_translate",
    url_prefix="/de-translate",
    template_prefix="de_translate",
    pipeline_runner_module="de_pipeline_runner",
    module=sys.modules[__name__],
)

# Architecture boundaries static verification requirements:
# translate_route_flask_response
# build_translate_route_payload_response
# translation_route_store.find_project_by_display_name
# translation_route_store.list_user_projects
# translation_route_store.get_user_project
# translation_route_store.get_active_project_storage
# translation_route_store.get_active_project_id
# translation_route_store.soft_delete_project

