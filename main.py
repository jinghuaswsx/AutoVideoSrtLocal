"""
启动入口
"""
import logging
import os
import sys

# 配置日志：确保应用层日志输出到 stderr（gunicorn 可捕获）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)

sys.path.insert(0, os.path.dirname(__file__))

from config import OUTPUT_DIR, UPLOAD_DIR, validate_runtime_config
from web.app import create_app
from web.extensions import socketio

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# 先 apply DB migrations，再把基础设施凭据从 DB 同步到 config/os.environ，
# 最后才校验 required credentials —— 顺序很关键：
#   1) ensure_up_to_date() 建表 + seed infra_credentials 三行
#   2) infra_credentials.sync_to_runtime() 用 DB 值覆盖 .env 兜底（DB 留空时
#      保留 .env 值，平滑过渡），同时清 SDK client 缓存
#   3) validate_runtime_config() 校验最终的 TOS_ACCESS_KEY / TOS_SECRET_KEY
from appcore import db_migrations
db_migrations.ensure_up_to_date()

from appcore import infra_credentials
infra_credentials.sync_to_runtime()

validate_runtime_config()

app = create_app()

from appcore.scheduler import get_scheduler, register_atexit_shutdown
_scheduler = get_scheduler()
_scheduler.start()
# atexit fallback for shutting down APScheduler so its non-daemon thread
# does not block process exit. The Gunicorn worker_exit hook also calls
# shutdown_scheduler; this covers paths that bypass the hook.
register_atexit_shutdown()

if __name__ == "__main__":
    print("AutoVideoSrt 启动中...")
    print("访问 http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
