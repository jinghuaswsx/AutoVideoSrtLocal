from appcore import ad_material_ai_analysis as svc


def test_project_top_n_is_40():
    assert svc._PROJECT_TOP_N == 40


def test_max_ai_candidates_is_80():
    assert svc._MAX_AI_CANDIDATES == 80


def test_ranking_prompts_target_40_not_20():
    # 文案不能再出现 Top20/Top10，避免 prompt 误导模型只吐 20 个
    import inspect
    assert svc._ranking_prompt  # 函数存在
    text = inspect.getsource(svc._run_ai_ranking)
    assert "Top14" in text or "Top 14" in text
    assert "Top40" in text or "Top 40" in text
    assert "Top10" not in text and "Top20" not in text
