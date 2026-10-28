from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, require_user
from app.domain import (
    add_source,
    approve_summary,
    audit_dict,
    campaign_dict,
    change_password,
    confirm_subscription,
    create_campaign,
    create_document,
    create_ingestion_job,
    create_manual_summary,
    create_user,
    delete_source,
    delivery_dict,
    document_dict,
    email_settings_dict,
    generate_summary_draft,
    get_campaign,
    get_document,
    get_email_settings,
    get_ingestion_job,
    get_public_summary,
    get_publication_settings,
    job_dict,
    list_audit,
    list_campaigns,
    list_deliveries,
    list_document_summaries,
    list_documents,
    list_outbox,
    list_public_summaries,
    list_sources,
    list_subscribers,
    list_summaries,
    list_users,
    login_user,
    logout_session,
    preview_campaign,
    process_ingestion_jobs,
    public_summary_dict,
    publication_settings_dict,
    request_password_reset,
    reset_password,
    retrieve_passages,
    retry_delivery,
    search_documents,
    send_campaign,
    set_document_publication,
    submit_summary,
    subscribe_address,
    subscriber_dict,
    summary_dict,
    suppress_subscriber,
    unpublish_summary,
    unsubscribe_address,
    update_document,
    update_email_settings,
    update_publication_settings,
    update_summary,
    update_user,
    user_dict,
)
from app.models import User
from app.security import client_ip

router = APIRouter(prefix="/api/v1")


class LoginIn(BaseModel):
    email: str
    password: str


class ForgotIn(BaseModel):
    email: str


class ResetIn(BaseModel):
    token: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class UserIn(BaseModel):
    email: str
    password: str
    role: str
    can_send_public_email: bool = False


class UserPatch(BaseModel):
    role: str | None = None
    can_send_public_email: bool | None = None
    is_active: bool | None = None
    password: str | None = None


class DocumentIn(BaseModel):
    title: str
    body: str = ""
    publish: bool = False
    category: str = "architectural-patterns"


class DocumentPatch(BaseModel):
    title: str | None = None
    body: str | None = None
    category: str | None = None


class SourceIn(BaseModel):
    label: str
    url: str


class IngestIn(BaseModel):
    title: str
    body: str = ""
    source_label: str = ""
    source_url: str = ""
    publish: bool = False


class RetrievalIn(BaseModel):
    query: str = Field(min_length=1, max_length=200)


class SummaryIn(BaseModel):
    title: str = ""
    body: str = ""
    public_asset_url: str | None = None


class SummaryPatch(BaseModel):
    title: str | None = None
    body: str | None = None
    public_asset_url: str | None = None


class CampaignIn(BaseModel):
    subject: str
    email_type: str
    summary_ids: list[int] = Field(min_length=1)
    recipient_emails: list[str] = []
    audience: str | None = None


class SendIn(BaseModel):
    confirm: bool = False


class SubscribeIn(BaseModel):
    email: str
    consent: bool
    source: str = "public_form"


class TokenIn(BaseModel):
    token: str


class EmailSettingsIn(BaseModel):
    enabled: bool = False
    provider: str = "local"
    sender_email: str = ""
    sender_name: str = ""
    public_base_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str | None = None
    smtp_use_tls: bool = True


class PublicationIn(BaseModel):
    public_site_enabled: bool
    organization_name: str


class SuppressIn(BaseModel):
    reason: str = "suppressed by administrator"


def set_session_cookie(response: Response, request: Request, raw_token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        "adp_session",
        raw_token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_absolute_hours * 3600,
        path="/",
    )


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user, raw, csrf = login_user(
        db,
        request.app.state.settings,
        request.app.state.limiter,
        client_ip(request),
        body.email,
        body.password,
    )
    set_session_cookie(response, request, raw)
    payload = user_dict(user)
    payload["csrf_token"] = csrf
    return payload


@router.post("/auth/logout")
def logout(request: Request, response: Response, user: User = Depends(require_user), db: Session = Depends(get_db)):
    logout_session(db, request.state.session)
    response.delete_cookie("adp_session", path="/")
    return {"detail": "Signed out."}


@router.get("/auth/me")
def me(request: Request, user: User = Depends(require_user)):
    payload = user_dict(user)
    payload["csrf_token"] = request.state.session.csrf_token
    return payload


@router.post("/auth/forgot-password")
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    request_password_reset(
        db,
        request.app.state.settings,
        request.app.state.mail_state,
        request.app.state.limiter,
        client_ip(request),
        body.email,
    )
    return {"detail": "If an account exists, a reset link has been sent."}


@router.post("/auth/reset-password")
def reset(body: ResetIn, request: Request, db: Session = Depends(get_db)):
    request.app.state.limiter.hit(
        f"reset-submit:{client_ip(request)}",
        request.app.state.settings.public_limit,
        request.app.state.settings.public_window_seconds,
    )
    reset_password(db, body.token, body.password)
    return {"detail": "Password updated. Sign in with the new password."}


