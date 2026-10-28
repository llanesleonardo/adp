from tests.conftest import PASSWORD, sign_in


def test_sign_in_page_and_private_editor(client, accounts):
    login = client.get("/login")
    assert login.status_code == 200
    assert "Sign in" in login.text
    blocked = client.get("/", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/login"

    sign_in(client, accounts["editor"])
    home = client.get("/")
    assert home.status_code == 200
    assert "Private library" in home.text
    created = client.post("/documents", data={"title": "Wiki note", "body": "Private **decision**", "csrf": "x"}, follow_redirects=True)
    assert created.status_code == 200
    assert "Private knowledge" in created.text
    assert "Private decision" in created.text or "decision" in created.text
    assert "Create public summary" in created.text

    form_login = client.post("/api/v1/auth/logout")
    assert form_login.status_code == 200
    page = client.post("/login", data={"email": accounts["viewer"], "password": PASSWORD}, follow_redirects=True)
    assert page.status_code == 200
    assert "Private library" in page.text
    assert "New private page" not in page.text
