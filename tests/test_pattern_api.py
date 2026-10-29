from sqlalchemy import select

from app.models import SharedPattern
from tests.conftest import sign_in


def test_pattern_categories_api_lists_inventory(client, accounts):
    sign_in(client, accounts["viewer"])
    response = client.get("/api/v1/pattern-categories")
    assert response.status_code == 200, response.text
    payload = response.json()
    titles = {row["title"] for row in payload}
    for name in (
        "Architectural Patterns",
        "AI and Agentic Patterns",
        "Azure",
        "Security Patterns",
        "Software Development Frameworks",
    ):
        assert name in titles
    assert all({"slug", "title", "summary", "tone"} <= set(row) for row in payload)
    assert client.get("/api/v1/pattern-categories/missing/patterns").status_code == 404


def test_patterns_in_category_and_detail_api(client, accounts):
    sign_in(client, accounts["viewer"])
    listed = client.get("/api/v1/pattern-categories/architectural-patterns/patterns")
    assert listed.status_code == 200, listed.text
    patterns = listed.json()
    assert patterns
    assert all(row["category_slug"] == "architectural-patterns" for row in patterns)
    assert any(row["name"] == "Microservices" for row in patterns)

    db = client.app.state.session_factory()
    try:
        pattern = db.scalar(select(SharedPattern).where(SharedPattern.category_slug == "architectural-patterns"))
    finally:
        db.close()

    detail = client.get(f"/api/v1/pattern-categories/architectural-patterns/patterns/{pattern.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["id"] == pattern.id
    assert body["name"] == pattern.name
    assert body["category_slug"] == "architectural-patterns"
    assert "source_url" in body
    assert "repository_path" in body
    assert isinstance(body["sources"], list)
    assert isinstance(body["problems"], list)
    assert isinstance(body["concerns"], list)
    assert body["show_concerns"] is True
    assert body["favorite"] is False

    wrong = client.get(f"/api/v1/pattern-categories/azure/patterns/{pattern.id}")
    assert wrong.status_code == 404
