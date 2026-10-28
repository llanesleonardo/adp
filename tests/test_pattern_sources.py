from sqlalchemy import select

from app.models import SharedPattern
from tests.conftest import sign_in


def test_pattern_page_keeps_the_sources_and_ingestion_is_separate(client, accounts):
    sign_in(client, accounts["editor"])
    db = client.app.state.session_factory()
    try:
        pattern = db.scalar(
            select(SharedPattern).where(SharedPattern.category_slug == "resilience-patterns", SharedPattern.name == "Circuit Breaker")
        )
        pattern_id = pattern.id
        source_url = pattern.source_url
    finally:
        db.close()

    page = client.get(f"/patterns/resilience-patterns/{pattern_id}")
    assert page.status_code == 200
    assert "taken from these sources" in page.text
    assert source_url in page.text
    assert "Add source" in page.text

    added = client.post(
        f"/patterns/resilience-patterns/{pattern_id}/sources",
        data={"label": "Release it", "url": "https://example.test/circuit-breaker", "csrf": "x"},
    )
    assert added.status_code == 200
    assert "https://example.test/circuit-breaker" in added.text

    blocked = client.post(
        f"/patterns/resilience-patterns/{pattern_id}/sources",
        data={"label": "Bad", "url": "javascript:alert(1)", "csrf": "x"},
    )
    assert blocked.status_code == 400

    sign_in(client, accounts["viewer"])
    visible = client.get(f"/patterns/resilience-patterns/{pattern_id}")
    assert "https://example.test/circuit-breaker" in visible.text
    assert "Add source" not in visible.text
    denied = client.post(
        f"/patterns/resilience-patterns/{pattern_id}/sources",
        data={"label": "Nope", "url": "https://example.test/nope", "csrf": "x"},
    )
    assert denied.status_code == 403

    desk = client.get("/sources")
    assert desk.status_code == 200
    assert "Ingestion" in desk.text
    assert "https://example.test/circuit-breaker" not in desk.text
    assert "Ingest a page" not in desk.text

    sign_in(client, accounts["plus"])
    ingested = client.post(
        "/sources",
        data={
            "title": "Notes on bulkheads",
            "body": "Isolate the pool.",
            "source_label": "Team note",
            "source_url": "https://example.test/bulkhead-note",
            "csrf": "x",
        },
    )
    assert ingested.status_code == 200
    assert "Notes on bulkheads" in ingested.text
    assert "Ingested" in ingested.text
    assert "https://example.test/circuit-breaker" not in ingested.text
