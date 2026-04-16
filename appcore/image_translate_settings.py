"""图片翻译默认 prompt 管理，使用 system_settings 表。"""
from __future__ import annotations

from appcore.db import execute, query_one


_KEY_COVER = "image_translate.prompt_cover"
_KEY_DETAIL = "image_translate.prompt_detail"

_DEFAULT_TEMPLATE = (
    "把图中出现的所有文字翻译成 {target_language_name}，"
    "保持原有布局、字体风格、颜色、图像内容不变，"
    "只替换文字本身。对于装饰性排版或特殊字体，尽量保持视觉一致。"
)

_DEFAULTS = {
    "cover": _DEFAULT_TEMPLATE,
    "detail": _DEFAULT_TEMPLATE,
}


def _read(key: str) -> str | None:
    row = query_one("SELECT `value` FROM system_settings WHERE `key`=%s", (key,))
    return (row.get("value") or "") if row else None


def _write(key: str, value: str) -> None:
    execute(
        "INSERT INTO system_settings (`key`, `value`) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE `value`=VALUES(`value`)",
        (key, value),
    )


def get_default_prompts() -> dict[str, str]:
    """返回 {cover, detail} 两条默认 prompt；不存在则写入内置默认后返回。"""
    cover = _read(_KEY_COVER)
    if cover is None:
        _write(_KEY_COVER, _DEFAULTS["cover"])
        cover = _DEFAULTS["cover"]
    detail = _read(_KEY_DETAIL)
    if detail is None:
        _write(_KEY_DETAIL, _DEFAULTS["detail"])
        detail = _DEFAULTS["detail"]
    return {"cover": cover, "detail": detail}


def update_prompt(preset: str, value: str) -> None:
    if preset not in _DEFAULTS:
        raise ValueError("preset must be cover or detail")
    key = _KEY_COVER if preset == "cover" else _KEY_DETAIL
    _write(key, value)


def render_prompt(template: str, *, target_language_name: str) -> str:
    """仅替换 {target_language_name}；其他占位符原样保留。"""
    return template.replace("{target_language_name}", target_language_name)
