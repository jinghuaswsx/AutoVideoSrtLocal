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

_BY_LOWER = {item.lower(): item for item in TIKTOK_SHOP_US_L1_CATEGORIES}


def normalize_category(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "Other"
    return _BY_LOWER.get(text.lower(), "Other")


def category_options() -> list[dict[str, str]]:
    return [{"value": item, "label": item} for item in TIKTOK_SHOP_US_L1_CATEGORIES]
