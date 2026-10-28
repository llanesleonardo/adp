from tests.conftest import (
    approve_summary,
    enable_mail,
    extract_token,
    mailbox,
    sign_in,
    subscriber_rows,
)

PRIVATE = "PRIVATE_ARCHITECTURE_SENTINEL"
SOURCE_URL = "https://intranet.example.internal/runbook"
PUBLIC = "Public explanation of the cache-aside pattern and its tradeoffs."


def _private_body():
    return f"Intro.\n\n## Internal notes\n{PRIVATE}\n\n## Sources\n{SOURCE_URL}\n"


def _campaign(client, summary_id, email_type="share", recipients=None, audience=None):
    return client.post(
        "/api/v1/email/campaigns",
        json={
            "subject": "Approved pattern",
            "email_type": email_type,
            "summary_ids": [summary_id],
            "recipient_emails": recipients or [],
            "audience": audience,
        },
    )


def test_email_cannot_be_sent_without_an_approved_summary(client, accounts):
    sign_in(client, accounts["editor"])
    created = client.post("/api/v1/documents", json={"title": "Cache-aside", "body": _private_body(), "publish": True})
    drafted = client.post(
        f"/api/v1/documents/{created.json()['id']}/public-summaries",
        json={"title": "Cache-aside", "body": PUBLIC},
    )
    sign_in(client, accounts["admin"])
    denied = _campaign(client, drafted.json()["id"], recipients=["person@example.com"])
    assert denied.status_code == 409
    settings = client.get("/api/v1/settings/email")
    assert settings.json()["enabled"] is False
    _document_id, summary_id, _approved = approve_summary(client, accounts, _private_body(), PUBLIC)
    created_campaign = _campaign(client, summary_id, recipients=["person@example.com"])
    assert created_campaign.status_code == 201, created_campaign.text
    preview = client.post(f"/api/v1/email/campaigns/{created_campaign.json()['id']}/preview")
    assert preview.status_code == 200
    blocked = client.post(f"/api/v1/email/campaigns/{created_campaign.json()['id']}/send", json={"confirm": True})
    assert blocked.status_code == 409
    assert all(message["kind"] != "campaign" for message in mailbox(client))


def test_viewer_and_ordinary_editor_cannot_send_emails(client, accounts):
    _document_id, summary_id, _approved = approve_summary(client, accounts, _private_body(), PUBLIC)
    for email in (accounts["viewer"], accounts["editor"]):
        sign_in(client, email)
        response = _campaign(client, summary_id, recipients=["person@example.com"])
        assert response.status_code == 403


def test_authorized_sender_can_preview_and_send(client, accounts):
    _document_id, summary_id, approved = approve_summary(client, accounts, _private_body(), PUBLIC, SOURCE_URL)
    enable_mail(client)
    sign_in(client, accounts["sender"])
    created = _campaign(client, summary_id, recipients=["person@example.com"])
    assert created.status_code == 201, created.text
    campaign_id = created.json()["id"]
    preview = client.post(f"/api/v1/email/campaigns/{campaign_id}/preview")
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["recipient_count"] == 1
    assert body["sender"]
    assert PUBLIC in body["text"]
    assert approved["slug"] in body["text"]
    assert "not added to a newsletter" in body["text"]
    assert PRIVATE not in body["text"]
    assert SOURCE_URL not in body["text"]
    sent = client.post(f"/api/v1/email/campaigns/{campaign_id}/send", json={"confirm": True})
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "sent"
    deliveries = client.get(f"/api/v1/email/campaigns/{campaign_id}/deliveries")
    assert deliveries.json()[0]["status"] == "sent"
    messages = [message for message in mailbox(client) if message["kind"] == "campaign"]
    assert len(messages) == 1
    assert messages[0]["to"] == "person@example.com"
    assert PUBLIC in messages[0]["text"]
    assert PUBLIC in messages[0]["html"]
    assert PRIVATE not in messages[0]["text"]
    assert PRIVATE not in messages[0]["html"]
    assert SOURCE_URL not in messages[0]["text"]
    assert SOURCE_URL not in messages[0]["html"]
    assert ("person@example.com", "subscribed") not in subscriber_rows(client)
    assert all(email != "person@example.com" for email, _status in subscriber_rows(client))


def test_share_does_not_subscribe_the_recipient(client, accounts):
    _document_id, summary_id, _approved = approve_summary(client, accounts, _private_body(), PUBLIC)
    enable_mail(client)
    created = _campaign(client, summary_id, recipients=["outsider@example.com"])
    campaign_id = created.json()["id"]
    client.post(f"/api/v1/email/campaigns/{campaign_id}/preview")
    client.post(f"/api/v1/email/campaigns/{campaign_id}/send", json={"confirm": True})
    assert all(email != "outsider@example.com" for email, _status in subscriber_rows(client))


