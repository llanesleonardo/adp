from sqlalchemy import select

from app.models import SharedPattern
from tests.conftest import sign_in


def _pattern(client, name: str) -> tuple[int, str, str]:
    db = client.app.state.session_factory()
    try:
        pattern = db.scalar(select(SharedPattern).where(SharedPattern.name == name))
        return pattern.id, pattern.name, pattern.category_slug
    finally:
        db.close()


def test_prompt_marketplace_is_its_own_section(client, accounts):
    sign_in(client, accounts["viewer"])
    page = client.get("/prompts")
    assert page.status_code == 200
    assert "Prompt Marketplace" in page.text
    for title in ("Documentation", "Examples", "Risks", "Diagrams", "Tackle your code"):
        assert title in page.text
    assert "separate from exercises" in page.text
    home = client.get("/")
    assert 'href="/prompts/documentation"' in home.text
    assert 'href="/prompts/code"' in home.text


def test_build_a_pattern_prompt_and_keep_it_private(client, accounts):
    sign_in(client, accounts["viewer"])
    pattern_id, pattern_name, category_slug = _pattern(client, "Circuit Breaker")
    missing = client.post(
        "/prompts/code",
        data={"pattern_id": str(pattern_id), "focus": "checkout", "csrf": "x"},
    )
    assert missing.status_code == 400

    built = client.post(
        "/prompts/code",
        data={
            "pattern_id": str(pattern_id),
            "focus": "checkout API",
            "code": "async function pay() { return charge(); }",
            "csrf": "x",
        },
    )
    assert built.status_code == 200
    assert "Tackle your code" in built.text
    assert pattern_name in built.text
    assert "async function pay()" in built.text
    assert "Change only the code this pattern is responsible for." in built.text
    assert f'href="/patterns/{category_slug}/{pattern_id}"' in built.text

    sign_in(client, accounts["editor"])
    private = client.get(built.url.path)
    assert private.status_code == 404

    sign_in(client, accounts["viewer"])
    documented = client.post(
        "/prompts/documentation",
        data={"pattern_id": str(pattern_id), "focus": "payments", "code": "should be dropped", "csrf": "x"},
    )
    assert documented.status_code == 200
    assert "Write the documentation" in documented.text
    assert "should be dropped" not in documented.text
    assert client.get("/prompts/missing").status_code == 404
