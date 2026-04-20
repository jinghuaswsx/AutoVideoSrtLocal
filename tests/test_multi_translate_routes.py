import base64
import json
from unittest.mock import patch


def test_list_page_renders(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query", return_value=[]), \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        resp = authed_client_no_db.get("/multi-translate")
    assert resp.status_code == 200
    assert "多语种视频翻译".encode("utf-8") in resp.data


def test_list_filters_by_lang(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query") as m_q, \
         patch("appcore.settings.get_retention_hours", return_value=72), \
         patch("appcore.task_recovery.recover_all_interrupted_tasks"):
        m_q.return_value = []
        authed_client_no_db.get("/multi-translate?lang=de")
    sql = m_q.call_args.args[0]
    assert "type = 'multi_translate'" in sql
    args = m_q.call_args.args[1]
    assert "de" in args


def test_detail_404_for_other_user(authed_client_no_db):
    with patch("web.routes.multi_translate.db_query_one", return_value=None), \
         patch("appcore.task_recovery.recover_project_if_needed"):
        resp = authed_client_no_db.get("/multi-translate/unknown")
    assert resp.status_code == 404


def test_rematch_excludes_default_voice_from_top10(authed_client_no_db):
    state = {
        "target_lang": "de",
        "voice_match_query_embedding": base64.b64encode(b"fake-embedding").decode("ascii"),
    }
    with patch(
        "web.routes.multi_translate.db_query_one",
        return_value={"state_json": json.dumps(state, ensure_ascii=False)},
    ), patch(
        "web.routes.multi_translate.db_execute",
    ), patch(
        "appcore.video_translate_defaults.resolve_default_voice",
        return_value="default-voice-id",
    ), patch(
        "pipeline.voice_embedding.deserialize_embedding",
        return_value="decoded-embedding",
    ), patch(
        "pipeline.voice_match.match_candidates",
        return_value=[{"voice_id": "voice-b", "similarity": 0.91}],
    ) as m_match:
        resp = authed_client_no_db.post(
            "/api/multi-translate/task-1/rematch",
            json={"gender": "female"},
        )

    assert resp.status_code == 200
    assert resp.get_json()["candidates"][0]["voice_id"] == "voice-b"
    assert m_match.call_args.kwargs["exclude_voice_ids"] == {"default-voice-id"}
