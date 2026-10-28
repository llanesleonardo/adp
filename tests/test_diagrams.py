import json
import re

from sqlalchemy import select

from app.models import Exercise, PatternExercise, SharedPattern
from tests.conftest import sign_in


def test_build_the_defined_diagram_for_a_pattern(client, accounts):
    sign_in(client, accounts["viewer"])
    library = client.get("/exercises")
    assert library.status_code == 200
    assert "Build the diagram" in library.text

    db = client.app.state.session_factory()
    try:
        exercise = db.scalar(
            select(Exercise).where(Exercise.repository_path.like("%/19-rate-limiter.md#diagram"))
        )
        spec = json.loads(exercise.spec)
        link = db.scalar(select(PatternExercise).where(PatternExercise.exercise_id == exercise.id))
        pattern = db.get(SharedPattern, link.pattern_id)
    finally:
        db.close()

    assert spec["kind"] == "diagram"
    assert len(spec["edges"]) >= 2
    page = client.get(f"/exercises/{exercise.id}")
    assert page.status_code == 200
    assert "exercise.js" in page.text
    assert spec["nodes"][0]["label"] in page.text
    embedded = re.search(r'id="exercise-spec" type="application/json">(.*?)</script>', page.text)
    public = json.loads(embedded.group(1))
    assert public["kind"] == "diagram"
    assert "edges" not in public
    request = next(node for node in public["nodes"] if node["label"] == "Request")
    assert "-->" not in request["explain"]
    assert "API key" in request["explain"]
    assert "Notes" in page.text
    assert "Export PDF" in page.text

    saved = client.post(
        f"/exercises/{exercise.id}/notes",
        data={"body": "Watch the gateway before the limiter.", "csrf": "x"},
    )
    assert saved.status_code == 200
    assert "Watch the gateway before the limiter." in saved.text
    sign_in(client, accounts["editor"])
    private = client.get(f"/exercises/{exercise.id}")
    assert "Watch the gateway before the limiter." not in private.text
    sign_in(client, accounts["viewer"])
    drawn = spec["edges"][0]
    labels = {node["id"]: node["label"] for node in spec["nodes"]}
    graded = client.post(
        f"/exercises/{exercise.id}/check",
        json={"links": [drawn], "answers": {}},
    )
    assert graded.json()["passed"] is False
    pdf = client.get(f"/exercises/{exercise.id}/result.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"].startswith("application/pdf")
    document = pdf.content.decode("latin-1")
    assert document.startswith("%PDF")
    assert "Watch the gateway before the limiter." in document
    assert "Lines 1 of" in document
    other = next(edge for edge in spec["edges"] if edge != drawn)
    hidden = f"{labels[other['source']]} -> {labels[other['target']]}"
    assert hidden not in document

    owned = client.get(f"/patterns/{pattern.category_slug}/{pattern.id}")
    assert f'href="/exercises/{exercise.id}"' in owned.text
    assert "Build the diagram" in owned.text

    graded = client.post(
        f"/exercises/{exercise.id}/check",
        json={"links": spec["edges"], "answers": {}},
    )
    assert graded.status_code == 200
    assert graded.json()["passed"] is True
    missed = client.post(
        f"/exercises/{exercise.id}/check",
        json={"links": [spec["edges"][0]], "answers": {}},
    )
    assert missed.json()["passed"] is False
    assert spec["edges"][1]["target"] not in missed.text


def test_prompt_exercise_keeps_notes_and_exports_a_pdf(client, accounts):
    sign_in(client, accounts["editor"])
    created = client.post(
        "/exercises",
        data={"title": "Read the limiter", "prompt": "Write what a token bucket does.", "csrf": "x"},
    )
    assert created.status_code == 200
    assert "Notes" in created.text
    assert "Export PDF" in created.text
    saved = client.post(
        f"{created.url.path}/notes",
        data={"body": "A token bucket refills over time.", "csrf": "x"},
    )
    assert "A token bucket refills over time." in saved.text
    pdf = client.get(f"{created.url.path}/result.pdf")
    document = pdf.content.decode("latin-1")
    assert document.startswith("%PDF")
    assert "Not checked yet." in document
    assert "A token bucket refills over time." in document
    assert "Write what a token bucket does." in document
