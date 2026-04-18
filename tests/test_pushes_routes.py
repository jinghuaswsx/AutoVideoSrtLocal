"""推送管理蓝图骨架测试。"""


def test_pushes_index_requires_login():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    # /pushes 先被 Flask 308 重定向到 /pushes/（strict_slashes），
    # 再被 @login_required 302 重定向到登录页。
    # follow_redirects=True 跟到最终：要么 200 但是登录页（含"登录"），
    # 要么停在 302（登录页本身）。
    resp = client.get("/pushes/", follow_redirects=False)
    # 未登录应该跳转到登录页
    assert resp.status_code in (301, 302)


def test_pushes_index_loads_for_admin(authed_client_no_db):
    resp = authed_client_no_db.get("/pushes/")
    assert resp.status_code == 200
    assert b"\xe6\x8e\xa8\xe9\x80\x81\xe7\xae\xa1\xe7\x90\x86" in resp.data  # "推送管理"
