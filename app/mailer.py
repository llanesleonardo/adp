import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EmailSettings, OutboxMessage
from app.security import utcnow

logger = logging.getLogger("adp")


class DeliveryError(Exception):
    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


@dataclass
class Outbound:
    to_email: str
    sender: str
    subject: str
    text_body: str
    html_body: str
    idempotency_key: str
    kind: str


@dataclass
class MailState:
    fail_once: set[str] = field(default_factory=set)
    failed_once: set[str] = field(default_factory=set)


class LocalOutboxProvider:
    """Development mailbox. Messages stay in the database and are not sent externally."""

    def __init__(self, state: MailState):
        self.state = state

    def send(self, db: Session, message: Outbound) -> str:
        existing = db.scalar(
            select(OutboxMessage).where(
                OutboxMessage.idempotency_key == message.idempotency_key,
                OutboxMessage.accepted.is_(True),
            )
        )
        if existing is not None:
            return existing.provider_message_id

        if message.kind == "campaign" and message.to_email.endswith("@suppress.test"):
            raise DeliveryError("permanent bounce", permanent=True)
        if message.kind == "campaign" and message.to_email in self.state.fail_once and message.to_email not in self.state.failed_once:
            self.state.failed_once.add(message.to_email)
            raise DeliveryError("mailbox temporarily unavailable", permanent=False)

        provider_message_id = f"local-{message.idempotency_key}"
        db.add(
            OutboxMessage(
                idempotency_key=message.idempotency_key,
                kind=message.kind,
                to_email=message.to_email,
                sender=message.sender,
                subject=message.subject,
                text_body=message.text_body,
                html_body=message.html_body,
                provider_message_id=provider_message_id,
                accepted=True,
                created_at=utcnow(),
            )
        )
        db.flush()
        logger.info("local mailbox accepted %s message %s", message.kind, provider_message_id)
        return provider_message_id


class SmtpEmailProvider:
    def __init__(self, settings: EmailSettings):
        self.settings = settings

    def send(self, db: Session, message: Outbound) -> str:
        if not self.settings.smtp_host:
            raise DeliveryError("SMTP host is not configured", permanent=True)
        email = EmailMessage()
        email["To"] = message.to_email
        email["From"] = message.sender
        email["Subject"] = message.subject
        email.set_content(message.text_body)
        email.add_alternative(message.html_body, subtype="html")
        try:
            with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=20) as smtp:
                if self.settings.smtp_use_tls:
                    smtp.starttls()
                if self.settings.smtp_username:
                    smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                smtp.send_message(email)
        except OSError as exc:
            raise DeliveryError("email provider rejected the message", permanent=False) from exc
        logger.info("smtp provider accepted message %s", message.idempotency_key)
        return f"smtp-{message.idempotency_key}"


def campaign_provider(settings: EmailSettings, state: MailState):
    if settings.provider == "smtp":
        return SmtpEmailProvider(settings)
    return LocalOutboxProvider(state)


def transactional_provider(settings: EmailSettings, state: MailState):
    if settings.enabled and settings.provider == "smtp":
        return SmtpEmailProvider(settings)
    return LocalOutboxProvider(state)
