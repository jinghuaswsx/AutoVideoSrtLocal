def test_video_csk_analyze_uses_billing_use_case(tmp_path, monkeypatch):
    from pipeline import video_csk as mod

    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"video")
    captured = {}

    def fake_generate(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return {
            "video_analysis": {
                "summary": "总结",
                "detailed_description": "详细描述",
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

    monkeypatch.setattr(mod.gemini, "generate", fake_generate)

    result = mod.analyze_video(video_path, user_id=3, project_id="proj-3")

    assert result["video_analysis"]["summary"] == "总结"
    assert len(result["keyframes"]) == 3
    assert captured["kwargs"]["service"] == "video_csk.analyze"
    assert captured["kwargs"]["user_id"] == 3
    assert captured["kwargs"]["project_id"] == "proj-3"
