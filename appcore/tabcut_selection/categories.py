from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TabcutGoodsCategory:
    id: str
    label: str
    name: str

    @property
    def source(self) -> str:
        return f"goods_cat_{self.id}"


TARGET_GOODS_CATEGORIES: tuple[TabcutGoodsCategory, ...] = (
    TabcutGoodsCategory("11", "家装建材", "Home Improvement"),
    TabcutGoodsCategory("12", "居家日用", "Home Supplies"),
    TabcutGoodsCategory("13", "家电", "Household Appliances"),
    TabcutGoodsCategory("16", "厨房用品", "Kitchenware"),
    TabcutGoodsCategory("20", "宠物用品", "Pet Supplies"),
    TabcutGoodsCategory("21", "手机与数码", "Phones & Electronics"),
    TabcutGoodsCategory("25", "五金工具", "Tools & Hardware"),
    TabcutGoodsCategory("26", "玩具和爱好", "Toys & Hobbies"),
    TabcutGoodsCategory("27", "汽车与摩托车", "Automotive & Motorcycle"),
)

_BY_ID = {item.id: item for item in TARGET_GOODS_CATEGORIES}
_BY_SOURCE = {item.source: item for item in TARGET_GOODS_CATEGORIES}
_BY_LABEL = {item.label: item for item in TARGET_GOODS_CATEGORIES}
_BY_NAME = {item.name: item for item in TARGET_GOODS_CATEGORIES}


def goods_category_options() -> list[dict[str, str]]:
    return [
        {"id": item.id, "label": item.label, "name": item.name, "source": item.source}
        for item in TARGET_GOODS_CATEGORIES
    ]


def goods_category_for_source(source: str | None) -> TabcutGoodsCategory | None:
    return _BY_SOURCE.get(str(source or "").strip())


def goods_category_source(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text in _BY_SOURCE:
        return text
    category = _BY_ID.get(text) or _BY_LABEL.get(text) or _BY_NAME.get(text)
    return category.source if category else None
