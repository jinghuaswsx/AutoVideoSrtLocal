from appcore.llm_media_optimizer import OptimizedMedia


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


def test_video_csk_analyze_optimizes_video_before_llm(tmp_path, monkeypatch):
    from pipeline import video_csk as mod

    source = tmp_path / "demo.mp4"
    optimized = tmp_path / "demo.llm.mp4"
    source.write_bytes(b"video")
    optimized.write_bytes(b"small")
    captured = {}

    def fake_prepare(video_path, policy, output_dir=None):
        captured["policy"] = policy
        return OptimizedMedia(
            original_path=str(source),
            llm_path=str(optimized),
            optimized=True,
            cleanup_path=str(optimized),
            original_bytes=5,
            llm_bytes=5,
            command=["ffmpeg"],
            policy_name=policy.name,
        )

    def fake_invoke_generate(use_case, **kwargs):
        captured["kwargs"] = kwargs
        return {
            "json": {
                "video_analysis": {
                    "summary": "summary",
                    "detailed_description": "description",
                    "product_features": {},
                    "video_text": [],
                    "voiceover": "",
                },
                "keyframes": [],
            }
        }

    monkeypatch.setattr(mod, "prepare_video_for_llm", fake_prepare)
    monkeypatch.setattr("appcore.llm_client.invoke_generate", fake_invoke_generate)

    result = mod.analyze_video(source, user_id=3, project_id="proj-3")

    assert captured["policy"].name == "review_480p_audio"
    assert captured["kwargs"]["media"] == [str(optimized)]
    assert result["_video_optimization"]["llm_video_path"] == str(optimized)
