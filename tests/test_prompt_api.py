from sqlalchemy import select

from app.models import SharedPattern
from tests.conftest import sign_in


def test_prompt_marketplace_api_lists_builds_and_keeps_uses_private(client, accounts):
    sign_in(client, accounts["viewer"])
    listed = client.get("/api/v1/prompts")
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    titles = {row["title"] for row in payload["prompts"]}
    for title in ("Documentation", "Examples", "Risks", "Diagrams", "Tackle your code"):
        assert title in titles
    assert all("task" not in row for row in payload["prompts"])
    assert payload["recent_uses"] == []

    detail = client.get("/api/v1/prompts/documentation?category=architectural-patterns")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["slug"] == "documentation"
    assert body["needs_code"] is False
    assert body["selected_category"] == "architectural-patterns"
    assert body["patterns"]
    assert "task" not in body

    db = client.app.state.session_factory()
    try:
        pattern = db.scalar(select(SharedPattern).where(SharedPattern.name == "Circuit Breaker"))
    finally:
        db.close()

    missing_code = client.post(
        "/api/v1/prompts/code/uses",
        json={"pattern_id": pattern.id, "focus": "checkout"},
    )
    assert missing_code.status_code == 400

    built = client.post(
        "/api/v1/prompts/code/uses",
        json={
            "pattern_id": pattern.id,
            "focus": "checkout API",
            "code": "async function pay() { return charge(); }",
        },
    )
    assert built.status_code == 201, built.text
    use = built.json()
    assert use["title"] == "Tackle your code"
    assert use["pattern"]["name"] == "Circuit Breaker"
    assert "async function pay()" in use["body"]
    assert use["has_code"] is True

    fetched = client.get(f"/api/v1/prompts/uses/{use['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["body"] == use["body"]

    recent = client.get("/api/v1/prompts").json()["recent_uses"]
    assert any(row["id"] == use["id"] for row in recent)

    sign_in(client, accounts["editor"])
    assert client.get(f"/api/v1/prompts/uses/{use['id']}").status_code == 404

    sign_in(client, accounts["viewer"])
    documented = client.post(
        "/api/v1/prompts/documentation/uses",
        json={"pattern_id": pattern.id, "focus": "payments", "code": "should be dropped"},
    )
    assert documented.status_code == 201, documented.text
    assert "Write the documentation" in documented.json()["body"]
    assert "should be dropped" not in documented.json()["body"]
    assert client.get("/api/v1/prompts/missing").status_code == 404
