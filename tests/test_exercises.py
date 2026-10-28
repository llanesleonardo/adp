import json

from sqlalchemy import select

from app.models import Exercise, PatternExercise, SharedPattern
from tests.conftest import sign_in


def test_practice_exercises_match_and_ask(client, accounts):
    sign_in(client, accounts["viewer"])
    library = client.get("/exercises")
    assert library.status_code == 200
    assert "Every exercise is listed here." in library.text
    assert "Exercise 02" in library.text
    assert "Exercise 19" in library.text
    rate = library.text[library.text.find("Exercise 19") - 200 : library.text.find("Exercise 19") + 700]
    assert "Redis" in rate
    assert "Lua" in rate
    assert "Token Bucket" in rate
    assert "Rate Limiting" in rate
    assert "Cache Aside" in rate

    db = client.app.state.session_factory()
    try:
        exercise = db.scalar(select(Exercise).where(Exercise.title.contains("Rate Limiter")))
        spec = json.loads(exercise.spec)
        pattern = db.scalar(
            select(SharedPattern).where(
                SharedPattern.repository_path.like("%/Problem_solving_using_SEP/19-rate-limiter.md")
            )
        )
        linked_ids = set(
            db.scalars(select(PatternExercise.pattern_id).where(PatternExercise.exercise_id == exercise.id)).all()
        )
        other = db.scalar(
            select(SharedPattern).where(
                SharedPattern.category_slug == "architectural-patterns",
                SharedPattern.id.not_in(linked_ids or {0}),
            )
        )
    finally:
        db.close()
        owned = client.get(f"/patterns/{pattern.category_slug}/{pattern.id}")
    assert f'href="/exercises/{exercise.id}"' in owned.text
    assert "Match and questions" in owned.text
    unrelated = client.get(f"/patterns/{other.category_slug}/{other.id}")
    assert f'href="/exercises/{exercise.id}"' not in unrelated.text
    page = client.get(f"/exercises/{exercise.id}")
    assert page.status_code == 200
    assert "exercise.js" in page.text
    assert spec["pairs"][0]["left"] in page.text
    assert "rightId" not in page.text
    assert '"answer"' not in page.text

    correct = {
        "links": [{"source": pair["leftId"], "target": pair["rightId"]} for pair in spec["pairs"]],
        "answers": {question["id"]: question["answer"] for question in spec["questions"]},
    }
    graded = client.post(f"/exercises/{exercise.id}/check", json=correct)
    assert graded.status_code == 200
    body = graded.json()
    assert body["passed"] is True
    assert body["match"]["correct"] == body["match"]["total"]

    missed = client.post(
        f"/exercises/{exercise.id}/check",
        json={"links": [{"source": spec["pairs"][0]["leftId"], "target": spec["pairs"][1]["rightId"]}], "answers": {}},
    )
    assert missed.status_code == 200
    assert missed.json()["passed"] is False
    assert spec["pairs"][0]["rightId"] not in missed.text

    home = client.get("/")
    assert "Rate Limiter" in home.text

    sign_in(client, accounts["editor"])
    created = client.post(
        "/exercises",
        data={"title": "Draw a retry timeline", "prompt": "Sketch the waits.", "csrf": "x"},
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert "Start exercise" in created.text
    assert "exercise.js" not in created.text
