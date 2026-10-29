from sqlalchemy import func, select

from app.models import PatternProblem, SharedPattern, SystemProblem
from tests.conftest import sign_in


def test_system_problems_start_from_existing_examples(client, accounts):
    sign_in(client, accounts["viewer"])
    page = client.get("/problems")
    assert page.status_code == 200
    assert "Scaling Ecommerce Notifications" in page.text
    assert "Ticketmaster Style Event Booking" in page.text
    assert "Real Time Updates" not in page.text
    assert "No problem-solving examples yet." not in page.text

    db = client.app.state.session_factory()
    try:
        problem_count = db.scalar(select(func.count()).select_from(SystemProblem))
        inventory_count = db.scalar(
            select(func.count()).select_from(SharedPattern).where(SharedPattern.subcategory == "Problems")
        )
        problem = db.scalar(select(SystemProblem).where(SystemProblem.title == "Rate Limiter"))
        pattern = db.scalar(select(SharedPattern).where(SharedPattern.category_slug == "architectural-patterns"))
    finally:
        db.close()
    assert problem_count == inventory_count
    assert problem_count > 0

    detail = client.get(f"/problems/{problem.id}")
    assert detail.status_code == 200
    assert "A3 Report" in detail.text
    for label in (
        "1. Background",
        "2. Current Condition",
        "3. Goal",
        "4. Root-Cause Analysis",
        "5. Countermeasures",
        "6. Implementation Plan",
        "7. Follow-up",
        "100 req/min",
        "429 Too Many Requests",
    ):
        assert label in detail.text
    assert "Relate to a pattern" not in detail.text
    assert "Circuit Breaker" in detail.text
    denied = client.post(
        f"/problems/{problem.id}/patterns",
        data={"pattern_id": str(pattern.id), "csrf": "x"},
    )
    assert denied.status_code == 403

    sign_in(client, accounts["editor"])
    related = client.post(
        f"/problems/{problem.id}/patterns",
        data={"pattern_id": str(pattern.id), "csrf": "x"},
        follow_redirects=True,
    )
    assert related.status_code == 200
    assert pattern.name in related.text
    again = client.post(
        f"/problems/{problem.id}/patterns",
        data={"pattern_id": str(pattern.id), "csrf": "x"},
        follow_redirects=True,
    )
    assert again.status_code == 200
    pattern_page = client.get(f"/patterns/{pattern.category_slug}/{pattern.id}")
    assert "Rate Limiter" in pattern_page.text
    assert client.get("/problems/999999").status_code == 404

    db = client.app.state.session_factory()
    try:
        links = db.scalar(
            select(func.count())
            .select_from(PatternProblem)
            .where(PatternProblem.problem_id == problem.id, PatternProblem.pattern_id == pattern.id)
        )
    finally:
        db.close()
    assert links == 1
