"""声音仓库 blueprint：浏览 elevenlabs_voices + 匹配入口。"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from appcore import medias
from appcore.voice_library_browse import list_filter_options, list_voices

log = logging.getLogger(__name__)
bp = Blueprint("voice_library", __name__, url_prefix="/voice-library")


@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@login_required
def page():
    return render_template("voice_library.html")


@bp.route("/api/filters", methods=["GET"])
@login_required
def api_filters():
    """返回筛选选项。

    - 不带 language：只回 languages + genders，label 类选项为空数组（前端选语种后再拉）。
    - 带 language：额外把 list_filter_options(language=...) 的 use_cases/accents/ages/descriptives 合并回去。
    """
    language = (request.args.get("language") or "").strip().lower()
    languages = [
        {"code": code, "name_zh": name_zh}
        for code, name_zh in medias.list_enabled_languages_kv()
    ]
    payload = {
        "languages": languages,
        "genders": ["male", "female"],
        "use_cases": [],
        "accents": [],
        "ages": [],
        "descriptives": [],
    }
    if language:
        payload.update(list_filter_options(language=language))
    return jsonify(payload)


def _split_csv(raw):
    if not raw:
        return []
    return [x for x in (s.strip() for s in raw.split(",")) if x]


@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    language = (request.args.get("language") or "").strip().lower()
    if not language:
        return jsonify({"error": "language is required"}), 400
    try:
        result = list_voices(
            language=language,
            gender=(request.args.get("gender") or "").strip() or None,
            use_cases=_split_csv(request.args.get("use_case")),
            accents=_split_csv(request.args.get("accent")),
            ages=_split_csv(request.args.get("age")),
            descriptives=_split_csv(request.args.get("descriptive")),
            q=(request.args.get("q") or "").strip() or None,
            page=int(request.args.get("page") or 1),
            page_size=int(request.args.get("page_size") or 48),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)
