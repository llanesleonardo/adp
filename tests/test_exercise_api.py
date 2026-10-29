import json

from sqlalchemy import select

from app.models import Exercise
from tests.conftest import sign_in


def test_exercises_api_lists_and_returns_public_board(client, accounts):
    sign_in(client, accounts["viewer"])
    listed = client.get("/api/v1/exercises")
    assert listed.status_code == 200, listed.text
    exercises = listed.json()
    assert exercises
    assert any("Exercise 19" in row["title"] for row in exercises)
    sample = next(
        row for row in exercises if "Rate Limiter" in row["title"] and row["kind"] == "practice"
    )
    assert sample["kind_label"] == "Match and questions"
    assert "Redis" in sample["technologies"]
    assert any(pattern["name"] == "Token Bucket" for pattern in sample["patterns"])

    db = client.app.state.session_factory()
    try:
        exercise = db.scalar(
            select(Exercise).where(
                Exercise.title.contains("Rate Limiter"),
                Exercise.kind == "practice",
            )
        )
        spec = json.loads(exercise.spec)
    finally:
        db.close()

    detail = client.get(f"/api/v1/exercises/{exercise.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["id"] == exercise.id
    assert body["kind"] == "practice"
    assert body["board"] is not None
    assert body["board"]["matchPrompt"]
    assert body["board"]["left"]
    assert body["board"]["questions"]
    assert "rightId" not in json.dumps(body["board"])
    assert '"answer"' not in json.dumps(body["board"])
    assert spec["pairs"][0]["left"] in json.dumps(body["board"])
    assert body["report"]["checked"] is False
    assert isinstance(body["patterns"], list)
    assert client.get("/api/v1/exercises/999999").status_code == 404
