from appcore.settings import PROJECT_TYPE_LABELS


def test_image_translate_label_present():
    assert PROJECT_TYPE_LABELS.get("image_translate") == "图片翻译"


def test_link_check_label_present():
    assert PROJECT_TYPE_LABELS.get("link_check") == "链接检测"