def test_unsubscribed_recipients_are_excluded_from_newsletters(client, accounts):
    _document_id, summary_id, _approved = approve_summary(client, accounts, _private_body(), PUBLIC)
    enable_mail(client)
    client.post("/api/v1/auth/logout")
    for address in ("keep@example.com", "leave@example.com", "pending@example.com"):
        subscribed = client.post(
            "/api/v1/public/subscriptions",
            json={"email": address, "consent": True, "source": "public_form"},
        )
        assert subscribed.status_code == 200, subscribed.text
    messages = mailbox(client)
    keep_token = extract_token(next(item["text"] for item in messages if item["to"] == "keep@example.com"), "confirm")
    leave_token = extract_token(next(item["text"] for item in messages if item["to"] == "leave@example.com"), "confirm")
    assert client.post("/api/v1/public/subscriptions/confirm", json={"token": keep_token}).status_code == 200
    assert client.post("/api/v1/public/subscriptions/confirm", json={"token": leave_token}).status_code == 200
    assert client.post("/api/v1/public/subscriptions/confirm", json={"token": leave_token}).status_code == 400
    sign_in(client, accounts["admin"])
    first = _campaign(client, summary_id, email_type="newsletter", audience="all_subscribed")
    first_id = first.json()["id"]
    client.post(f"/api/v1/email/campaigns/{first_id}/preview")
    sent = client.post(f"/api/v1/email/campaigns/{first_id}/send", json={"confirm": True})
    assert sent.json()["status"] == "sent"
    campaign_messages = [item for item in mailbox(client) if item["kind"] == "campaign"]
    assert {item["to"] for item in campaign_messages} == {"keep@example.com", "leave@example.com"}
    leave_mail = next(item for item in campaign_messages if item["to"] == "leave@example.com")
    assert "/unsubscribe/" in leave_mail["text"]
    token = extract_token(leave_mail["text"], "unsubscribe")
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/public/subscriptions/unsubscribe", json={"token": token}).status_code == 200
    assert client.post("/api/v1/public/subscriptions/unsubscribe", json={"token": token}).status_code == 400
    sign_in(client, accounts["admin"])
    second = _campaign(client, summary_id, email_type="newsletter", audience="all_subscribed")
    second_id = second.json()["id"]
    client.post(f"/api/v1/email/campaigns/{second_id}/preview")
    client.post(f"/api/v1/email/campaigns/{second_id}/send", json={"confirm": True})
    second_messages = [
        item for item in mailbox(client) if item["kind"] == "campaign" and item["key"].startswith("delivery-")
    ]
    second_ids = {item["key"] for item in second_messages}
    # The second send adds one new delivery. The earlier two remain.
    latest = [item for item in second_messages if item["to"] == "leave@example.com"]
    assert len(latest) == 1
    assert ("leave@example.com", "unsubscribed") in subscriber_rows(client)
    assert ("pending@example.com", "pending") in subscriber_rows(client)
    deliveries = client.get(f"/api/v1/email/campaigns/{second_id}/deliveries").json()
    assert [row["email"] for row in deliveries] == ["keep@example.com"]
    assert second_ids


