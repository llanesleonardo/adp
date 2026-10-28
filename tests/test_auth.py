import logging
from datetime import timedelta

from sqlalchemy import select

from app.bootstrap import main
from app.domain import create_ingestion_job, process_ingestion_jobs
from app.models import AuthSession, PasswordResetToken, User
from app.redact import RedactFilter
from app.security import hash_token, utcnow
from tests.conftest import PASSWORD, extract_token, mailbox, sign_in


def test_unauthenticated_users_cannot_access_private_documents(client, accounts):
    assert client.get("/api/v1/documents/1").status_code == 401
    assert client.get("/api/v1/search", params={"q": "cache"}).status_code == 401
    assert client.post("/api/v1/retrieval", json={"query": "cache"}).status_code == 401
    sign_in(client, accounts["editor"])
    created = client.post("/api/v1/documents", json={"title": "Hidden", "body": "draft only", "publish": False})
    document_id = created.json()["id"]
    client.post("/api/v1/auth/logout")
    assert client.get(f"/api/v1/documents/{document_id}").status_code == 401


def test_each_role_can_perform_only_its_permitted_actions(client, accounts):
    sign_in(client, accounts["editor"])
    draft = client.post("/api/v1/documents", json={"title": "Draft page", "body": "editor draft", "publish": False})
    published = client.post(
        "/api/v1/documents",
        json={"title": "Published page", "body": "visible pattern", "publish": True},
    )
    draft_id = draft.json()["id"]
    published_id = published.json()["id"]
    summary = client.post(
        f"/api/v1/documents/{published_id}/public-summaries",
        json={"title": "Public", "body": "A public explanation of the pattern."},
    )
    summary_id = summary.json()["id"]
    client.post(f"/api/v1/public-summaries/{summary_id}/submit-review")

    sign_in(client, accounts["viewer"])
    assert client.get(f"/api/v1/documents/{published_id}").status_code == 200
    assert client.get(f"/api/v1/documents/{draft_id}").status_code == 403
    assert client.post("/api/v1/documents", json={"title": "Nope", "body": "x"}).status_code == 403
    assert client.post(f"/api/v1/documents/{published_id}/public-summaries", json={"title": "x", "body": "y"}).status_code == 403
    assert client.post("/api/v1/ingest/jobs", json={"title": "Nope"}).status_code == 403
    assert client.post("/api/v1/email/campaigns", json={"subject": "x", "email_type": "share", "summary_ids": [summary_id], "recipient_emails": ["a@example.com"]}).status_code == 403
    assert client.post(f"/api/v1/public-summaries/{summary_id}/approve").status_code == 403
    assert client.get("/api/v1/users").status_code == 403

    sign_in(client, accounts["editor"])
    assert client.post("/api/v1/ingest/jobs", json={"title": "Nope"}).status_code == 403
    assert client.post("/api/v1/email/campaigns", json={"subject": "x", "email_type": "share", "summary_ids": [summary_id], "recipient_emails": ["a@example.com"]}).status_code == 403
    assert client.post(f"/api/v1/public-summaries/{summary_id}/approve").status_code == 403
    assert client.get("/api/v1/audit").status_code == 403

    sign_in(client, accounts["plus"])
    ingested = client.post("/api/v1/ingest/jobs", json={"title": "Ingested note", "body": "from a source"})
    assert ingested.status_code == 201, ingested.text
    assert ingested.json()["status"] == "completed"
    blocked_send = client.post(
        "/api/v1/email/campaigns",
        json={"subject": "x", "email_type": "share", "summary_ids": [summary_id], "recipient_emails": ["a@example.com"]},
    )
    assert blocked_send.status_code == 409

    sign_in(client, accounts["admin"])
    assert client.post(f"/api/v1/public-summaries/{summary_id}/approve").status_code == 200
    assert client.get("/api/v1/users").status_code == 200
    assert client.get("/api/v1/audit").status_code == 200


def test_direct_api_calls_cannot_bypass_permission_checks(client, accounts):
    assert client.post("/api/v1/bootstrap", json={"email": "a@b.co", "password": PASSWORD, "role": "SUPER_ADMIN"}).status_code == 404
    assert client.post("/api/v1/auth/register", json={"email": "a@b.co", "password": PASSWORD}).status_code == 404
    sign_in(client, accounts["viewer"])
    db = client.app.state.session_factory()
    try:
        viewer = db.scalar(select(User).where(User.email == accounts["viewer"]))
        plus = db.scalar(select(User).where(User.email == accounts["plus"]))
        viewer_id = viewer.id
        plus_id = plus.id
    finally:
        db.close()
    assert client.patch(f"/api/v1/users/{viewer_id}", json={"role": "SUPER_ADMIN"}).status_code == 403
    sign_in(client, accounts["plus"])
    assert client.patch(f"/api/v1/users/{plus_id}", json={"can_send_public_email": True}).status_code == 403
    sign_in(client, accounts["admin"])
    db = client.app.state.session_factory()
    try:
        admin = db.scalar(select(User).where(User.email == accounts["admin"]))
        admin_id = admin.id
    finally:
        db.close()
    assert client.patch(f"/api/v1/users/{admin_id}", json={"role": "VIEWER"}).status_code == 409


