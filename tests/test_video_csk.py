def test_video_csk_analyze_uses_billing_use_case(tmp_path, monkeypatch):
    from pipeline import video_csk as mod

    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"video")
    captured = {}

    def fake_invoke_generate(use_case, **kwargs):
        captured["use_case"] = use_case
        captured["kwargs"] = kwargs
        return {
            "json": {
                "video_analysis": {
                    "summary": "summary",
                    "detailed_description": "description",
                    "product_features": {"color_desc": "blue", "material_desc": "metal"},
                    "video_text": ["text"],
                    "voiceover": "voice",
                },
                "keyframes": [
                    {"timestamp": "00:01.000", "type": "Hero", "reason": "reason-1"},
                    {"timestamp": "00:02.000", "type": "Detail", "reason": "reason-2"},
                    {"timestamp": "00:03.000", "type": "Usage", "reason": "reason-3"},
                ],
            }
        }

    monkeypatch.setattr("appcore.llm_client.invoke_generate", fake_invoke_generate)

    result = mod.analyze_video(video_path, user_id=3, project_id="proj-3")

    assert result["video_analysis"]["summary"] == "summary"
    assert len(result["keyframes"]) == 3
    assert captured["use_case"] == "video_csk.analyze"
    assert captured["kwargs"]["user_id"] == 3
    assert captured["kwargs"]["project_id"] == "proj-3"
    assert captured["kwargs"]["model_override"] == mod.CSK_MODEL
