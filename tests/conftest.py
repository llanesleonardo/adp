import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.domain import create_initial_super_admin, create_user
from app.main import create_app
from app.models import OutboxMessage, Subscriber

PASSWORD = "valid-password-1"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path.as_posix()}/test.db",
        environment="test",
        cookie_secure=False,
    )
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def accounts(client):
    db = client.app.state.session_factory()
    try:
        admin = create_initial_super_admin(db, "admin@library.test", PASSWORD)
        create_user(db, admin, "viewer@library.test", PASSWORD, "VIEWER")
        create_user(db, admin, "editor@library.test", PASSWORD, "EDITOR")
        create_user(db, admin, "plus@library.test", PASSWORD, "EDITOR_PLUS")
        create_user(db, admin, "sender@library.test", PASSWORD, "EDITOR_PLUS", True)
    finally:
        db.close()
    return {
        "admin": "admin@library.test",
        "viewer": "viewer@library.test",
        "editor": "editor@library.test",
        "plus": "plus@library.test",
        "sender": "sender@library.test",
    }


def sign_in(client, email: str):
    response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


def enable_mail(client):
    response = client.patch(
        "/api/v1/settings/email",
        json={
            "enabled": True,
            "provider": "local",
            "sender_email": "library@example.com",
            "sender_name": "Knowledge Library",
            "public_base_url": "http://127.0.0.1:8000",
        },
    )
    assert response.status_code == 200, response.text
    return response


def mailbox(client):
    db = client.app.state.session_factory()
    try:
        rows = db.scalars(select(OutboxMessage).order_by(OutboxMessage.id)).all()
        return [
            {
                "to": row.to_email,
                "subject": row.subject,
                "text": row.text_body,
                "html": row.html_body,
                "kind": row.kind,
                "key": row.idempotency_key,
            }
            for row in rows
        ]
    finally:
        db.close()


def subscriber_rows(client):
    db = client.app.state.session_factory()
    try:
        rows = db.scalars(select(Subscriber).order_by(Subscriber.email)).all()
        return [(row.email, row.status) for row in rows]
    finally:
        db.close()


def extract_token(text: str, kind: str) -> str:
    match = re.search(rf"/{kind}/([A-Za-z0-9_\-]+)", text)
    assert match, text
    return match.group(1)


def approve_summary(client, accounts, private_body: str, public_body: str, source_url: str | None = None):
    sign_in(client, accounts["editor"])
    created = client.post(
        "/api/v1/documents",
        json={"title": "Cache-aside", "body": private_body, "publish": True},
    )
    assert created.status_code == 201, created.text
    document_id = created.json()["id"]
    if source_url:
        source = client.post(
            f"/api/v1/documents/{document_id}/sources",
            json={"label": "Runbook", "url": source_url},
        )
        assert source.status_code == 201, source.text
    drafted = client.post(
        f"/api/v1/documents/{document_id}/public-summaries",
        json={"title": "Cache-aside", "body": public_body},
    )
    assert drafted.status_code == 201, drafted.text
    summary_id = drafted.json()["id"]
    submitted = client.post(f"/api/v1/public-summaries/{summary_id}/submit-review")
    assert submitted.status_code == 200, submitted.text
    sign_in(client, accounts["admin"])
    approved = client.post(f"/api/v1/public-summaries/{summary_id}/approve")
    assert approved.status_code == 200, approved.text
    return document_id, summary_id, approved.json()
