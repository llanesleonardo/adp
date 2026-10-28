from sqlalchemy import select

from app.models import AssistantMessage, EmailCampaign, Exercise, SharedPattern, SystemProblem, User
from app.security import utcnow
from tests.conftest import sign_in


def test_dashboard_shows_personal_activity(client, accounts):
    sign_in(client, accounts["editor"])
    home = client.get("/")
    assert home.status_code == 200
    for heading in ("Recent searches", "Favorites", "Sent email", "Started exercises", "Ask the knowledge assistant", "Newest threads"):
        assert heading in home.text

    assert client.get("/search", params={"q": "cache aside"}).status_code == 200
    assert "cache aside" in client.get("/").text

    created = client.post("/api/v1/documents", json={"title": "Retry pattern", "body": "Try again after a failure.", "publish": True})
    assert created.status_code == 201
    document_id = created.json()["id"]
    assert client.post(f"/documents/{document_id}/favorite", data={"csrf": "x"}, follow_redirects=True).status_code == 200
    assert "Retry pattern" in client.get("/").text

    exercise = client.post("/exercises", data={"title": "Draw a retry timeline", "prompt": "Sketch the waits.", "csrf": "x"}, follow_redirects=True)
    assert exercise.status_code == 200
    assert "Start exercise" in exercise.text
    exercise_id = exercise.url.path.rsplit("/", 1)[-1]
    assert client.post(f"/exercises/{exercise_id}/start", data={"csrf": "x"}, follow_redirects=True).status_code == 200
    assert "Draw a retry timeline" in client.get("/").text

    private = client.post(
        "/api/v1/documents",
        json={"title": "Hidden draft", "body": "DRAFTONLYWORD secret architecture", "publish": False},
    )
    assert private.status_code == 201
    asked = client.post("/ask", data={"question": "Where is retry explained?", "csrf": "x"}, follow_redirects=True)
    assert asked.status_code == 200
    assert "Where is retry explained?" in client.get("/").text
    thread_id = asked.url.path.rsplit("/", 1)[-1]

    sign_in(client, accounts["viewer"])
    viewer_home = client.get("/")
    assert "cache aside" not in viewer_home.text
    assert "Draw a retry timeline" not in viewer_home.text
    assert "Where is retry explained?" not in viewer_home.text
    assert "New private page" not in viewer_home.text
    assert client.get(f"/ask/{thread_id}").status_code == 404
    hidden = client.post("/ask", data={"question": "DRAFTONLYWORD", "csrf": "x"}, follow_redirects=True)
    assert hidden.status_code == 200
    assert "secret architecture" not in hidden.text

    db = client.app.state.session_factory()
    try:
        admin = db.scalar(select(User).where(User.email == accounts["admin"]))
        db.add(
            EmailCampaign(
                subject="Weekly patterns",
                email_type="newsletter",
                summary_version_ids="[]",
                recipient_spec="{}",
                created_by=admin.id,
                sent_by=admin.id,
                status="sent",
                created_at=utcnow(),
                sent_at=utcnow(),
            )
        )
        db.commit()
    finally:
        db.close()

    sign_in(client, accounts["admin"])
    admin_home = client.get("/")
    assert "Weekly patterns" in admin_home.text
    sign_in(client, accounts["editor"])
    editor_home = client.get("/")
    assert "Weekly patterns" not in editor_home.text
    assert "New private page" in editor_home.text


def test_dashboard_lists_favorites_from_the_library(client, accounts):
    sign_in(client, accounts["viewer"])
    db = client.app.state.session_factory()
    try:
        exercise = db.scalar(select(Exercise).where(Exercise.title.contains("Rate Limiter")))
        pattern = db.scalar(select(SharedPattern).where(SharedPattern.name == "Abstract Factory"))
        problem = db.scalar(select(SystemProblem).where(SystemProblem.title == "Rate Limiter"))
    finally:
        db.close()

    assert client.post(f"/exercises/{exercise.id}/favorite", data={"csrf": "x"}, follow_redirects=True).status_code == 200
    assert client.post(
        f"/patterns/{pattern.category_slug}/{pattern.id}/favorite",
        data={"csrf": "x"},
        follow_redirects=True,
    ).status_code == 200
    assert client.post(f"/problems/{problem.id}/favorite", data={"csrf": "x"}, follow_redirects=True).status_code == 200
    asked = client.post("/ask", data={"question": "How does a token bucket work?", "csrf": "x"}, follow_redirects=True)
    assert asked.status_code == 200
    thread_id = asked.url.path.rsplit("/", 1)[-1]
    db = client.app.state.session_factory()
    try:
        message = db.scalar(
            select(AssistantMessage).where(
                AssistantMessage.thread_id == int(thread_id),
                AssistantMessage.role == "assistant",
            )
        )
    finally:
        db.close()
    saved = client.post(
        f"/ask/{thread_id}/messages/{message.id}/favorite",
        data={"csrf": "x"},
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert "Favorited" in saved.text

    home = client.get("/")
    assert f'href="/exercises/{exercise.id}"' in home.text
    assert f'href="/patterns/{pattern.category_slug}/{pattern.id}"' in home.text
    assert f'href="/problems/{problem.id}"' in home.text
    assert f"#message-{message.id}" in home.text
    for kind in ("Exercise", "Pattern", "Problem", "Answer"):
        assert kind in home.text

    sign_in(client, accounts["editor"])
    assert f'href="/patterns/{pattern.category_slug}/{pattern.id}"' not in client.get("/").text

    sign_in(client, accounts["viewer"])
    removed = client.post(
        f"/patterns/{pattern.category_slug}/{pattern.id}/favorite",
        data={"csrf": "x"},
        follow_redirects=True,
    )
    assert "Favorited" not in removed.text
    assert f'href="/patterns/{pattern.category_slug}/{pattern.id}"' not in client.get("/").text
