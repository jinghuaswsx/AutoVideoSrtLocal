from appcore import ai_material_strategist as svc


def _row(**overrides):
    base = {
        "product_id": 1,
        "product_code": "demo-rjc",
        "product_name": "Demo",
        "spend_30d": 0,
        "orders_30d": 0,
        "results_30d": 0,
        "ad_count_30d": 0,
        "spend_7d": 0,
        "spend_yesterday": 0,
        "spend_today": 0,
        "orders_7d": 0,
        "revenue_30d": 0,
        "profit_30d": 0,
        "purchase_value_30d": 0,
        "local_material_count": 0,
    }
    base.update(overrides)
    base["true_roas_30d"] = (
        round(base["revenue_30d"] / base["spend_30d"], 4)
        if base["spend_30d"]
        else None
    )
    base["meta_roas_30d"] = (
        round(base["purchase_value_30d"] / base["spend_30d"], 4)
        if base["spend_30d"]
        else None
    )
    return base


def test_strip_rjc_for_mingkong_search_code():
    assert svc.strip_rjc("emergency-choking-relief-kit-rjc") == "emergency-choking-relief-kit"
    assert svc.strip_rjc("demo_RJC") == "demo"
    assert svc.strip_rjc("plain-code") == "plain-code"


def test_score_product_rows_filters_high_roas_low_volume_and_prefers_volume():
    tiny_high_roas = _row(
        product_id=1,
        product_code="tiny-rjc",
        spend_30d=3,
        orders_30d=1,
        revenue_30d=90,
        purchase_value_30d=90,
    )
    strong_volume = _row(
        product_id=2,
        product_code="strong-rjc",
        spend_30d=800,
        spend_7d=180,
        spend_yesterday=45,
        orders_30d=80,
        orders_7d=18,
        revenue_30d=1800,
        purchase_value_30d=1500,
        profit_30d=360,
        results_30d=120,
        ad_count_30d=18,
    )
    moderate = _row(
        product_id=3,
        product_code="moderate-rjc",
        spend_30d=90,
        orders_30d=9,
        revenue_30d=360,
        purchase_value_30d=280,
        profit_30d=80,
        results_30d=15,
        ad_count_30d=4,
    )

    ranked = svc.score_product_rows([tiny_high_roas, moderate, strong_volume], limit=10)

    assert [row["product_id"] for row in ranked] == [2, 3]
    assert "30天消耗有量" in ranked[0]["selection_reasons"]
    assert "真实ROAS较好" in ranked[0]["selection_reasons"]


def test_mk_search_codes_include_stripped_code_first():
    mapping = svc._mk_search_codes(["demo-product-rjc"])

    assert mapping["demo-product-rjc"] == ["demo-product", "demo-product-rjc"]


def test_task_status_group_matches_strategist_dedup_policy():
    assert svc._task_status_group({"status": "blocked"}) == "pending"
    assert svc._task_status_group({"status": "assigned"}) == "in_progress"
    assert svc._task_status_group({"status": "done", "parent_status": "cancelled"}) == "completed"
    assert svc._task_status_group({"status": "assigned", "parent_status": "cancelled"}) == "cancelled"
    assert svc._task_status_group({"status": "cancelled"}) == "cancelled"


def test_existing_active_task_suppresses_duplicate_translation_action():
    product = _row(product_id=10, product_code="demo-rjc")
    blocking_task = {
        "task_id": 44,
        "country_code": "DE",
        "lang": "de",
        "status_group": "in_progress",
        "status_label": "进行中",
        "task_url": "/tasks/detail/44",
    }
    countries = [{
        "country_code": "DE",
        "lang": "de",
        "blocking_task": blocking_task,
        "cancelled_task": None,
        "tasks": [blocking_task],
    }]
    ai_result = {
        "primary_action": "expand_country",
        "country_actions": [{
            "country_code": "DE",
            "lang": "de",
            "action": "expand_country",
            "reason": "DE适合扩量",
        }],
    }

    decorated = svc._decorate_ai_result_with_tasks(ai_result, countries, [blocking_task])
    actions = svc._build_action_items(product, decorated, [], countries)

    assert decorated["primary_action"] == "hold"
    assert decorated["country_actions"][0]["duplicate_suppressed"] is True
    assert decorated["country_actions"][0]["existing_task"]["task_id"] == 44
    assert not [
        item for item in actions
        if item["type"] == "create_translation_task" and item.get("country_code") == "DE"
    ]
    task_actions = [item for item in actions if item["type"] == "view_task"]
    assert task_actions[0]["task_id"] == 44
    assert task_actions[0]["url"] == "/tasks/detail/44"


def test_cancelled_task_keeps_link_and_allows_new_translation_action():
    product = _row(product_id=10, product_code="demo-rjc")
    cancelled_task = {
        "task_id": 45,
        "country_code": "JP",
        "lang": "ja",
        "status_group": "cancelled",
        "status_label": "已取消",
        "task_url": "/tasks/detail/45",
    }
    countries = [{
        "country_code": "JP",
        "lang": "ja",
        "blocking_task": None,
        "cancelled_task": cancelled_task,
        "tasks": [cancelled_task],
    }]
    ai_result = {
        "primary_action": "expand_country",
        "country_actions": [{
            "country_code": "JP",
            "lang": "ja",
            "action": "expand_country",
            "reason": "JP可重新测试",
        }],
    }

    decorated = svc._decorate_ai_result_with_tasks(ai_result, countries, [cancelled_task])
    actions = svc._build_action_items(product, decorated, [], countries)

    assert decorated["country_actions"][0]["cancelled_task"]["task_id"] == 45
    assert any(item["type"] == "view_task" and item["task_id"] == 45 for item in actions)
    create_actions = [
        item for item in actions
        if item["type"] == "create_translation_task" and item.get("country_code") == "JP"
    ]
    assert create_actions
    assert create_actions[0]["target_lang"] == "ja"
