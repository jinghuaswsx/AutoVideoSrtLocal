# tests/test_push_history_link_filter.py
import pytest
from web.routes.pushes import _filter_links_by_lang

def test_filter_links_small_lang():
    """当素材语种为小语种时，应当过滤并仅返回该小语种的匹配链接。"""
    links = [
        "https://newjoyloo.com/products/essential-preparedness-kit-rjc",
        "https://newjoyloo.com/de/products/essential-preparedness-kit-rjc",
        "https://newjoyloo.com/fr/products/essential-preparedness-kit-rjc",
        "https://newjoyloo.com/es/products/essential-preparedness-kit-rjc",
        "https://newjoyloo.com/it/products/essential-preparedness-kit-rjc",
    ]
    
    # 测试 es (西班牙语)
    filtered_es = _filter_links_by_lang(links, "es")
    assert filtered_es == ["https://newjoyloo.com/es/products/essential-preparedness-kit-rjc"]
    
    # 测试 de (德语)
    filtered_de = _filter_links_by_lang(links, "DE")
    assert filtered_de == ["https://newjoyloo.com/de/products/essential-preparedness-kit-rjc"]

    # 测试 sv (瑞典语)
    links_with_sv = links + [
        "https://newjoyloo.com/sv/products/essential-preparedness-kit-rjc"
    ]
    filtered_sv = _filter_links_by_lang(links_with_sv, "sv")
    assert filtered_sv == ["https://newjoyloo.com/sv/products/essential-preparedness-kit-rjc"]

    # 测试 fi (芬兰语)
    links_with_fi = links + [
        "https://newjoyloo.com/fi/products/essential-preparedness-kit-rjc"
    ]
    filtered_fi = _filter_links_by_lang(links_with_fi, "fi")
    assert filtered_fi == ["https://newjoyloo.com/fi/products/essential-preparedness-kit-rjc"]


def test_filter_links_english_default():
    """当素材语种为英语 (en) 时，应当过滤并仅返回不含其他任何小语种标识路径的英语/默认链接。"""
    links = [
        "https://newjoyloo.com/products/essential-preparedness-kit-rjc",  # 默认英语链接
        "https://newjoyloo.com/de/products/essential-preparedness-kit-rjc",
        "https://newjoyloo.com/fr/products/essential-preparedness-kit-rjc",
        "https://newjoyloo.com/es/products/essential-preparedness-kit-rjc",
        "https://newjoyloo.com/sv/products/essential-preparedness-kit-rjc",  # 瑞典语链接
        "https://newjoyloo.com/fi/products/essential-preparedness-kit-rjc",  # 芬兰语链接
    ]
    
    filtered_en = _filter_links_by_lang(links, "en")
    assert filtered_en == ["https://newjoyloo.com/products/essential-preparedness-kit-rjc"]
    
    # 如果列表里有显式的 /en/ 链接
    links_with_en = [
        "https://newjoyloo.com/en/products/essential-preparedness-kit-rjc",
        "https://newjoyloo.com/fr/products/essential-preparedness-kit-rjc",
    ]
    filtered_en_explicit = _filter_links_by_lang(links_with_en, "en")
    assert filtered_en_explicit == ["https://newjoyloo.com/en/products/essential-preparedness-kit-rjc"]


def test_filter_links_fallback_to_original():
    """防呆机制：当过滤后结果为空时，应当退回原始列表。"""
    links = [
        "https://newjoyloo.com/fr/products/essential-preparedness-kit-rjc",
        "https://newjoyloo.com/it/products/essential-preparedness-kit-rjc",
    ]
    
    # 查找不存在的 ja (日语) 链接，因为无 ja，应当退避返回整个列表
    filtered = _filter_links_by_lang(links, "ja")
    assert filtered == links

