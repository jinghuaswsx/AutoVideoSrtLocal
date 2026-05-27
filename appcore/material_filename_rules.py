"""素材视频文件名命名规范。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
import os
import re
from typing import Any


_DATE_RE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})$")
_COMPACT_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_LOCALIZED_MARKER = "补充素材"
_LOCALIZED_TRANSLATED_MARKER = "小语种翻译素材"
_LOCALIZED_ASSIGNMENT_TAIL_RE = re.compile(r"^(.*\))-(\S+)-蔡靖华\.mp4$")
_LOCALIZED_TRANSLATED_TAIL_RE = re.compile(r"^(.*\))-(\d{8}\S*)-蔡靖华\.mp4$")
_LOCALIZED_MID_MARKER = "-原素材-补充素材"
_LOCALIZED_TRANSLATED_MID_MARKER = "-原素材-小语种翻译素材"
_LOCALIZED_SLOT_LANG_RE = re.compile(r"^[A-Ga-g]?\(")
_SOURCE_ASSIGNEE_RE = re.compile(r"-指派-([^-.\s]+)")
FILENAME_SPACE_ERROR = "文件名不能包含空格"


@dataclass(frozen=True)
class MaterialFilenameValidation:
    ok: bool
    errors: tuple[str, ...]
    effective_lang: str
    suggested_filename: str | None = None


def validate_material_filename(
    filename: str,
    product_name: str,
    lang_code: str = "en",
    languages: Mapping[str, str] | Sequence[Mapping[str, Any]] | None = None,
) -> MaterialFilenameValidation:
    """Validate edit-page material filename rules."""
    filename = _basename(filename).replace(" ", "")
    product_name = (product_name or "").strip()
    lang_map = _normalize_languages(languages)
    effective_lang = resolve_material_filename_lang(filename, lang_code, lang_map)

    space_errors = validate_video_filename_no_spaces(filename)
    if space_errors:
        errors = list(space_errors)
    elif effective_lang == "en":
        errors = _validate_simple_filename(filename, product_name)
    else:
        errors = _validate_localized_filename(filename, product_name, effective_lang, lang_map)
    suggestion = None
    if errors and product_name and product_name in filename:
        suggestion = build_suggested_material_filename(
            filename,
            product_name,
            effective_lang,
            lang_map,
        )

    return MaterialFilenameValidation(
        ok=not errors,
        errors=tuple(errors),
        effective_lang=effective_lang,
        suggested_filename=suggestion,
    )


def validate_initial_material_filename(
    filename: str,
    product_name: str,
    lang_code: str = "en",
    languages: Mapping[str, str] | Sequence[Mapping[str, Any]] | None = None,
) -> MaterialFilenameValidation:
    """Validate the add-product first-screen rule: YYYY.MM.DD-产品名-xxxxx.mp4."""
    filename = _basename(filename).replace(" ", "")
    product_name = (product_name or "").strip()
    lang_map = _normalize_languages(languages)
    effective_lang = resolve_material_filename_lang(filename, lang_code, lang_map)
    space_errors = validate_video_filename_no_spaces(filename)
    errors = list(space_errors) if space_errors else _validate_simple_filename(filename, product_name)
    suggestion = None
    if errors and product_name:
        suggestion = build_initial_suggested_material_filename(filename, product_name)
    return MaterialFilenameValidation(
        ok=not errors,
        errors=tuple(errors),
        effective_lang=effective_lang,
        suggested_filename=suggestion,
    )


def validate_video_filename_no_spaces(filename: str) -> tuple[str, ...]:
    """Return an error when the client-facing video filename contains ASCII spaces."""
    return (FILENAME_SPACE_ERROR,) if " " in _basename(filename) else ()


def resolve_material_filename_lang(
    filename: str,
    lang_code: str = "en",
    languages: Mapping[str, str] | Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """Use explicit lang unless the English/default slot receives an obvious localized file."""
    requested = (lang_code or "en").strip().lower() or "en"
    if requested != "en":
        return requested

    filename = _basename(filename)
    if _LOCALIZED_MARKER not in filename and _LOCALIZED_TRANSLATED_MARKER not in filename:
        return requested

    lang_map = _normalize_languages(languages)
    for code, name_zh in lang_map.items():
        if code == "en" or not name_zh:
            continue
        if name_zh in filename:
            return code
    return requested


def build_suggested_material_filename(
    filename: str,
    product_name: str,
    lang_code: str,
    languages: Mapping[str, str] | Sequence[Mapping[str, Any]] | None = None,
) -> str:
    filename = _basename(filename)
    product_name = (product_name or "").strip() or "{产品名}"
    lang = (lang_code or "en").strip().lower() or "en"
    lang_map = _normalize_languages(languages)
    date_part = _valid_date_prefix(filename[:10]) or date.today().strftime("%Y.%m.%d")

    if lang == "en":
        return f"{date_part}-{product_name}-素材.mp4".replace(" ", "")

    lang_zh = lang_map.get(lang) or lang
    return f"{date_part}-{product_name}-原素材-补充素材({lang_zh})-指派-蔡靖华.mp4".replace(" ", "")


def build_translated_material_filename(
    filename: str,
    product_name: str,
    lang_code: str,
    languages: Mapping[str, str] | Sequence[Mapping[str, Any]] | None = None,
) -> str:
    filename = _basename(filename)
    product_name = (product_name or "").strip() or "{产品名}"
    lang = (lang_code or "en").strip().lower() or "en"
    if lang == "en":
        return build_suggested_material_filename(filename, product_name, lang, languages)

    lang_map = _normalize_languages(languages)
    lang_zh = lang_map.get(lang) or lang
    today_part = date.today().strftime("%Y.%m.%d")
    source_token = _source_assignment_token(filename)
    return f"{today_part}-{product_name}-原素材-小语种翻译素材({lang_zh})-{source_token}-蔡靖华.mp4".replace(" ", "")


def build_initial_suggested_material_filename(filename: str, product_name: str) -> str:
    filename = _basename(filename)
    product_name = (product_name or "").strip() or "{产品名}"
    date_part = _valid_date_prefix(filename[:10]) or date.today().strftime("%Y.%m.%d")
    return f"{date_part}-{product_name}-素材.mp4".replace(" ", "")


def _validate_simple_filename(filename: str, product_name: str) -> list[str]:
    if not product_name:
        return ["当前产品尚未加载，请重试"]
    if len(filename) < 12 or filename[10] != "-":
        return ['文件名必须是 "YYYY.MM.DD-产品名-xxxxx.mp4" 格式']

    date_str = filename[:10]
    if not _valid_date_prefix(date_str):
        return [f'日期段 "{date_str}" 必须是合法的 YYYY.MM.DD']

    rest = filename[11:]
    product_name_stripped = product_name.replace(" ", "")
    product_prefix = product_name_stripped + "-"
    if not rest.startswith(product_prefix):
        return [f'日期之后必须紧跟 "{product_name}-"']

    tail = rest[len(product_prefix):]
    if not tail:
        return ['产品名之后必须保留一段文件说明，例如 "混剪-李文龙"']
    if not filename.lower().endswith(".mp4"):
        return ['文件扩展名必须是 ".mp4"']
    if tail.lower() == ".mp4":
        return ['产品名之后必须保留一段文件说明，例如 "混剪-李文龙"']
    return []


def _validate_localized_filename(
    filename: str,
    product_name: str,
    lang_code: str,
    languages: Mapping[str, str],
) -> list[str]:
    errors: list[str] = []
    lang_zh = languages.get(lang_code) or ""
    if not lang_zh:
        return [f"未知语种 code='{lang_code}'，无法校验"]
    if not product_name:
        return ["当前产品尚未加载，请重试"]

    if _LOCALIZED_TRANSLATED_MID_MARKER in filename:
        return _validate_translated_localized_filename(filename, product_name, lang_zh)

    head_mid = _strip_localized_tail(filename)
    if head_mid is None:
        return ['结尾必须是 "-{不含空格的指派字段}-蔡靖华.mp4"']

    if len(head_mid) < 11 or head_mid[10] != "-":
        return ['开头必须是 "YYYY.MM.DD-" 格式']

    date_str = head_mid[:10]
    if not _valid_date_prefix(date_str):
        return [f'日期段 "{date_str}" 格式必须是 YYYY.MM.DD']

    rest = head_mid[11:]
    if not rest.endswith(")"):
        return ['在结尾负责人段之前必须紧跟 ")"（常见问题：多了空格、或用了中文全角括号 "）"）']

    mid_start = rest.rfind(_LOCALIZED_MID_MARKER)
    if mid_start < 0:
        return [f'中间必须包含 "{_LOCALIZED_MID_MARKER}(语种中文名)" 或 "{_LOCALIZED_MID_MARKER}A(语种中文名)"（常见问题：多了/少了连字符、或用了全角括号）']

    product_part = rest[:mid_start]
    slot_lang_part = rest[mid_start + len(_LOCALIZED_MID_MARKER) :]
    if not _LOCALIZED_SLOT_LANG_RE.match(slot_lang_part):
        return ['补充素材 后只能接 A-G 字母或直接接半角括号 "("']
    lang_start = 1 if slot_lang_part.startswith("(") else 2
    lang_part = slot_lang_part[lang_start:-1]

    product_name_stripped = product_name.replace(" ", "")
    if product_part != product_name_stripped:
        errors.append(f'商品名不符：文件名写的是 "{product_part}"，应为 "{product_name}"（注意前后不能有空格）')
    if lang_part != lang_zh:
        errors.append(f'语种中文名不符：文件名写的是 "{lang_part}"，应为 "{lang_zh}"')
    return errors


def _validate_translated_localized_filename(
    filename: str,
    product_name: str,
    lang_zh: str,
) -> list[str]:
    errors: list[str] = []
    match = _LOCALIZED_TRANSLATED_TAIL_RE.match(filename)
    if not match:
        return ['结尾必须是 "-YYYYMMDD原负责人-蔡靖华.mp4"']

    head_mid = match.group(1)
    source_token = match.group(2)
    source_date = source_token[:8]
    if not _valid_compact_date_prefix(source_date) or len(source_token) <= 8:
        errors.append('来源字段必须是 "YYYYMMDD原负责人"')

    if len(head_mid) < 11 or head_mid[10] != "-":
        return ['开头必须是 "YYYY.MM.DD-" 格式']

    date_str = head_mid[:10]
    if not _valid_date_prefix(date_str):
        return [f'日期段 "{date_str}" 格式必须是 YYYY.MM.DD']

    rest = head_mid[11:]
    if not rest.endswith(")"):
        return ['在来源字段之前必须紧跟 ")"（常见问题：多了空格、或用了中文全角括号 "）"）']

    mid_start = rest.rfind(_LOCALIZED_TRANSLATED_MID_MARKER)
    if mid_start < 0:
        return [f'中间必须包含 "{_LOCALIZED_TRANSLATED_MID_MARKER}(语种中文名)"（常见问题：多了/少了连字符、或用了全角括号）']

    product_part = rest[:mid_start]
    lang_part = rest[mid_start + len(_LOCALIZED_TRANSLATED_MID_MARKER) :]
    if not (lang_part.startswith("(") and lang_part.endswith(")")):
        return ['小语种翻译素材 后必须直接接半角括号 "(语种中文名)"']

    lang_name = lang_part[1:-1]
    product_name_stripped = product_name.replace(" ", "")
    if product_part != product_name_stripped:
        errors.append(f'商品名不符：文件名写的是 "{product_part}"，应为 "{product_name}"（注意前后不能有空格）')
    if lang_name != lang_zh:
        errors.append(f'语种中文名不符：文件名写的是 "{lang_name}"，应为 "{lang_zh}"')
    return errors


def _strip_localized_tail(filename: str) -> str | None:
    match = _LOCALIZED_ASSIGNMENT_TAIL_RE.match(filename)
    if match:
        return match.group(1)
    return None


def _valid_date_prefix(value: str) -> str | None:
    match = _DATE_RE.match(value or "")
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    return parsed.strftime("%Y.%m.%d")


def _valid_compact_date_prefix(value: str) -> str | None:
    match = _COMPACT_DATE_RE.match(value or "")
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        parsed = date(year, month, day)
    except ValueError:
        return None
    return parsed.strftime("%Y%m%d")


def _source_assignment_token(filename: str) -> str:
    source_date = _valid_date_prefix(filename[:10]) or date.today().strftime("%Y.%m.%d")
    compact_date = source_date.replace(".", "")
    
    # 去除路径和扩展名
    base = os.path.basename(filename.replace("\\", "/"))
    if base.lower().endswith(".mp4"):
        base = base[:-4]
        
    # 作者名就是文件名里最后一个横杠后面的名字
    parts = base.split("-")
    assignee = parts[-1].strip() if parts else "未知"
    
    # 清理空白字符
    assignee = re.sub(r"\s+", "", assignee) or "未知"
    return f"{compact_date}{assignee}"


def _normalize_languages(
    languages: Mapping[str, str] | Sequence[Mapping[str, Any]] | None,
) -> dict[str, str]:
    if languages is None:
        return {"en": "英语"}
    if isinstance(languages, Mapping):
        return {
            str(code or "").strip().lower(): str(name or "").strip()
            for code, name in languages.items()
            if str(code or "").strip()
        }
    normalized: dict[str, str] = {}
    for row in languages:
        code = str(row.get("code") or "").strip().lower()
        if not code:
            continue
        normalized[code] = str(row.get("name_zh") or row.get("name") or "").strip()
    return normalized


def _basename(filename: str) -> str:
    name = (filename or "").replace("\\", "/")
    return os.path.basename(name)
