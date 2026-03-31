"""
启动入口
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from config import OUTPUT_DIR, UPLOAD_DIR, validate_runtime_config
from web.app import create_app
from web.extensions import socketio

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = create_app()

if __name__ == "__main__":
    validate_runtime_config()
    print("AutoVideoSrt 启动中...")
    print("访问 http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
