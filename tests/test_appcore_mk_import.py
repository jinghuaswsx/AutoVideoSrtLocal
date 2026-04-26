from appcore import mk_import


def test_normalize_strips_rjc_suffix():
    assert mk_import._normalize_product_code("ABC-DEF-RJC") == "abc-def"
    assert mk_import._normalize_product_code("abc-def-rjc") == "abc-def"


def test_normalize_no_suffix():
    assert mk_import._normalize_product_code("ABC-DEF") == "abc-def"


def test_normalize_mixed_case_rjc():
    assert mk_import._normalize_product_code("ABC-DEF-rjc") == "abc-def"
    assert mk_import._normalize_product_code("ABC-DEF-Rjc") == "abc-def"


def test_normalize_empty_returns_empty():
    assert mk_import._normalize_product_code("") == ""
    assert mk_import._normalize_product_code(None) == ""


def test_exception_classes_exist():
    assert issubclass(mk_import.DuplicateError, mk_import.MkImportError)
    assert issubclass(mk_import.DownloadError, mk_import.MkImportError)
    assert issubclass(mk_import.StorageError, mk_import.MkImportError)
    assert issubclass(mk_import.DBError, mk_import.MkImportError)
