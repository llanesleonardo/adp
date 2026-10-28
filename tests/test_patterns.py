from tests.conftest import sign_in


def test_explore_patterns_starts_with_categories(client, accounts):
    sign_in(client, accounts["editor"])
    created = client.post(
        "/api/v1/documents",
        json={
            "title": "Cache-aside",
            "body": "Check the cache first.",
            "publish": True,
            "category": "architectural-patterns",
        },
    )
    assert created.status_code == 201
    assert created.json()["category"] == "architectural-patterns"

    library = client.get("/patterns")
    assert library.status_code == 200
    for name in ("Architectural Patterns", "AI and Agentic Patterns", "Azure", "Security Patterns", "Software Development Frameworks"):
        assert name in library.text
    assert "Microservices" not in library.text
    assert "Cache-aside" not in library.text

    category = client.get("/patterns/architectural-patterns")
    assert category.status_code == 200
    assert "Microservices" in category.text
    assert "Cache-aside" in category.text
    assert "Check the cache first." in category.text
    azure = client.get("/patterns/azure")
    assert azure.status_code == 200
    assert "Check the cache first." not in azure.text
    assert client.get("/patterns/missing").status_code == 404

    db = client.app.state.session_factory()
    try:
        from sqlalchemy import func, select
        from app.models import SharedPattern

        assert db.scalar(select(func.count()).select_from(SharedPattern)) == 632
    finally:
        db.close()

    draft = client.post(
        "/api/v1/documents",
        json={"title": "Hidden pattern", "body": "Draft only", "publish": False, "category": "security-patterns"},
    )
    assert draft.status_code == 201
    sign_in(client, accounts["viewer"])
    visible = client.get("/patterns/security-patterns")
    assert visible.status_code == 200
    assert "Hidden pattern" not in visible.text
    assert "Cache-aside" not in client.get("/patterns").text


def test_pattern_page_lists_learning_tools(client, accounts):
    from sqlalchemy import select

    from app.models import LearningSection, SharedPattern

    sign_in(client, accounts["editor"])
    db = client.app.state.session_factory()
    try:
        slugs = set(db.scalars(select(LearningSection.slug)).all())
        pattern = db.scalar(select(SharedPattern).where(SharedPattern.category_slug == "architectural-patterns"))
    finally:
        db.close()
    assert slugs == {"patterns", "videos", "exercises", "lectures"}
    page = client.get(f"/patterns/architectural-patterns/{pattern.id}")
    assert page.status_code == 200
    assert "Learning tools" in page.text
    for label in ("Videos", "Exercises", "Lectures", "No videos yet.", "No exercises yet.", "No lectures yet."):
        assert label in page.text
    added = client.post(
        f"/patterns/architectural-patterns/{pattern.id}/tools",
        data={"section": "exercises", "title": "Sketch the actors", "summary": "Draw one mailbox.", "csrf": "x"},
        follow_redirects=True,
    )
    assert added.status_code == 200
    assert "Sketch the actors" in added.text
    sign_in(client, accounts["viewer"])
    denied = client.post(
        f"/patterns/architectural-patterns/{pattern.id}/tools",
        data={"section": "videos", "title": "Viewer video", "csrf": "x"},
    )
    assert denied.status_code == 403
    viewer = client.get(f"/patterns/architectural-patterns/{pattern.id}")
    assert "Sketch the actors" in viewer.text
    assert "Add a learning tool" not in viewer.text
