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


def _synthetic_candidates(n):
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "product_id": i, "product_code": f"P{i}", "product_name": f"name{i}",
            "spend_30d": 1000 - i, "orders_30d": 100 - i, "spend_7d": 200,
            "spend_yesterday": 30, "results_30d": 50, "ad_count_30d": 5,
            "true_roas_30d": 2.0, "meta_roas_30d": 2.0, "profit_30d": 100,
            "score": float(1000 - i), "selection_reasons": ["有量"],
            "local_material_count": 1, "local_material_langs": {}, "delivery_status": "active",
            "effective_breakeven_roas": 1.2,
        })
    return rows


def test_ranking_selects_40_when_ai_covers(monkeypatch):
    monkeypatch.setenv("AD_MATERIAL_AI_ANALYSIS_LLM_SPACING_SECONDS", "0")
    candidates = _synthetic_candidates(80)

    def fake_invoke(use_case_code=None, **kw):
        # 每批回 14 个、final 回 40 个：用 prompt 里的 product_id 还原
        import re
        ids = [int(x) for x in re.findall(r'"product_id":(\d+)', kw["prompt"])]
        stage = (kw.get("billing_extra") or {}).get("stage")
        take = 40 if stage == "final_rank" else 14
        ranked = [{"product_id": pid, "rank": idx + 1} for idx, pid in enumerate(ids[:take])]
        return {"json": {"ranked_products": ranked}, "text": "", "usage_log_id": None}

    monkeypatch.setattr(svc.llm_client, "invoke_generate", fake_invoke)
    ranking = svc._run_ai_ranking(candidates, project_id=1, user_id=None, run_ai=True)
    selected = svc._select_products(candidates, ranking)
    assert len(ranking["selected_product_ids"]) == 40
    assert len(selected) == 40