def test_email_failures_are_visible_and_retry_does_not_duplicate(client, accounts):
    _document_id, summary_id, _approved = approve_summary(client, accounts, _private_body(), PUBLIC)
    enable_mail(client)
    client.app.state.mail_state.fail_once.add("retry@example.com")
    created = _campaign(client, summary_id, recipients=["retry@example.com", "ok@example.com"])
    campaign_id = created.json()["id"]
    client.post(f"/api/v1/email/campaigns/{campaign_id}/preview")
    sent = client.post(f"/api/v1/email/campaigns/{campaign_id}/send", json={"confirm": True})
    assert sent.json()["status"] == "partial"
    deliveries = client.get(f"/api/v1/email/campaigns/{campaign_id}/deliveries").json()
    failed = next(row for row in deliveries if row["email"] == "retry@example.com")
    assert failed["status"] == "failed"
    assert failed["failure"]
    assert failed["attempts"] == 1
    campaign_messages = [item for item in mailbox(client) if item["kind"] == "campaign"]
    assert len(campaign_messages) == 1
    assert campaign_messages[0]["to"] == "ok@example.com"
    retried = client.post(f"/api/v1/email/campaigns/{campaign_id}/deliveries/{failed['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "sent"
    assert retried.json()["attempts"] == 2
    again = client.post(f"/api/v1/email/campaigns/{campaign_id}/deliveries/{failed['id']}/retry")
    assert again.status_code == 409
    sent_to_retry = [item for item in mailbox(client) if item["to"] == "retry@example.com"]
    assert len(sent_to_retry) == 1

    bounced = client.post(
        "/api/v1/public/subscriptions",
        json={"email": "bad@suppress.test", "consent": True, "source": "public_form"},
    )
    assert bounced.status_code == 200
    token = extract_token(next(item["text"] for item in mailbox(client) if item["to"] == "bad@suppress.test"), "confirm")
    # Confirmation uses the local transactional mailbox, so it is not a provider bounce.
    assert client.post("/api/v1/public/subscriptions/confirm", json={"token": token}).status_code == 200
    newsletter = _campaign(client, summary_id, email_type="newsletter", audience="all_subscribed")
    newsletter_id = newsletter.json()["id"]
    client.post(f"/api/v1/email/campaigns/{newsletter_id}/preview")
    client.post(f"/api/v1/email/campaigns/{newsletter_id}/send", json={"confirm": True})
    rows = client.get(f"/api/v1/email/campaigns/{newsletter_id}/deliveries").json()
    bounce = next(row for row in rows if row["email"] == "bad@suppress.test")
    assert bounce["status"] == "failed"
    assert "bounce" in (bounce["failure"] or "")
    assert ("bad@suppress.test", "suppressed") in subscriber_rows(client)
    follow = _campaign(client, summary_id, email_type="newsletter", audience="all_subscribed")
    follow_id = follow.json()["id"]
    preview = client.post(f"/api/v1/email/campaigns/{follow_id}/preview")
    assert "bad@suppress.test" not in preview.json()["recipients"]


def test_people_subscribe_to_the_weekly_email(client, accounts):
    public = client.get("/public")
    assert 'href="/subscribe"' in public.text
    form = client.get("/subscribe")
    assert form.status_code == 200
    assert "weekly email" in form.text.lower()
    assert "I agree to receive the weekly email" in form.text

    sign_in(client, accounts["viewer"])
    assert client.get("/subscribers").status_code == 403

    sign_in(client, accounts["admin"])
    audience = client.get("/subscribers")
    assert audience.status_code == 200
    assert "weekly email" in audience.text.lower()
    assert 'href="/campaigns/new?weekly=1"' in audience.text
    weekly = client.get("/campaigns/new?weekly=1")
    assert "This week's email" in weekly.text
    assert 'value="newsletter" checked' in weekly.text
    assert 'value="all_subscribed" checked' in weekly.text
    assert 'value="Weekly knowledge news"' in weekly.text


def test_settings_is_a_grid_of_pages(client, accounts):
    sign_in(client, accounts["viewer"])
    assert client.get("/settings").status_code == 403
    assert client.get("/outbox").status_code == 403
    assert client.post("/settings/email", data={"sender_email": "news@library.test", "csrf": "x"}).status_code == 403

    sign_in(client, accounts["admin"])
    page = client.get("/settings")
    assert page.status_code == 200
    assert 'class="cats"' in page.text
    for href in (
        "/settings/publication",
        "/settings/email",
        "/settings/service",
        "/users",
        "/subscribers",
        "/outbox",
        "/audit",
    ):
        assert f'href="{href}"' in page.text
    assert "smtp_host" not in page.text

    address = client.post(
        "/settings/email",
        data={
            "csrf": "x",
            "enabled": "yes",
            "sender_name": "Library News",
            "sender_email": "news@library.test",
            "public_base_url": "http://127.0.0.1:8000",
        },
        follow_redirects=True,
    )
    assert address.status_code == 200
    assert address.url.path == "/settings/email"
    assert 'value="news@library.test"' in address.text

    service = client.post(
        "/settings/service",
        data={
            "csrf": "x",
            "provider": "smtp",
            "smtp_host": "smtp.library.test",
            "smtp_port": "587",
            "smtp_username": "news",
            "smtp_password": "mailbox-secret-1",
            "smtp_use_tls": "yes",
        },
        follow_redirects=True,
    )
    assert service.status_code == 200
    assert service.url.path == "/settings/service"
    assert "smtp.library.test" in service.text
    assert "mailbox-secret-1" not in service.text
    assert 'value="news@library.test"' in client.get("/settings/email").text


def test_smtp_password_is_not_returned_or_audited(client, accounts):
    sign_in(client, accounts["admin"])
    secret = "smtp-secret-value"
    updated = client.patch(
        "/api/v1/settings/email",
        json={
            "enabled": False,
            "provider": "smtp",
            "sender_email": "library@example.com",
            "sender_name": "Knowledge Library",
            "public_base_url": "http://127.0.0.1:8000",
            "smtp_host": "smtp.example.com",
            "smtp_password": secret,
        },
    )
    assert updated.status_code == 200, updated.text
    assert secret not in updated.text
    assert updated.json()["smtp_password_set"] is True
    audit = client.get("/api/v1/audit")
    assert secret not in audit.text
