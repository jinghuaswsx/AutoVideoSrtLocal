from __future__ import annotations

TIKTOK_SHOP_US_L1_CATEGORIES: tuple[str, ...] = (
    "Automotive & Motorcycle",
    "Baby & Maternity",
    "Beauty & Personal Care",
    "Books, Magazines & Audio",
    "Collectibles",
    "Computers & Office Equipment",
    "Fashion Accessories",
    "Food & Beverages",
    "Furniture",
    "Health",
    "Home Improvement",
    "Home Supplies",
    "Household Appliances",
    "Jewelry Accessories & Derivatives",
    "Kids' Fashion",
    "Kitchenware",
    "Luggage & Bags",
    "Menswear & Underwear",
    "Pet Supplies",
    "Phones & Electronics",
    "Shoes",
    "Sports & Outdoor",
    "Textiles & Soft Furnishings",
    "Tools & Hardware",
    "Toys & Hobbies",
    "Womenswear & Underwear",
    "Other",
)

CATEGORY_ZH_LABELS: dict[str, str] = {
    "Automotive & Motorcycle": "汽车与摩托车",
    "Baby & Maternity": "母婴",
    "Beauty & Personal Care": "美妆个护",
    "Books, Magazines & Audio": "图书杂志与音频",
    "Collectibles": "收藏品",
    "Computers & Office Equipment": "电脑与办公设备",
    "Fashion Accessories": "时尚配饰",
    "Food & Beverages": "食品饮料",
    "Furniture": "家具",
    "Health": "健康",
    "Home Improvement": "家装建材",
    "Home Supplies": "家居用品",
    "Household Appliances": "家用电器",
    "Jewelry Accessories & Derivatives": "珠宝配饰及衍生品",
    "Kids' Fashion": "童装",
    "Kitchenware": "厨房用品",
    "Luggage & Bags": "箱包",
    "Menswear & Underwear": "男装与内衣",
    "Pet Supplies": "宠物用品",
    "Phones & Electronics": "手机与电子产品",
    "Shoes": "鞋类",
    "Sports & Outdoor": "运动户外",
    "Textiles & Soft Furnishings": "家纺软装",
    "Tools & Hardware": "工具与五金",
    "Toys & Hobbies": "玩具与爱好",
    "Womenswear & Underwear": "女装与内衣",
    "Other": "其他/无法判断",
}

_BY_LOWER = {item.lower(): item for item in TIKTOK_SHOP_US_L1_CATEGORIES}


def normalize_category(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "Other"
    return _BY_LOWER.get(text.lower(), "Other")


def category_label_zh(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return CATEGORY_ZH_LABELS.get(text, text)


def category_option(value: object) -> dict[str, str]:
    text = str(value or "").strip()
    return {
        "value": text,
        "label": category_label_zh(text),
        "label_zh": category_label_zh(text),
        "label_en": text,
    }


def category_options() -> list[dict[str, str]]:
    return [category_option(item) for item in TIKTOK_SHOP_US_L1_CATEGORIES]
