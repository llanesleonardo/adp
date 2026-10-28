from app.domain import AI_BANNER
from tests.conftest import approve_summary, mailbox, sign_in

PRIVATE = "PRIVATE_ARCHITECTURE_SENTINEL"
SOURCE_URL = "https://intranet.example.internal/runbook"
PUBLIC = "Public explanation of the cache-aside pattern and its tradeoffs."


def _private_body():
    return f"""A short public-safe intro.

## Internal notes
{PRIVATE}

## Sources
{SOURCE_URL}

password = super-secret-value
"""


def test_public_summary_is_distinct_from_its_private_source(client, accounts):
    document_id, summary_id, approved = approve_summary(client, accounts, _private_body(), PUBLIC, SOURCE_URL)
    sign_in(client, accounts["editor"])
    document = client.get(f"/api/v1/documents/{document_id}")
    summary = client.get(f"/api/v1/public-summaries/{summary_id}")
    assert document.json()["body"] != summary.json()["body"]
    assert document.json()["sources"][0]["url"] == SOURCE_URL
    assert "sources" not in summary.json()["published_snapshot"]
    assert summary.json()["document_id"] == document_id
    assert summary.json()["published_snapshot"]["body"] == PUBLIC


def test_unapproved_summary_cannot_be_exposed_publicly_or_emailed(client, accounts):
    sign_in(client, accounts["editor"])
    created = client.post("/api/v1/documents", json={"title": "Cache-aside", "body": _private_body(), "publish": True})
    document_id = created.json()["id"]
    generated = client.post(f"/api/v1/documents/{document_id}/public-summaries/generate-draft")
    assert generated.status_code == 201, generated.text
    body = generated.json()
    assert body["ai_generated"] is True
    assert body["status"] == "draft"
    assert body["publicly_listed"] is False
    assert AI_BANNER in body["body"]
    assert PRIVATE not in body["body"]
    assert SOURCE_URL not in body["body"]
    assert "super-secret-value" not in body["body"]
    summary_id = body["id"]
    assert client.get("/api/v1/public/summaries").json() == []
    sign_in(client, accounts["admin"])
    denied = client.post(
        "/api/v1/email/campaigns",
        json={
            "subject": "Pattern",
            "email_type": "share",
            "summary_ids": [summary_id],
            "recipient_emails": ["person@example.com"],
        },
    )
    assert denied.status_code == 409
    assert all(message["kind"] != "campaign" for message in mailbox(client))


def test_editing_an_approved_summary_requires_approval_again(client, accounts):
    _document_id, summary_id, approved = approve_summary(client, accounts, _private_body(), PUBLIC)
    slug = approved["slug"]
    sign_in(client, accounts["editor"])
    edited = client.patch(
        f"/api/v1/public-summaries/{summary_id}",
        json={"body": "UNAPPROVED CHANGE that is not yet public."},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "draft"
    public = client.get(f"/api/v1/public/summaries/{slug}")
    assert public.status_code == 200
    assert public.json()["body"] == PUBLIC
    assert "UNAPPROVED CHANGE" not in public.text
    sign_in(client, accounts["admin"])
    too_soon = client.post(f"/api/v1/public-summaries/{summary_id}/approve")
    assert too_soon.status_code == 409
    sign_in(client, accounts["editor"])
    assert client.post(f"/api/v1/public-summaries/{summary_id}/submit-review").status_code == 200
    sign_in(client, accounts["admin"])
    assert client.post(f"/api/v1/public-summaries/{summary_id}/approve").status_code == 200
    republished = client.get(f"/api/v1/public/summaries/{slug}")
    assert "UNAPPROVED CHANGE" in republished.json()["body"]


def test_public_endpoints_do_not_return_private_source_content(client, accounts):
    _document_id, _summary_id, approved = approve_summary(client, accounts, _private_body(), PUBLIC, SOURCE_URL)
    client.post("/api/v1/auth/logout")
    listing = client.get("/api/v1/public/summaries")
    detail = client.get(f"/api/v1/public/summaries/{approved['slug']}")
    combined = listing.text + detail.text
    assert PRIVATE not in combined
    assert SOURCE_URL not in combined
    assert "super-secret-value" not in combined
    assert "sources" not in detail.json()
    assert "document_id" not in detail.json()
    assert detail.json()["body"] == PUBLIC
    page = client.get(f"/public/summaries/{approved['slug']}")
    assert page.status_code == 200
    assert PRIVATE not in page.text
    assert "Approved for public sharing" in page.text


def test_changed_source_marks_summary_potentially_outdated(client, accounts):
    document_id, summary_id, approved = approve_summary(client, accounts, _private_body(), PUBLIC)
    sign_in(client, accounts["editor"])
    before = client.get(f"/api/v1/public-summaries/{summary_id}").json()
    updated = client.patch(
        f"/api/v1/documents/{document_id}",
        json={"body": _private_body() + "\nA later private revision."},
    )
    assert updated.status_code == 200
    after = client.get(f"/api/v1/public-summaries/{summary_id}").json()
    assert after["outdated"] is True
    assert after["body"] == before["body"]
    assert after["published_snapshot"]["body"] == PUBLIC
    public = client.get(f"/api/v1/public/summaries/{approved['slug']}")
    assert public.json()["body"] == PUBLIC
    assert "later private revision" not in public.text
