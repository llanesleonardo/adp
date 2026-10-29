from sqlalchemy import select

from app.models import SystemProblem
from tests.conftest import sign_in


def test_problems_api_lists_and_returns_a3_detail(client, accounts):
    sign_in(client, accounts["viewer"])
    listed = client.get("/api/v1/problems")
    assert listed.status_code == 200, listed.text
    problems = listed.json()
    assert problems
    titles = {row["title"] for row in problems}
    assert "Scaling Ecommerce Notifications" in titles
    assert "Rate Limiter" in titles
    assert all({"id", "title", "source_url", "favorite"} <= set(row) for row in problems)

    db = client.app.state.session_factory()
    try:
        problem = db.scalar(select(SystemProblem).where(SystemProblem.title == "Rate Limiter"))
    finally:
        db.close()

    detail = client.get(f"/api/v1/problems/{problem.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["id"] == problem.id
    assert body["title"] == "Rate Limiter"
    assert body["sheet_title"]
    assert body["favorite"] is False
    assert len(body["a3"]) == 7
    assert [box["title"] for box in body["a3"]] == [
        "1. Background",
        "2. Current Condition",
        "3. Goal",
        "4. Root-Cause Analysis",
        "5. Countermeasures",
        "6. Implementation Plan",
        "7. Follow-up",
    ]
    assert any("100 req/min" in (box["markdown"] or box["html"]) for box in body["a3"])
    assert isinstance(body["related_patterns"], list)
    assert any(row["name"] == "Circuit Breaker" for row in body["related_patterns"])
    assert client.get("/api/v1/problems/999999").status_code == 404
