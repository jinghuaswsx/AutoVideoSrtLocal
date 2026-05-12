from appcore.tabcut_selection.models import normalize_goods_row, normalize_video_row
from appcore.tabcut_selection.scoring import score_candidate


def test_normalize_video_row_strips_signed_video_url():
    row = normalize_video_row(
        {
            "videoId": "v1",
            "videoUrl": "https://cdn.example/v1.mp4?auth_key=secret",
            "videoCoverUrl": "cover",
            "videoDesc": "demo",
            "playCount": 100,
            "itemSoldCount": 8,
            "itemList": [{"itemId": "i1", "itemName": "Item", "soldCount": 20}],
        }
    )

    assert row["video_id"] == "v1"
    assert "video_url" not in row
    assert row["primary_item_id"] == "i1"
    assert row["primary_item_name"] == "Item"


def test_normalize_goods_row_extracts_gmv_and_categories():
    row = normalize_goods_row(
        {
            "itemId": "i1",
            "itemName": "Item",
            "categoryLv1Name": "Food",
            "categoryLv2Name": "Drinks",
            "categoryLv3Name": "Powder",
            "soldCount7d": 12,
            "gmvInfo": {"period7d": {"local": 34.5}, "total": {"local": 99}},
            "relatedVideoCount": 7,
            "priceList": [{"local": 2.1}, {"local": 4.5}],
        }
    )

    assert row["item_id"] == "i1"
    assert row["category_l1_name"] == "Food"
    assert row["category_l2_name"] == "Drinks"
    assert row["category_l3_name"] == "Powder"
    assert row["sold_count_7d"] == 12
    assert row["gmv_7d"] == 34.5
    assert row["gmv_total"] == 99
    assert row["price_min"] == 2.1
    assert row["price_max"] == 4.5


def test_score_candidate_prefers_sales_and_revenue_with_explainable_parts():
    score = score_candidate(
        {
            "play_count": 1_000_000,
            "item_sold_count": 100,
            "video_split_sold_count": 50,
            "goods_sold_count_7d": 1000,
            "goods_gmv_7d": 20000,
            "goods_growth_rate_7d": 0.8,
        }
    )

    assert score["score"] > 0
    assert score["parts"]["goods_gmv_7d"] > 0
    assert score["parts"]["goods_sold_count_7d"] > score["parts"]["video_split_sold_count"]