@router.post("/auth/change-password")
def change(body: ChangePasswordIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    change_password(db, user, body.current_password, body.new_password)
    return {"detail": "Password updated. Sign in again."}


@router.get("/users")
def users(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return [user_dict(row) for row in list_users(db, user)]


@router.post("/users", status_code=201)
def post_user(body: UserIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    created = create_user(db, user, body.email, body.password, body.role, body.can_send_public_email)
    return user_dict(created)


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, user: User = Depends(require_user), db: Session = Depends(get_db)):
    updated = update_user(db, user, user_id, body.model_dump(exclude_unset=True))
    return user_dict(updated)


@router.get("/documents")
def documents(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return [document_dict(db, row) for row in list_documents(db, user)]


@router.post("/documents", status_code=201)
def post_document(body: DocumentIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    document = create_document(db, user, body.title, body.body, body.publish, body.category)
    return document_dict(db, document)


@router.get("/documents/{document_id}")
def get_one_document(document_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return document_dict(db, get_document(db, user, document_id))


@router.patch("/documents/{document_id}")
def patch_document(document_id: int, body: DocumentPatch, user: User = Depends(require_user), db: Session = Depends(get_db)):
    document = update_document(db, user, document_id, body.title, body.body, body.category)
    return document_dict(db, document)


@router.post("/documents/{document_id}/publish")
def publish_document(document_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return document_dict(db, set_document_publication(db, user, document_id, True))


@router.post("/documents/{document_id}/unpublish")
def unpublish_document(document_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return document_dict(db, set_document_publication(db, user, document_id, False))


@router.post("/documents/{document_id}/sources", status_code=201)
def post_source(document_id: int, body: SourceIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    source = add_source(db, user, document_id, body.label, body.url)
    return {"id": source.id, "document_id": source.document_id, "label": source.label, "url": source.url}


@router.get("/documents/{document_id}/public-summaries")
def document_summaries(document_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return [summary_dict(db, user, row) for row in list_document_summaries(db, user, document_id)]


@router.post("/documents/{document_id}/public-summaries", status_code=201)
def post_summary(document_id: int, body: SummaryIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    summary = create_manual_summary(db, user, document_id, body.title, body.body, body.public_asset_url)
    return summary_dict(db, user, summary)


@router.post("/documents/{document_id}/public-summaries/generate-draft", status_code=201)
def post_generated_summary(document_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    summary = generate_summary_draft(db, user, document_id)
    return summary_dict(db, user, summary)


@router.get("/search")
def search(q: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return [document_dict(db, row) for row in search_documents(db, user, q)]


@router.post("/retrieval")
def retrieval(body: RetrievalIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return {"results": retrieve_passages(db, user, body.query)}


@router.post("/ingest/jobs", status_code=201)
def post_ingest(body: IngestIn, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    job = create_ingestion_job(db, user, body.title, body.body, body.source_label, body.source_url, body.publish)
    process_ingestion_jobs(db)
    job = get_ingestion_job(db, user, job.id)
    return job_dict(job)


@router.get("/ingest/jobs/{job_id}")
def get_ingest(job_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return job_dict(get_ingestion_job(db, user, job_id))


@router.get("/sources")
def sources(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = list_sources(db, user)
    return [
        {"id": row.id, "document_id": row.document_id, "label": row.label, "url": row.url}
        for row in rows
    ]


@router.delete("/sources/{source_id}")
def remove_source(source_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    delete_source(db, user, source_id)
    return {"detail": "Source removed."}


@router.get("/public-summaries")
def summaries(status: str | None = None, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return [summary_dict(db, user, row) for row in list_summaries(db, user, status)]


@router.get("/public-summaries/{summary_id}")
def get_summary(summary_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    from app.domain import get_summary_row

    summary = get_summary_row(db, summary_id)
    return summary_dict(db, user, summary)


@router.patch("/public-summaries/{summary_id}")
def patch_summary(summary_id: int, body: SummaryPatch, user: User = Depends(require_user), db: Session = Depends(get_db)):
    summary = update_summary(
        db,
        user,
        summary_id,
        body.title,
        body.body,
        body.public_asset_url,
        "public_asset_url" in body.model_fields_set,
    )
    return summary_dict(db, user, summary)


@router.post("/public-summaries/{summary_id}/submit-review")
def submit(summary_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return summary_dict(db, user, submit_summary(db, user, summary_id))


@router.post("/public-summaries/{summary_id}/approve")
def approve(summary_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return summary_dict(db, user, approve_summary(db, user, summary_id))


@router.post("/public-summaries/{summary_id}/unpublish")
def unpublish(summary_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return summary_dict(db, user, unpublish_summary(db, user, summary_id))


@router.get("/public/summaries")
def public_summaries(db: Session = Depends(get_db)):
    return list_public_summaries(db)


@router.get("/public/summaries/{slug}")
def public_summary(slug: str, db: Session = Depends(get_db)):
    summary, version = get_public_summary(db, slug)
    return public_summary_dict(summary, version)


@router.post("/email/campaigns", status_code=201)
def post_campaign(body: CampaignIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    campaign = create_campaign(
        db,
        user,
        body.subject,
        body.email_type,
        body.summary_ids,
        body.recipient_emails,
        body.audience,
    )
    return campaign_dict(db, campaign)


@router.get("/email/campaigns")
def campaigns(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return [campaign_dict(db, row) for row in list_campaigns(db, user)]


@router.get("/email/campaigns/{campaign_id}")
def campaign(campaign_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return campaign_dict(db, get_campaign(db, user, campaign_id))


@router.post("/email/campaigns/{campaign_id}/preview")
def campaign_preview(campaign_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return preview_campaign(db, user, campaign_id)


@router.post("/email/campaigns/{campaign_id}/send")
def campaign_send(campaign_id: int, body: SendIn, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    campaign = send_campaign(
        db,
        request.app.state.settings,
        request.app.state.mail_state,
        user,
        campaign_id,
        body.confirm,
    )
    return campaign_dict(db, campaign)


@router.get("/email/campaigns/{campaign_id}/deliveries")
def campaign_deliveries(campaign_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return [delivery_dict(row) for row in list_deliveries(db, user, campaign_id)]


@router.post("/email/campaigns/{campaign_id}/deliveries/{delivery_id}/retry")
def campaign_retry(
    campaign_id: int,
    delivery_id: int,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    delivery = retry_delivery(
        db,
        request.app.state.settings,
        request.app.state.mail_state,
        user,
        campaign_id,
        delivery_id,
    )
    return delivery_dict(delivery)


@router.post("/public/subscriptions")
def subscribe(body: SubscribeIn, request: Request, db: Session = Depends(get_db)):
    subscribe_address(
        db,
        request.app.state.settings,
        request.app.state.mail_state,
        request.app.state.limiter,
        client_ip(request),
        body.email,
        body.consent,
        body.source,
    )
    return {"detail": "If this address can be subscribed, a confirmation message was sent."}


@router.post("/public/subscriptions/confirm")
def confirm(body: TokenIn, request: Request, db: Session = Depends(get_db)):
    request.app.state.limiter.hit(
        f"confirm:{client_ip(request)}",
        request.app.state.settings.public_limit,
        request.app.state.settings.public_window_seconds,
    )
    confirm_subscription(db, body.token)
    return {"detail": "Subscription confirmed."}


@router.post("/public/subscriptions/unsubscribe")
def unsubscribe(body: TokenIn, request: Request, db: Session = Depends(get_db)):
    request.app.state.limiter.hit(
        f"unsubscribe:{client_ip(request)}",
        request.app.state.settings.public_limit,
        request.app.state.settings.public_window_seconds,
    )
    unsubscribe_address(db, body.token)
    return {"detail": "You are unsubscribed."}


@router.get("/subscribers")
def subscribers(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return [subscriber_dict(row) for row in list_subscribers(db, user)]


@router.post("/subscribers/{subscriber_id}/suppress")
def suppress(subscriber_id: int, body: SuppressIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    return subscriber_dict(suppress_subscriber(db, user, subscriber_id, body.reason))


@router.get("/settings/email")
def read_email_settings(user: User = Depends(require_user), db: Session = Depends(get_db)):
    if not user.role == "SUPER_ADMIN":
        from app.errors import Forbidden

        raise Forbidden()
    return email_settings_dict(get_email_settings(db))


@router.patch("/settings/email")
def patch_email_settings(body: EmailSettingsIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    row = update_email_settings(db, user, body.model_dump())
    return email_settings_dict(row)


@router.get("/settings/publication")
def read_publication_settings(user: User = Depends(require_user), db: Session = Depends(get_db)):
    if user.role != "SUPER_ADMIN":
        from app.errors import Forbidden

        raise Forbidden()
    return publication_settings_dict(get_publication_settings(db))


@router.patch("/settings/publication")
def patch_publication_settings(body: PublicationIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    row = update_publication_settings(db, user, body.public_site_enabled, body.organization_name)
    return publication_settings_dict(row)


@router.get("/audit")
def audit(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return [audit_dict(row) for row in list_audit(db, user)]


@router.get("/outbox")
def outbox(user: User = Depends(require_user), db: Session = Depends(get_db)):
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "to_email": row.to_email,
            "sender": row.sender,
            "subject": row.subject,
            "text_body": row.text_body,
            "created_at": row.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        for row in list_outbox(db, user)
    ]
