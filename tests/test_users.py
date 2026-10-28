from sqlalchemy import select

from app.models import SharedPattern, User
from tests.conftest import PASSWORD, sign_in


def _user_id(client, email: str) -> int:
    db = client.app.state.session_factory()
    try:
        return db.scalar(select(User.id).where(User.email == email))
    finally:
        db.close()


def _pattern_id(client) -> int:
    db = client.app.state.session_factory()
    try:
        return db.scalar(select(SharedPattern.id).where(SharedPattern.name == "Circuit Breaker"))
    finally:
        db.close()


def test_users_list_and_customer_dashboard_are_super_admin_only(client, accounts):
    sign_in(client, accounts["viewer"])
    viewer_id = _user_id(client, accounts["viewer"])
    editor_id = _user_id(client, accounts["editor"])
    assert client.get("/users").status_code == 403
    assert client.get(f"/users/{editor_id}").status_code == 403
    assert 'href="/users"' not in client.get("/").text
    assert client.get("/search", params={"q": "viewer ledger search"}).status_code == 200

    sign_in(client, accounts["editor"])
    assert client.get("/users").status_code == 403
    assert client.get(f"/users/{viewer_id}").status_code == 403
    assert client.get("/search", params={"q": "editor ledger search"}).status_code == 200
    created = client.post(
        "/api/v1/documents",
        json={"title": "Editor private page", "body": "Written by the editor.", "publish": True},
    )
    assert created.status_code == 201
    exercise = client.post(
        "/exercises",
        data={"title": "Editor retry sketch", "prompt": "Sketch the waits.", "csrf": "x"},
        follow_redirects=True,
    )
    assert exercise.status_code == 200
    exercise_id = exercise.url.path.rsplit("/", 1)[-1]
    assert client.post(f"/exercises/{exercise_id}/start", data={"csrf": "x"}, follow_redirects=True).status_code == 200
    assert client.post(
        f"/exercises/{exercise_id}/notes",
        data={"body": "Keep the waits short.", "csrf": "x"},
        follow_redirects=True,
    ).status_code == 200
    asked = client.post("/ask", data={"question": "Where does the editor keep retry notes?", "csrf": "x"}, follow_redirects=True)
    assert asked.status_code == 200
    thread_id = asked.url.path.rsplit("/", 1)[-1]
    built = client.post(
        "/prompts/code",
        data={
            "pattern_id": str(_pattern_id(client)),
            "focus": "editor checkout",
            "code": "function editorPay() { return 1; }",
            "csrf": "x",
        },
        follow_redirects=False,
    )
    assert built.status_code == 303
    use_id = built.headers["location"].rsplit("/", 1)[-1]

    sign_in(client, accounts["admin"])
    directory = client.get("/users")
    assert directory.status_code == 200
    assert "Everyone with an account is here." in directory.text
    assert f'href="/users/{editor_id}"' in directory.text
    assert accounts["viewer"] in directory.text
    assert "Create user" in directory.text
    assert client.get(f"/prompts/runs/{use_id}").status_code == 404
    assert client.get(f"/ask/{thread_id}").status_code == 404

    editor_dash = client.get(f"/users/{editor_id}")
    assert editor_dash.status_code == 200
    assert "Customer dashboard" in editor_dash.text
    assert "editor ledger search" in editor_dash.text
    assert "viewer ledger search" not in editor_dash.text
    assert "Editor retry sketch" in editor_dash.text
    assert "Keep the waits short." in editor_dash.text
    assert "Editor private page" in editor_dash.text
    assert "Where does the editor keep retry notes?" in editor_dash.text
    assert f'href="/users/{editor_id}/prompts/{use_id}"' in editor_dash.text
    assert f'href="/users/{editor_id}/threads/{thread_id}"' in editor_dash.text

    prompt_page = client.get(f"/users/{editor_id}/prompts/{use_id}")
    assert prompt_page.status_code == 200
    assert "function editorPay()" in prompt_page.text
    assert "editor checkout" in prompt_page.text

    thread_page = client.get(f"/users/{editor_id}/threads/{thread_id}")
    assert thread_page.status_code == 200
    assert "Where does the editor keep retry notes?" in thread_page.text
    assert accounts["editor"] in thread_page.text

    viewer_dash = client.get(f"/users/{viewer_id}")
    assert "viewer ledger search" in viewer_dash.text
    assert "editor ledger search" not in viewer_dash.text
    assert "Editor retry sketch" not in viewer_dash.text
    assert client.get(f"/users/{viewer_id}/prompts/{use_id}").status_code == 404
    assert client.get(f"/users/{viewer_id}/threads/{thread_id}").status_code == 404

    sign_in(client, accounts["editor"])
    assert client.get(f"/users/{editor_id}/prompts/{use_id}").status_code == 403
    assert client.get(f"/users/{editor_id}/threads/{thread_id}").status_code == 403


def test_super_admin_can_still_create_a_user_from_the_directory(client, accounts):
    sign_in(client, accounts["admin"])
    created = client.post(
        "/users",
        data={"email": "newperson@library.test", "password": PASSWORD, "role": "VIEWER", "csrf": "x"},
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert "newperson@library.test" in created.text
    person_id = _user_id(client, "newperson@library.test")
    profile = client.get(f"/users/{person_id}")
    assert profile.status_code == 200
    assert "No searches yet." in profile.text
    assert "VIEWER" in profile.text
