"""Service 单元测试 — B 子系统：新品审核。

本地无 MySQL；全部用 monkeypatch mock appcore.db.query / query_one / execute。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

import appcore.new_product_review as npr


# ===========================================================================
# Task 11: update_product 能写 npr_* 字段
# ===========================================================================

def test_update_product_writes_npr_fields(monkeypatch):
    """update_product 白名单包含所有 npr_* 字段，JSON 字段自动序列化。"""
    captured = {}

    def fake_execute(sql, args=()):
        captured["sql"] = sql
        captured["args"] = args
        return 1

    monkeypatch.setattr("appcore.medias.execute", fake_execute)

    from appcore import medias
    medias.update_product(
        42,
        npr_decision_status="approved",
        npr_decided_countries=["US", "DE"],
        npr_decided_at="2026-04-28 10:00:00",
        npr_decided_by=1,
        npr_rejected_reason=None,
        npr_eval_clip_path="/tmp/clip.mp4",
    )

    sql = captured["sql"]
    args = captured["args"]

    assert "npr_decision_status" in sql
    assert "npr_decided_countries" in sql
    assert "npr_decided_at" in sql
    assert "npr_decided_by" in sql
    assert "npr_eval_clip_path" in sql

    # npr_decided_countries list → JSON string
    args_list = list(args)
    idx = [i for i, a in enumerate(args_list) if isinstance(a, str) and "US" in a]
    assert idx, f"npr_decided_countries not serialized to JSON string; args={args_list}"
    assert json.loads(args_list[idx[0]]) == ["US", "DE"]


# ===========================================================================
# Task 12: list_pending 测试
# ===========================================================================

def _make_product(**overrides):
    base = {
        "id": 1,
        "name": "Test Product",
        "product_code": "P001",
        "product_link": "https://example.com",
        "main_image": None,
        "translator_id": 10,
        "translator_name": "Alice",
        "cover_object_key": "key/cover.jpg",
        "mk_id": 123,
        "ai_score": 85.0,
        "ai_evaluation_result": "适合推广",
        "ai_evaluation_detail": None,
        "npr_decision_status": None,
        "npr_decided_countries": None,
        "npr_decided_at": None,
        "npr_eval_clip_path": None,
        "created_at": "2026-04-01 10:00:00",
        "updated_at": "2026-04-01 10:00:00",
    }
    base.update(overrides)
    return base


def test_list_pending_filters_by_mk_id(monkeypatch):
    """SQL 中必须有 mk_id IS NOT NULL 约束。"""
    captured_sql = []

    def fake_query(sql, args=()):
        captured_sql.append(sql)
        return []

    monkeypatch.setattr("appcore.new_product_review.query", fake_query)
    npr.list_pending()
    assert captured_sql, "query not called"
    assert "mk_id IS NOT NULL" in captured_sql[0]


def test_list_pending_excludes_approved(monkeypatch):
    """npr_decision_status='approved' 不出现在结果中 (SQL 过滤)。"""
    def fake_query(sql, args=()):
        assert "approved" in sql or "npr_decision_status" in sql
        return []

    monkeypatch.setattr("appcore.new_product_review.query", fake_query)
    result = npr.list_pending()
    assert result == []


def test_list_pending_excludes_rejected(monkeypatch):
    """SQL 过滤条件覆盖 rejected 状态。"""
    def fake_query(sql, args=()):
        # 条件：只返回 NULL 或 pending
        assert "npr_decision_status" in sql
        return []

    monkeypatch.setattr("appcore.new_product_review.query", fake_query)
    result = npr.list_pending()
    assert result == []


def test_list_pending_includes_failed_evaluation(monkeypatch):
    """ai_evaluation_result='评估失败' + npr_decision_status NULL → 应出现在列表。"""
    rows = [_make_product(ai_evaluation_result="评估失败", npr_decision_status=None)]

    def fake_query(sql, args=()):
        return rows

    monkeypatch.setattr("appcore.new_product_review.query", fake_query)
    result = npr.list_pending()
    assert len(result) == 1
    assert result[0]["ai_evaluation_result"] == "评估失败"


def test_list_pending_orders_by_created_at_desc(monkeypatch):
    """SQL 包含 ORDER BY created_at DESC。"""
    captured_sql = []

    def fake_query(sql, args=()):
        captured_sql.append(sql)
        return []

    monkeypatch.setattr("appcore.new_product_review.query", fake_query)
    npr.list_pending()
    assert "created_at DESC" in captured_sql[0]


def test_list_pending_deserializes_detail_json(monkeypatch):
    """ai_evaluation_detail JSON 字符串应被解析为 dict。"""
    detail_str = json.dumps({"countries": [{"lang": "de"}]})
    rows = [_make_product(ai_evaluation_detail=detail_str)]

    monkeypatch.setattr("appcore.new_product_review.query", lambda sql, args=(): rows)
    result = npr.list_pending()
    assert isinstance(result[0]["ai_evaluation_detail"], dict)
    assert "countries" in result[0]["ai_evaluation_detail"]


def test_list_pending_deserializes_countries_json(monkeypatch):
    """npr_decided_countries JSON 字符串应被解析为 list。"""
    rows = [_make_product(npr_decided_countries='["US","DE"]')]

    monkeypatch.setattr("appcore.new_product_review.query", lambda sql, args=(): rows)
    result = npr.list_pending()
    assert result[0]["npr_decided_countries"] == ["US", "DE"]


# ===========================================================================
# Task 13: _make_eval_clip_15s + _resolve_translator 测试
# ===========================================================================

def test_make_eval_clip_15s_creates_file(monkeypatch, tmp_path):
    """mock subprocess 成功 → 返回 out_path，不重跑。"""
    monkeypatch.setattr(npr, "EVAL_CLIPS_ROOT", tmp_path / "eval_clips")

    fake_src = tmp_path / "video.mp4"
    fake_src.write_bytes(b"video")

    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation._materialize_media",
        lambda key: fake_src,
    )

    call_count = {"n": 0}

    def fake_run(cmd, **kwargs):
        call_count["n"] += 1
        # 创建产物
        out_path = Path(cmd[-1])
        out_path.write_bytes(b"clip")
        result = MagicMock()
        result.returncode = 0
        result.stderr = b""
        return result

    monkeypatch.setattr("subprocess.run", fake_run)

    item = {"id": 99, "object_key": "key/v.mp4"}
    path1 = npr._make_eval_clip_15s(1, item)
    path2 = npr._make_eval_clip_15s(1, item)  # 复用
    assert call_count["n"] == 1  # 第二次不重跑
    assert path1 == path2
    assert "99_15s.mp4" in path1


def test_make_eval_clip_15s_falls_back_on_ffmpeg_failure(monkeypatch, tmp_path):
    """ffmpeg returncode=1 → fallback 原视频路径。"""
    monkeypatch.setattr(npr, "EVAL_CLIPS_ROOT", tmp_path / "eval_clips")

    fake_src = tmp_path / "video.mp4"
    fake_src.write_bytes(b"video")
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation._materialize_media",
        lambda key: fake_src,
    )

    def fake_run(cmd, **kwargs):
        result = MagicMock()
        result.returncode = 1
        result.stderr = b"error"
        return result

    monkeypatch.setattr("subprocess.run", fake_run)

    path = npr._make_eval_clip_15s(1, {"id": 88, "object_key": "k"})
    assert str(fake_src) == path


def test_make_eval_clip_15s_falls_back_on_ffmpeg_not_found(monkeypatch, tmp_path):
    """ffmpeg 不在 PATH → FileNotFoundError → fallback 原视频。"""
    monkeypatch.setattr(npr, "EVAL_CLIPS_ROOT", tmp_path / "eval_clips")

    fake_src = tmp_path / "video.mp4"
    fake_src.write_bytes(b"video")
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation._materialize_media",
        lambda key: fake_src,
    )
    monkeypatch.setattr("subprocess.run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("ffmpeg")))

    path = npr._make_eval_clip_15s(1, {"id": 77, "object_key": "k"})
    assert str(fake_src) == path


def test_make_eval_clip_15s_reuses_existing(monkeypatch, tmp_path):
    """产物已存在且非空 → 不调 subprocess.run。"""
    clip_root = tmp_path / "eval_clips"
    monkeypatch.setattr(npr, "EVAL_CLIPS_ROOT", clip_root)

    # 预先创建产物
    existing_dir = clip_root / "1"
    existing_dir.mkdir(parents=True)
    existing = existing_dir / "55_15s.mp4"
    existing.write_bytes(b"existing clip")

    run_called = {"n": 0}

    def fake_run(*a, **k):
        run_called["n"] += 1
        return MagicMock(returncode=0, stderr=b"")

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation._materialize_media",
        lambda key: tmp_path / "v.mp4",
    )

    path = npr._make_eval_clip_15s(1, {"id": 55, "object_key": "k"})
    assert run_called["n"] == 0
    assert "55_15s.mp4" in path


def test_resolve_translator_rejects_inactive(monkeypatch):
    """is_active=0 → TranslatorInvalidError。"""
    monkeypatch.setattr(
        "appcore.new_product_review.query_one",
        lambda sql, args: {"id": 5, "username": "u", "role": "user", "permissions": "{}", "is_active": 0},
    )
    with pytest.raises(npr.TranslatorInvalidError, match="inactive"):
        npr._resolve_translator(5)


def test_resolve_translator_rejects_no_can_translate_perm(monkeypatch):
    """permissions JSON 缺 can_translate → TranslatorInvalidError。"""
    monkeypatch.setattr(
        "appcore.new_product_review.query_one",
        lambda sql, args: {"id": 5, "username": "u", "role": "user", "permissions": '{"can_translate": false}', "is_active": 1},
    )
    with pytest.raises(npr.TranslatorInvalidError, match="lacks can_translate"):
        npr._resolve_translator(5)


def test_resolve_translator_accepts_valid(monkeypatch):
    """正常用户 → 返回 dict。"""
    user = {"id": 5, "username": "u", "role": "user", "permissions": '{"can_translate": true}', "is_active": 1}
    monkeypatch.setattr(
        "appcore.new_product_review.query_one",
        lambda sql, args: user,
    )
    result = npr._resolve_translator(5)
    assert result["id"] == 5


def test_resolve_translator_rejects_missing_user(monkeypatch):
    """用户不存在 → TranslatorInvalidError。"""
    monkeypatch.setattr(
        "appcore.new_product_review.query_one",
        lambda sql, args: None,
    )
    with pytest.raises(npr.TranslatorInvalidError, match="not found"):
        npr._resolve_translator(999)


def test_resolve_translator_rejects_zero_id(monkeypatch):
    """translator_id=0 → TranslatorInvalidError。"""
    with pytest.raises(npr.TranslatorInvalidError, match="required"):
        npr._resolve_translator(0)


# ===========================================================================
# Task 14: evaluate_product 测试
# ===========================================================================

def _setup_evaluate_mocks(monkeypatch, product=None, video=None, llm_result=None):
    """统一 mock 评估所需的外部依赖。"""
    if product is None:
        product = _make_product()

    monkeypatch.setattr("appcore.new_product_review._medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation._resolve_product_cover_key",
        lambda pid, p: "cover/key.jpg",
    )
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation._materialize_media",
        lambda key: Path("/tmp/media_file"),
    )
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation._first_english_video",
        lambda pid: video if video is not None else {"id": 10, "object_key": "video/en.mp4"},
    )
    monkeypatch.setattr(
        "appcore.new_product_review._medias.list_enabled_languages_kv",
        lambda: [("de", "German"), ("fr", "French")],
    )
    monkeypatch.setattr(
        "appcore.new_product_review._make_eval_clip_15s",
        lambda pid, item: "/tmp/clip.mp4",
    )
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation.build_prompt",
        lambda product, url, langs: "test prompt",
    )
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation.build_system_prompt",
        lambda: "test system",
    )
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation.build_response_schema",
        lambda langs: {},
    )

    if llm_result is None:
        llm_result = {
            "json": {
                "countries": [
                    {"lang": "de", "country": "Germany", "is_suitable": True, "score": 85.0,
                     "risk_level": "low", "decision": "适合推广", "reason": "good", "suggestions": []},
                    {"lang": "fr", "country": "France", "is_suitable": True, "score": 80.0,
                     "risk_level": "low", "decision": "适合推广", "reason": "good", "suggestions": []},
                ]
            }
        }

    monkeypatch.setattr(
        "appcore.new_product_review.llm_client.invoke_generate",
        lambda *a, **kw: llm_result,
    )
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation.normalize_result",
        lambda raw, langs: {
            "ai_score": 82.5,
            "ai_evaluation_result": "适合推广",
            "countries": raw.get("countries", []) if isinstance(raw, dict) else [],
        },
    )

    update_calls = []
    monkeypatch.setattr(
        "appcore.new_product_review._medias.update_product",
        lambda pid, **fields: update_calls.append((pid, fields)) or 1,
    )
    monkeypatch.setattr(
        "appcore.pushes.resolve_product_page_url",
        lambda lang, product: "https://example.com/product",
    )
    return update_calls


def test_evaluate_product_writes_back_ai_fields(monkeypatch):
    """评估成功 → update_product 含 ai_score / ai_evaluation_result / ai_evaluation_detail / npr_eval_clip_path / npr_decision_status='pending'。"""
    update_calls = _setup_evaluate_mocks(monkeypatch)

    result = npr.evaluate_product(1, actor_user_id=99)

    assert result["status"] == "evaluated"
    assert result["ai_score"] == 82.5
    assert len(update_calls) == 1
    pid, fields = update_calls[0]
    assert pid == 1
    assert fields["ai_score"] == 82.5
    assert fields["ai_evaluation_result"] == "适合推广"
    assert "ai_evaluation_detail" in fields
    assert fields["npr_eval_clip_path"] == "/tmp/clip.mp4"
    assert fields["npr_decision_status"] == "pending"


def test_evaluate_product_handles_llm_failure(monkeypatch):
    """LLM raise → 写'评估失败' + raise EvaluationError。"""
    update_calls = _setup_evaluate_mocks(monkeypatch)
    monkeypatch.setattr(
        "appcore.new_product_review.llm_client.invoke_generate",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("LLM timeout")),
    )

    with pytest.raises(npr.EvaluationError, match="LLM call failed"):
        npr.evaluate_product(1, actor_user_id=99)

    assert any("评估失败" in str(f) for _, f in update_calls)


def test_evaluate_product_no_video_raises(monkeypatch):
    """_first_english_video 返回 None → NoVideoError。"""
    _setup_evaluate_mocks(monkeypatch, video=None)
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation._first_english_video",
        lambda pid: None,
    )

    with pytest.raises(npr.NoVideoError):
        npr.evaluate_product(1, actor_user_id=99)


def test_evaluate_product_product_not_found(monkeypatch):
    """get_product 返回 None → ProductNotFoundError。"""
    monkeypatch.setattr("appcore.new_product_review._medias.get_product", lambda pid: None)

    with pytest.raises(npr.ProductNotFoundError):
        npr.evaluate_product(999, actor_user_id=99)


def test_evaluate_product_preserves_decided_status(monkeypatch):
    """已 approved 的产品不覆盖 npr_decision_status。"""
    product = _make_product(npr_decision_status="approved")
    update_calls = _setup_evaluate_mocks(monkeypatch, product=product)

    npr.evaluate_product(1, actor_user_id=99)

    _, fields = update_calls[0]
    assert "npr_decision_status" not in fields


def test_evaluate_product_preserves_rejected_status(monkeypatch):
    """已 rejected 的产品不覆盖 npr_decision_status。"""
    product = _make_product(npr_decision_status="rejected")
    update_calls = _setup_evaluate_mocks(monkeypatch, product=product)

    npr.evaluate_product(1, actor_user_id=99)

    _, fields = update_calls[0]
    assert "npr_decision_status" not in fields


# ===========================================================================
# Task 15: decide_approve / decide_reject 测试
# ===========================================================================

def _setup_decide_mocks(monkeypatch, product=None, translator=None, task_id=42):
    if product is None:
        product = _make_product(user_id=10, npr_decision_status=None)

    monkeypatch.setattr("appcore.new_product_review._medias.get_product", lambda pid: product)
    monkeypatch.setattr(
        "appcore.new_product_review.query_one",
        lambda sql, args: translator if translator is not None else {
            "id": 10, "username": "alice", "role": "user",
            "permissions": '{"can_translate": true}', "is_active": 1,
        },
    )
    monkeypatch.setattr(
        "appcore.new_product_review.material_evaluation._first_english_video",
        lambda pid: {"id": 10, "object_key": "video/en.mp4"},
    )

    update_product_owner_calls = []
    monkeypatch.setattr(
        "appcore.new_product_review._medias.update_product_owner",
        lambda pid, uid: update_product_owner_calls.append((pid, uid)),
    )

    update_calls = []
    monkeypatch.setattr(
        "appcore.new_product_review._medias.update_product",
        lambda pid, **fields: update_calls.append((pid, fields)) or 1,
    )

    create_task_calls = []
    monkeypatch.setattr(
        "appcore.new_product_review._tasks.create_parent_task",
        lambda **kw: create_task_calls.append(kw) or task_id,
    )

    return update_calls, update_product_owner_calls, create_task_calls


def test_decide_approve_creates_task(monkeypatch):
    """decide_approve 成功 → create_parent_task 被调，返回 task_id。"""
    update_calls, _, task_calls = _setup_decide_mocks(monkeypatch)

    result = npr.decide_approve(1, countries=["DE", "FR"], translator_id=10, actor_user_id=99)

    assert result["task_id"] == 42
    assert result["countries"] == ["DE", "FR"]
    assert len(task_calls) == 1
    assert task_calls[0]["countries"] == ["DE", "FR"]

    # update_product 写了 approved
    _, fields = update_calls[0]
    assert fields["npr_decision_status"] == "approved"


def test_decide_approve_changes_owner_when_translator_differs(monkeypatch):
    """translator_id != product.user_id → update_product_owner 被调。"""
    product = _make_product(user_id=10, npr_decision_status=None)
    product["user_id"] = 10
    update_calls, owner_calls, _ = _setup_decide_mocks(monkeypatch, product=product)

    npr.decide_approve(1, countries=["DE"], translator_id=20, actor_user_id=99)

    assert len(owner_calls) == 1
    assert owner_calls[0] == (1, 20)


def test_decide_approve_skips_owner_change_when_same(monkeypatch):
    """translator_id == product.user_id → 不调 update_product_owner。"""
    product = _make_product(npr_decision_status=None)
    product["user_id"] = 10
    _, owner_calls, _ = _setup_decide_mocks(monkeypatch, product=product)

    npr.decide_approve(1, countries=["DE"], translator_id=10, actor_user_id=99)

    assert len(owner_calls) == 0


def test_decide_approve_no_countries_raises(monkeypatch):
    """countries=[] → ValueError。"""
    _setup_decide_mocks(monkeypatch)
    with pytest.raises(ValueError, match="non-empty"):
        npr.decide_approve(1, countries=[], translator_id=10, actor_user_id=99)


def test_decide_approve_already_approved(monkeypatch):
    """npr_decision_status='approved' → InvalidStateError。"""
    product = _make_product(npr_decision_status="approved")
    _setup_decide_mocks(monkeypatch, product=product)

    with pytest.raises(npr.InvalidStateError, match="already approved"):
        npr.decide_approve(1, countries=["DE"], translator_id=10, actor_user_id=99)


def test_decide_approve_already_rejected(monkeypatch):
    """npr_decision_status='rejected' → InvalidStateError。"""
    product = _make_product(npr_decision_status="rejected")
    _setup_decide_mocks(monkeypatch, product=product)

    with pytest.raises(npr.InvalidStateError, match="already rejected"):
        npr.decide_approve(1, countries=["DE"], translator_id=10, actor_user_id=99)


def test_decide_approve_invalid_translator(monkeypatch):
    """TranslatorInvalidError 透传。"""
    product = _make_product(npr_decision_status=None)
    _setup_decide_mocks(monkeypatch, product=product)

    monkeypatch.setattr(
        "appcore.new_product_review.query_one",
        lambda sql, args: {"id": 10, "username": "u", "role": "user",
                           "permissions": '{"can_translate": false}', "is_active": 1},
    )

    with pytest.raises(npr.TranslatorInvalidError):
        npr.decide_approve(1, countries=["DE"], translator_id=10, actor_user_id=99)


def test_decide_reject_writes_status_and_reason(monkeypatch):
    """decide_reject 成功 → npr_decision_status='rejected' + reason。"""
    product = _make_product(npr_decision_status=None)
    monkeypatch.setattr("appcore.new_product_review._medias.get_product", lambda pid: product)

    update_calls = []
    monkeypatch.setattr(
        "appcore.new_product_review._medias.update_product",
        lambda pid, **fields: update_calls.append((pid, fields)) or 1,
    )

    result = npr.decide_reject(1, reason="产品质量不符合要求，不适合推广", actor_user_id=99)

    assert result["product_id"] == 1
    _, fields = update_calls[0]
    assert fields["npr_decision_status"] == "rejected"
    assert "不符合" in fields["npr_rejected_reason"]


def test_decide_reject_short_reason_raises(monkeypatch):
    """reason 长度 < 10 → ValueError。"""
    product = _make_product(npr_decision_status=None)
    monkeypatch.setattr("appcore.new_product_review._medias.get_product", lambda pid: product)

    with pytest.raises(ValueError, match="10 characters"):
        npr.decide_reject(1, reason="太短", actor_user_id=99)


def test_decide_reject_already_approved(monkeypatch):
    """产品已 approved → InvalidStateError。"""
    product = _make_product(npr_decision_status="approved")
    monkeypatch.setattr("appcore.new_product_review._medias.get_product", lambda pid: product)

    with pytest.raises(npr.InvalidStateError):
        npr.decide_reject(1, reason="这个产品已经上架了还要拒绝", actor_user_id=99)


def test_decide_approve_product_not_found(monkeypatch):
    """product 不存在 → ProductNotFoundError。"""
    monkeypatch.setattr("appcore.new_product_review._medias.get_product", lambda pid: None)

    with pytest.raises(npr.ProductNotFoundError):
        npr.decide_approve(999, countries=["DE"], translator_id=10, actor_user_id=99)
