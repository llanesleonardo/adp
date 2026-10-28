from sqlalchemy import select

from app.models import DesignConcern, SharedPattern
from tests.conftest import sign_in


def test_design_concerns_belong_on_the_pattern(client, accounts):
    sign_in(client, accounts["viewer"])
    library = client.get("/patterns")
    assert library.status_code == 200
    assert 'href="/concerns"' not in library.text
    assert "Menu" in library.text
    assert client.get("/concerns").status_code == 404

    db = client.app.state.session_factory()
    try:
        concern = db.scalar(select(DesignConcern).where(DesignConcern.title == "Real Time Updates"))
        pattern = db.scalar(
            select(SharedPattern).where(SharedPattern.repository_path.endswith("06-cqrs.md"))
        )
        plain = db.scalar(
            select(SharedPattern).where(SharedPattern.category_slug == "architectural-patterns")
        )
    finally:
        db.close()
    assert concern is not None and (concern.body or "").strip()
    page = client.get(f"/patterns/{pattern.category_slug}/{pattern.id}")
    assert page.status_code == 200
    assert "Design concerns" in page.text
    assert "Real Time Updates" in page.text

    detail = client.get(f"/patterns/{pattern.category_slug}/{pattern.id}/concerns/{concern.id}")
    assert detail.status_code == 200
    assert "What this concern means" in detail.text
    assert "Explore Patterns" in detail.text
    assert f"/patterns/{pattern.category_slug}/{pattern.id}" in detail.text

    other = client.get(f"/patterns/{plain.category_slug}/{plain.id}")
    assert "Design concerns" in other.text
    assert client.get(f"/patterns/{plain.category_slug}/{plain.id}/concerns/{concern.id}").status_code == 404
