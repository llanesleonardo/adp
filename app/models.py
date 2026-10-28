from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(500))
    role: Mapped[str] = mapped_column(String(32), index=True)
    can_send_public_email: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    category: Mapped[str] = mapped_column(String(64), default="architectural-patterns", server_default="architectural-patterns", index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class DocumentSource(Base):
    __tablename__ = "document_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    label: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(2000))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    source_label: Mapped[str] = mapped_column(String(300), default="")
    source_url: Mapped[str] = mapped_column(String(2000), default="")
    publish: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    error: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PublicSummary(Base):
    __tablename__ = "public_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    source_document_version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    public_asset_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    slug: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    outdated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    publicly_listed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    published_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class PublicSummaryVersion(Base):
    __tablename__ = "public_summary_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    summary_id: Mapped[int] = mapped_column(ForeignKey("public_summaries.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    public_asset_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Subscriber(Base):
    __tablename__ = "subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    consent_source: Mapped[str] = mapped_column(String(120), default="")
    consent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmation_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    unsubscribe_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    suppression_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class SubscriptionToken(Base):
    __tablename__ = "subscription_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscriber_id: Mapped[int] = mapped_column(ForeignKey("subscribers.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class EmailSettings(Base):
    __tablename__ = "email_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    provider: Mapped[str] = mapped_column(String(32), default="local")
    sender_email: Mapped[str] = mapped_column(String(320), default="")
    sender_name: Mapped[str] = mapped_column(String(200), default="")
    public_base_url: Mapped[str] = mapped_column(String(500), default="http://127.0.0.1:8000")
    smtp_host: Mapped[str] = mapped_column(String(255), default="")
    smtp_port: Mapped[int] = mapped_column(Integer, default=587)
    smtp_username: Mapped[str] = mapped_column(String(255), default="")
    smtp_password: Mapped[str] = mapped_column(String(255), default="")
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class PublicationSettings(Base):
    __tablename__ = "publication_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_site_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    organization_name: Mapped[str] = mapped_column(String(200), default="Knowledge Library")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class EmailCampaign(Base):
    __tablename__ = "email_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject: Mapped[str] = mapped_column(String(300))
    email_type: Mapped[str] = mapped_column(String(32))
    summary_version_ids: Mapped[str] = mapped_column(Text, default="[]")
    recipient_spec: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    sent_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    previewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EmailDelivery(Base):
    __tablename__ = "email_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("email_campaigns.id"), index=True)
    subscriber_id: Mapped[int | None] = mapped_column(ForeignKey("subscribers.id"), nullable=True)
    email: Mapped[str] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    failure: Mapped[str | None] = mapped_column(String(500), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    to_email: Mapped[str] = mapped_column(String(320))
    sender: Mapped[str] = mapped_column(String(400), default="")
    subject: Mapped[str] = mapped_column(String(300))
    text_body: Mapped[str] = mapped_column(Text, default="")
    html_body: Mapped[str] = mapped_column(Text, default="")
    provider_message_id: Mapped[str] = mapped_column(String(200), default="")
    accepted: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class SearchHistory(Base):
    __tablename__ = "search_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    query: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "document_id", name="uq_favorite_user_document"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class LibraryFavorite(Base):
    __tablename__ = "library_favorites"
    __table_args__ = (UniqueConstraint("user_id", "kind", "entity_id", name="uq_library_favorite"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    prompt: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(32), default="prompt", server_default="prompt")
    spec: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    repository_path: Mapped[str | None] = mapped_column(String(500), unique=True, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ExerciseAttempt(Base):
    __tablename__ = "exercise_attempts"
    __table_args__ = (UniqueConstraint("user_id", "exercise_id", name="uq_attempt_user_exercise"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="started", index=True)
    result: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class ExerciseNote(Base):
    __tablename__ = "exercise_notes"
    __table_args__ = (UniqueConstraint("user_id", "exercise_id", name="uq_note_user_exercise"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), index=True)
    body: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class PromptUse(Base):
    __tablename__ = "prompt_uses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    slug: Mapped[str] = mapped_column(String(32), index=True)
    pattern_id: Mapped[int] = mapped_column(ForeignKey("shared_patterns.id"), index=True)
    focus: Mapped[str] = mapped_column(String(400), default="")
    code: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)


class AssistantThread(Base):
    __tablename__ = "assistant_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class SharedPattern(Base):
    __tablename__ = "shared_patterns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(200), index=True)
    category_slug: Mapped[str] = mapped_column(String(200), index=True)
    subcategory: Mapped[str] = mapped_column(String(200), default="")
    name: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str] = mapped_column(String(2000), default="")
    repository_path: Mapped[str] = mapped_column(String(500), unique=True)


class LearningSection(Base):
    __tablename__ = "learning_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(400), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_catalog: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class LearningTool(Base):
    __tablename__ = "learning_tools"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern_id: Mapped[int] = mapped_column(ForeignKey("shared_patterns.id"), index=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("learning_sections.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class SystemProblem(Base):
    __tablename__ = "system_problems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(32), default="inventory", index=True)
    source_url: Mapped[str] = mapped_column(String(2000), default="")
    repository_path: Mapped[str | None] = mapped_column(String(500), unique=True, nullable=True)
    shared_pattern_id: Mapped[int | None] = mapped_column(ForeignKey("shared_patterns.id"), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class PatternProblem(Base):
    __tablename__ = "pattern_problems"
    __table_args__ = (UniqueConstraint("pattern_id", "problem_id", name="uq_pattern_problem"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern_id: Mapped[int] = mapped_column(ForeignKey("shared_patterns.id"), index=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("system_problems.id"), index=True)


class DesignConcern(Base):
    __tablename__ = "design_concerns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(2000), default="")
    repository_path: Mapped[str | None] = mapped_column(String(500), unique=True, nullable=True)
    shared_pattern_id: Mapped[int | None] = mapped_column(ForeignKey("shared_patterns.id"), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class PatternSource(Base):
    __tablename__ = "pattern_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern_id: Mapped[int] = mapped_column(ForeignKey("shared_patterns.id"), index=True)
    label: Mapped[str] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(String(2000))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class PatternExercise(Base):
    __tablename__ = "pattern_exercises"
    __table_args__ = (UniqueConstraint("pattern_id", "exercise_id", name="uq_pattern_exercise"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern_id: Mapped[int] = mapped_column(ForeignKey("shared_patterns.id"), index=True)
    exercise_id: Mapped[int] = mapped_column(ForeignKey("exercises.id"), index=True)


class PatternConcern(Base):
    __tablename__ = "pattern_concerns"
    __table_args__ = (UniqueConstraint("pattern_id", "concern_id", name="uq_pattern_concern"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pattern_id: Mapped[int] = mapped_column(ForeignKey("shared_patterns.id"), index=True)
    concern_id: Mapped[int] = mapped_column(ForeignKey("design_concerns.id"), index=True)


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("assistant_threads.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime)
