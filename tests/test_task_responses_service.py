from __future__ import annotations

from flask import Flask


def test_task_not_found_response_matches_existing_json_shape():
    from web.services.task_responses import task_not_found_response

    app = Flask(__name__)

    with app.app_context():
        response, status = task_not_found_response()

    assert status == 404
    assert response.get_json() == {"error": "Task not found"}