def test_password_reset_tokens_expire_and_cannot_be_reused(client, accounts, caplog):
    caplog.set_level(logging.INFO)
    sign_in(client, accounts["admin"])
    client.post("/api/v1/auth/logout")
    forgot = client.post("/api/v1/auth/forgot-password", json={"email": accounts["viewer"]})
    assert forgot.status_code == 200
    assert "reset link" in forgot.json()["detail"].lower() or "account" in forgot.json()["detail"].lower()
    token = extract_token(mailbox(client)[-1]["text"], "reset-password")
    assert token not in caplog.text
    db = client.app.state.session_factory()
    try:
        row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(token)))
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    finally:
        db.close()
    expired = client.post("/api/v1/auth/reset-password", json={"token": token, "password": "replacement-password"})
    assert expired.status_code == 400
    client.post("/api/v1/auth/forgot-password", json={"email": accounts["viewer"]})
    fresh = extract_token(mailbox(client)[-1]["text"], "reset-password")
    assert fresh not in caplog.text
    reset = client.post("/api/v1/auth/reset-password", json={"token": fresh, "password": "replacement-password"})
    assert reset.status_code == 200, reset.text
    reused = client.post("/api/v1/auth/reset-password", json={"token": fresh, "password": "another-password-1"})
    assert reused.status_code == 400
    stale = client.post("/api/v1/auth/login", json={"email": accounts["viewer"], "password": PASSWORD})
    assert stale.status_code == 401
    fresh_login = client.post("/api/v1/auth/login", json={"email": accounts["viewer"], "password": "replacement-password"})
    assert fresh_login.status_code == 200


def test_logout_and_session_expiration(client, accounts):
    sign_in(client, accounts["viewer"])
    assert client.get("/api/v1/documents").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get("/api/v1/documents").status_code == 401

    sign_in(client, accounts["viewer"])
    raw = client.cookies.get("adp_session")
    db = client.app.state.session_factory()
    try:
        row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw)))
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    assert client.get("/api/v1/documents").status_code == 401


def test_login_is_rate_limited_and_credentials_are_not_logged(client, accounts, caplog):
    caplog.set_level(logging.INFO)
    secret = "UniqueLoginSecret99"
    for _ in range(8):
        response = client.post("/api/v1/auth/login", json={"email": accounts["viewer"], "password": secret})
        assert response.status_code == 401
    blocked = client.post("/api/v1/auth/login", json={"email": accounts["viewer"], "password": secret})
    assert blocked.status_code == 429
    assert secret not in caplog.text


def test_log_filter_redacts_secrets():
    filt = RedactFilter()
    record = logging.LogRecord("adp", logging.INFO, __file__, 1, "payload %s", ({"password": "SuperSecretValue", "reset_token": "raw-token"},), None)
    assert filt.filter(record) is True
    rendered = record.getMessage()
    assert "SuperSecretValue" not in rendered
    assert "raw-token" not in rendered
    path_record = logging.LogRecord("adp", logging.INFO, __file__, 1, "GET /reset-password/abcDEF_123", (), None)
    filt.filter(path_record)
    assert "abcDEF_123" not in path_record.getMessage()
    access = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", "/reset-password/abcDEF_123", "1.1", 200),
        None,
    )
    assert filt.filter(access) is True
    assert access.args[2] == "/reset-password/[redacted]"
    assert len(access.args) == 5


def test_passwords_are_scrypt_hashes_and_bootstrap_is_not_public(client, accounts):
    db = client.app.state.session_factory()
    try:
        user = db.scalar(select(User).where(User.email == accounts["admin"]))
        assert user.password_hash.startswith("scrypt$")
        assert PASSWORD not in user.password_hash
    finally:
        db.close()


def test_bootstrap_command_creates_only_the_first_super_admin(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ADP_DATABASE_URL", f"sqlite:///{tmp_path.as_posix()}/boot.db")
    monkeypatch.setenv("ADP_BOOTSTRAP_EMAIL", "root@library.test")
    monkeypatch.setenv("ADP_BOOTSTRAP_PASSWORD", PASSWORD)
    assert main() == 0
    first = capsys.readouterr()
    assert PASSWORD not in first.out
    assert "root@library.test" in first.out
    assert main() == 1


def test_ingestion_job_rechecks_authorization(client, accounts):
    db = client.app.state.session_factory()
    try:
        actor = db.scalar(select(User).where(User.email == accounts["plus"]))
        job = create_ingestion_job(db, actor, "Queued note", "private ingest", "", "", False)
        actor.role = "VIEWER"
        db.commit()
        process_ingestion_jobs(db)
        db.refresh(job)
        assert job.status == "failed"
        assert job.document_id is None
    finally:
        db.close()


def test_search_and_retrieval_honor_document_visibility(client, accounts):
    sign_in(client, accounts["editor"])
    client.post("/api/v1/documents", json={"title": "Secret draft", "body": "DRAFTONLYWORD", "publish": False})
    client.post("/api/v1/documents", json={"title": "Visible", "body": "PUBLICWORD", "publish": True})
    sign_in(client, accounts["viewer"])
    hidden = client.get("/api/v1/search", params={"q": "DRAFTONLYWORD"})
    assert hidden.status_code == 200
    assert hidden.json() == []
    visible = client.post("/api/v1/retrieval", json={"query": "PUBLICWORD"})
    assert visible.status_code == 200
    assert len(visible.json()["results"]) == 1
    assert "DRAFTONLYWORD" not in str(visible.json())
