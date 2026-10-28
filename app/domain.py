import json
import logging
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from markupsafe import escape
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.a3 import build_a3, case_title, linked_pattern_paths, rewrite_case_links
from app.markdown import render_case_markdown
from app.config import Settings
from app.errors import BadRequest, Conflict, Forbidden, NotFound, Unauthenticated
from app.limiter import RateLimiter
from app.mailer import DeliveryError, MailState, Outbound, campaign_provider, transactional_provider
from app.models import (
    AssistantMessage,
    AssistantThread,
    AuditLog,
    AuthSession,
    Document,
    DocumentSource,
    EmailCampaign,
    EmailDelivery,
    EmailSettings,
    Exercise,
    ExerciseAttempt,
    ExerciseNote,
    PromptUse,
    PatternExercise,
    Favorite,
    LibraryFavorite,
    IngestionJob,
    LearningSection,
    DesignConcern,
    LearningTool,
    PatternConcern,
    PatternProblem,
    PatternSource,
    SystemProblem,
    OutboxMessage,
    PasswordResetToken,
    PublicSummary,
    PublicSummaryVersion,
    PublicationSettings,
    SearchHistory,
    SharedPattern,
    Subscriber,
    SubscriptionToken,
    User,
)
from app.permissions import (
    can_admin,
    can_approve,
    can_create_campaign,
    can_edit,
    can_ingest,
    can_read_document,
    can_send_email,
    permission_map,
)
from app.security import (
    DUMMY_PASSWORD_HASH,
    ROLES,
    expires_in,
    hash_password,
    hash_token,
    new_secret,
    normalize_email,
    utcnow,
    validate_password,
    verify_password,
)

logger = logging.getLogger("adp")

_CATEGORY_TONES = ("blue", "purple", "green", "rose", "amber")


def slugify_label(value: str) -> str:
    text = (value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "uncategorized"


def list_pattern_categories(db: Session) -> list[dict]:
    rows = db.execute(
        select(SharedPattern.category_slug, SharedPattern.category, func.count())
        .group_by(SharedPattern.category_slug, SharedPattern.category)
        .order_by(SharedPattern.category)
    ).all()
    categories = []
    for index, (slug, title, count) in enumerate(rows):
        label = "pattern" if count == 1 else "patterns"
        categories.append(
            {
                "slug": slug,
                "title": title,
                "summary": f"{count} {label}",
                "tone": _CATEGORY_TONES[index % len(_CATEGORY_TONES)],
            }
        )
    return categories


def category_by_slug(db: Session, slug: str) -> dict:
    for category in list_pattern_categories(db):
        if category["slug"] == slug:
            return category
    raise NotFound()


def normalize_category(db: Session, value: str | None) -> str:
    slug = (value or "architectural-patterns").strip()
    try:
        category_by_slug(db, slug)
    except NotFound:
        raise BadRequest("Choose a pattern category.") from None
    return slug


def list_shared_patterns(db: Session, slug: str) -> list[SharedPattern]:
    return list(
        db.scalars(
            select(SharedPattern)
            .where(SharedPattern.category_slug == slug)
            .order_by(SharedPattern.name, SharedPattern.id)
        ).all()
    )


def pattern_brief(db: Session, pattern: SharedPattern) -> str:
    """Short library constraints for a prompt. Diagram answers stay out."""
    lines = []
    concerns = list_pattern_concerns(db, pattern.id)
    if concerns:
        lines.append("Design concerns: " + "; ".join(concern.title for concern in concerns[:6]))
    problems = list_related_problems(db, pattern.id)
    if problems:
        lines.append("Seen in system problems: " + "; ".join(problem.title for problem in problems[:6]))
        excerpt = _prompt_excerpt(problems[0].summary or "")
        if len(excerpt) < 40:
            excerpt = _prompt_excerpt(problems[0].body or "")
        if excerpt:
            lines.append(excerpt)
    return "\n".join(lines) or "Use the pattern as named. Do not replace it with a neighboring pattern."


def _prompt_excerpt(text: str) -> str:
    cleaned = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.M)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = cleaned.replace("**", "").replace("`", "")
    cleaned = re.sub(r"(?<!\*)\*(?!\*)", "", cleaned)
    cleaned = cleaned.replace("|", " ")
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > 320:
        cleaned = cleaned[:320].rsplit(" ", 1)[0]
    return cleaned


def list_prompt_uses(db: Session, actor: User, slug: str | None = None, limit: int = 8) -> list[tuple[PromptUse, SharedPattern]]:
    query = (
        select(PromptUse, SharedPattern)
        .join(SharedPattern, SharedPattern.id == PromptUse.pattern_id)
        .where(PromptUse.user_id == actor.id)
        .order_by(PromptUse.id.desc())
        .limit(limit)
    )
    if slug:
        query = query.where(PromptUse.slug == slug)
    return list(db.execute(query).all())


def get_prompt_use(db: Session, actor: User, use_id: int) -> tuple[PromptUse, SharedPattern]:
    row = db.get(PromptUse, use_id)
    if row is None or row.user_id != actor.id:
        raise NotFound()
    pattern = db.get(SharedPattern, row.pattern_id)
    if pattern is None:
        raise NotFound()
    return row, pattern


def build_prompt_use(db: Session, actor: User, slug: str, pattern_id: int, focus: str, code: str) -> PromptUse:
    from app.prompts import compose_prompt, prompt_by_slug

    spec = prompt_by_slug(slug)
    if spec is None:
        raise NotFound()
    pattern = db.get(SharedPattern, pattern_id)
    if pattern is None:
        raise BadRequest("Choose a pattern.")
    cleaned_focus = " ".join((focus or "").split())[:400]
    cleaned_code = (code or "").replace("\x00", "")[:12000]
    if spec["needs_code"] and not cleaned_code.strip():
        raise BadRequest("Paste the code this pattern should tackle.")
    if not spec["needs_code"]:
        cleaned_code = ""
    use = PromptUse(
        user_id=actor.id,
        slug=slug,
        pattern_id=pattern.id,
        focus=cleaned_focus,
        code=cleaned_code,
        body=compose_prompt(spec, pattern, pattern_brief(db, pattern), cleaned_focus, cleaned_code),
        created_at=utcnow(),
    )
    db.add(use)
    db.commit()
    return use


def list_pattern_sources(db: Session, pattern_id: int) -> list[PatternSource]:
    return list(
        db.scalars(
            select(PatternSource).where(PatternSource.pattern_id == pattern_id).order_by(PatternSource.id)
        ).all()
    )


def add_pattern_source(db: Session, actor: User, pattern: SharedPattern, label: str, url: str) -> PatternSource:
    if not can_edit(actor):
        raise Forbidden()
    cleaned_label = " ".join((label or "").split())
    cleaned_url = (url or "").strip()
    if not cleaned_label or not cleaned_url:
        raise BadRequest("A source label and link are required.")
    if not cleaned_url.startswith(("http://", "https://")):
        raise BadRequest("Use an http or https link.")
    source = PatternSource(
        pattern_id=pattern.id,
        label=cleaned_label[:300],
        url=cleaned_url[:2000],
        created_by=actor.id,
        created_at=utcnow(),
    )
    db.add(source)
    db.flush()
    audit(db, actor, "pattern.source", "shared_pattern", pattern.id, {"label": source.label})
    db.commit()
    return source


def delete_pattern_source(db: Session, actor: User, pattern: SharedPattern, source_id: int) -> None:
    if not can_edit(actor):
        raise Forbidden()
    source = db.get(PatternSource, source_id)
    if source is None or source.pattern_id != pattern.id:
        raise NotFound()
    db.delete(source)
    audit(db, actor, "pattern.source.delete", "shared_pattern", pattern.id, {"source_id": source_id})
    db.commit()


def list_ingestion_jobs(db: Session, actor: User, limit: int = 20) -> list[IngestionJob]:
    query = select(IngestionJob).order_by(IngestionJob.id.desc()).limit(limit)
    if not can_admin(actor):
        query = query.where(IngestionJob.actor_id == actor.id)
    return list(db.scalars(query).all())


def get_shared_pattern(db: Session, pattern_id: int, slug: str) -> SharedPattern:
    pattern = db.get(SharedPattern, pattern_id)
    if pattern is None or pattern.category_slug != slug:
        raise NotFound()
    return pattern


LEARNING_SECTIONS = (
    ("patterns", "Patterns", "The pattern library. Categories and patterns live here.", 1, True),
    ("videos", "Videos", "Video lessons attached to a pattern.", 2, False),
    ("exercises", "Exercises", "Practice activities attached to a pattern.", 3, False),
    ("lectures", "Lectures", "Guided lectures attached to a pattern.", 4, False),
)


def ensure_learning_sections(db: Session) -> None:
    existing = {row.slug: row for row in db.scalars(select(LearningSection)).all()}
    for slug, title, description, sort_order, is_catalog in LEARNING_SECTIONS:
        row = existing.get(slug)
        if row is None:
            db.add(
                LearningSection(
                    slug=slug,
                    title=title,
                    description=description,
                    sort_order=sort_order,
                    is_catalog=is_catalog,
                )
            )
        else:
            row.title = title
            row.description = description
            row.sort_order = sort_order
            row.is_catalog = is_catalog
    db.commit()


def list_tool_sections(db: Session) -> list[LearningSection]:
    return list(
        db.scalars(
            select(LearningSection)
            .where(LearningSection.is_catalog.is_(False))
            .order_by(LearningSection.sort_order, LearningSection.id)
        ).all()
    )


def list_pattern_tools(db: Session, pattern_id: int) -> dict[int, list[LearningTool]]:
    rows = db.scalars(
        select(LearningTool)
        .where(LearningTool.pattern_id == pattern_id)
        .order_by(LearningTool.sort_order, LearningTool.id)
    ).all()
    grouped: dict[int, list[LearningTool]] = {}
    for row in rows:
        grouped.setdefault(row.section_id, []).append(row)
    return grouped


def create_learning_tool(
    db: Session,
    actor: User,
    pattern: SharedPattern,
    section_slug: str,
    title: str,
    summary: str,
) -> LearningTool:
    if not can_edit(actor):
        raise Forbidden()
    section = db.scalar(select(LearningSection).where(LearningSection.slug == section_slug))
    if section is None or section.is_catalog:
        raise BadRequest("Choose a learning tool.")
    cleaned = (title or "").strip()
    if not cleaned or len(cleaned) > 300:
        raise BadRequest("A title is required.")
    tool = LearningTool(
        pattern_id=pattern.id,
        section_id=section.id,
        title=cleaned,
        summary=(summary or "").strip()[:4000],
        status="ready",
        sort_order=0,
        created_at=utcnow(),
    )
    db.add(tool)
    db.flush()
    audit(db, actor, "learning_tool.create", "learning_tool", tool.id, {"pattern_id": pattern.id, "section": section.slug})
    db.commit()
    return tool


def import_shared_patterns(db: Session, path: Path | None = None) -> int:
    inventory = path or Path(__file__).resolve().parent.parent / "data" / "All_Shared_Patterns_Inventory.json"
    if not inventory.is_file():
        return 0
    payload = json.loads(inventory.read_text(encoding="utf-8"))
    existing = set(db.scalars(select(SharedPattern.repository_path)).all())
    added = 0
    for entry in payload.get("entries", []):
        repository_path = str(entry.get("repository_path") or "").strip()
        name = str(entry.get("name") or "").strip()
        category = str(entry.get("category") or "").strip()
        if not repository_path or not name or not category or repository_path in existing:
            continue
        db.add(
            SharedPattern(
                category=category[:200],
                category_slug=slugify_label(category)[:200],
                subcategory=str(entry.get("subcategory") or "").strip()[:200],
                name=name[:300],
                source_url=str(entry.get("source_url") or "").strip()[:2000],
                repository_path=repository_path[:500],
            )
        )
        existing.add(repository_path)
        added += 1
    if added:
        db.commit()
    return added


def ensure_system_problems(db: Session) -> int:
    """Copy the problem-solving examples already in the pattern inventory."""
    existing_paths = set(db.scalars(select(SystemProblem.repository_path)).all())
    existing_patterns = set(
        path for path in db.scalars(select(SystemProblem.shared_pattern_id)).all() if path is not None
    )
    added = 0
    now = utcnow()
    rows = db.scalars(
        select(SharedPattern).where(SharedPattern.subcategory == "Problems").order_by(SharedPattern.id)
    ).all()
    for row in rows:
        if row.repository_path in existing_paths or row.id in existing_patterns:
            continue
        db.add(
            SystemProblem(
                title=row.name[:300],
                summary="",
                origin="inventory",
                source_url=row.source_url,
                repository_path=row.repository_path,
                shared_pattern_id=row.id,
                created_at=now,
            )
        )
        existing_paths.add(row.repository_path)
        existing_patterns.add(row.id)
        added += 1
    if added:
        db.commit()
    apply_case_library(db)
    return added


def apply_case_library(db: Session) -> int:
    """Fill problem sheets from the case notes already in the library."""
    library = Path(__file__).resolve().parent.parent / "data" / "system_problem_cases.json"
    if not library.is_file():
        return 0
    cases = json.loads(library.read_text(encoding="utf-8"))
    by_path = {row.repository_path: row for row in db.scalars(select(SharedPattern)).all()}
    updated = 0
    for problem in db.scalars(select(SystemProblem)).all():
        body = cases.get(problem.repository_path or "")
        if body and (problem.body or "") != body:
            problem.body = body
            updated += 1
        if not problem.body or not problem.repository_path:
            continue
        for repository_path in linked_pattern_paths(problem.body, problem.repository_path):
            pattern = by_path.get(repository_path)
            if pattern is None or pattern.id == problem.shared_pattern_id:
                continue
            existing = db.scalar(
                select(PatternProblem).where(
                    PatternProblem.problem_id == problem.id,
                    PatternProblem.pattern_id == pattern.id,
                )
            )
            if existing is None:
                db.add(PatternProblem(pattern_id=pattern.id, problem_id=problem.id))
    db.commit()
    return updated


def a3_for_problem(db: Session, problem: SystemProblem) -> tuple[str, list[dict]]:
    urls = _pattern_urls(db)
    title = case_title(problem.body or "") or problem.title
    return title, build_a3(problem.body or "", problem.repository_path or "", urls)


def _pattern_urls(db: Session) -> dict[str, str]:
    return {
        row.repository_path: f"/patterns/{row.category_slug}/{row.id}"
        for row in db.scalars(select(SharedPattern)).all()
    }


def ensure_design_concerns(db: Session) -> int:
    """Copy the design-concern notes already in the pattern inventory."""
    existing_paths = set(db.scalars(select(DesignConcern.repository_path)).all())
    existing_patterns = {
        row for row in db.scalars(select(DesignConcern.shared_pattern_id)).all() if row is not None
    }
    added = 0
    now = utcnow()
    rows = db.scalars(
        select(SharedPattern).where(SharedPattern.subcategory == "Concerns").order_by(SharedPattern.id)
    ).all()
    for row in rows:
        if row.repository_path in existing_paths or row.id in existing_patterns:
            continue
        db.add(
            DesignConcern(
                title=row.name[:300],
                summary="",
                body="",
                source_url=row.source_url,
                repository_path=row.repository_path,
                shared_pattern_id=row.id,
                created_at=now,
            )
        )
        existing_paths.add(row.repository_path)
        existing_patterns.add(row.id)
        added += 1
    if added:
        db.commit()
    apply_design_concerns(db)
    return added


def apply_design_concerns(db: Session) -> int:
    library = Path(__file__).resolve().parent.parent / "data" / "design_concern_cases.json"
    if not library.is_file():
        return 0
    cases = json.loads(library.read_text(encoding="utf-8"))
    by_path = {row.repository_path: row for row in db.scalars(select(SharedPattern)).all()}
    updated = 0
    for concern in db.scalars(select(DesignConcern)).all():
        body = cases.get(concern.repository_path or "")
        if body and (concern.body or "") != body:
            concern.body = body
            updated += 1
        if not concern.body or not concern.repository_path:
            continue
        for repository_path in linked_pattern_paths(concern.body, concern.repository_path):
            pattern = by_path.get(repository_path)
            if pattern is None or pattern.subcategory in {"Problems", "Concerns", "Exercises"}:
                continue
            if pattern.id == concern.shared_pattern_id:
                continue
            existing = db.scalar(
                select(PatternConcern).where(
                    PatternConcern.concern_id == concern.id,
                    PatternConcern.pattern_id == pattern.id,
                )
            )
            if existing is None:
                db.add(PatternConcern(pattern_id=pattern.id, concern_id=concern.id))
    db.commit()
    return updated


def pattern_has_concerns(pattern: SharedPattern) -> bool:
    return pattern.subcategory not in {"Problems", "Concerns", "Exercises"}


def list_pattern_concerns(db: Session, pattern_id: int) -> list[DesignConcern]:
    return list(
        db.scalars(
            select(DesignConcern)
            .join(PatternConcern, PatternConcern.concern_id == DesignConcern.id)
            .where(PatternConcern.pattern_id == pattern_id)
            .order_by(DesignConcern.title, DesignConcern.id)
        ).all()
    )


def get_pattern_concern(db: Session, pattern_id: int, concern_id: int) -> DesignConcern:
    concern = db.get(DesignConcern, concern_id)
    link = db.scalar(
        select(PatternConcern).where(
            PatternConcern.pattern_id == pattern_id,
            PatternConcern.concern_id == concern_id,
        )
    )
    if concern is None or link is None:
        raise NotFound()
    return concern


def render_concern(db: Session, concern: DesignConcern):
    return render_case_markdown(
        rewrite_case_links(concern.body or "", concern.repository_path or "", _pattern_urls(db))
    )


def list_system_problems(db: Session) -> list[SystemProblem]:
    return list(db.scalars(select(SystemProblem).order_by(SystemProblem.title, SystemProblem.id)).all())


def get_system_problem(db: Session, problem_id: int) -> SystemProblem:
    problem = db.get(SystemProblem, problem_id)
    if problem is None:
        raise NotFound()
    return problem


def list_related_patterns(db: Session, problem_id: int) -> list[SharedPattern]:
    return list(
        db.scalars(
            select(SharedPattern)
            .join(PatternProblem, PatternProblem.pattern_id == SharedPattern.id)
            .where(PatternProblem.problem_id == problem_id)
            .order_by(SharedPattern.category, SharedPattern.name, SharedPattern.id)
        ).all()
    )


def list_related_problems(db: Session, pattern_id: int) -> list[SystemProblem]:
    return list(
        db.scalars(
            select(SystemProblem)
            .join(PatternProblem, PatternProblem.problem_id == SystemProblem.id)
            .where(PatternProblem.pattern_id == pattern_id)
            .order_by(SystemProblem.title, SystemProblem.id)
        ).all()
    )


def list_pattern_groups(db: Session) -> list[dict]:
    groups: list[dict] = []
    current: dict | None = None
    rows = db.scalars(select(SharedPattern).order_by(SharedPattern.category, SharedPattern.name, SharedPattern.id)).all()
    for row in rows:
        if current is None or current["title"] != row.category:
            current = {"title": row.category, "patterns": []}
            groups.append(current)
        current["patterns"].append(row)
    return groups


def relate_problem_to_pattern(db: Session, actor: User, problem: SystemProblem, pattern_id: int) -> PatternProblem:
    if not can_edit(actor):
        raise Forbidden()
    pattern = db.get(SharedPattern, pattern_id)
    if pattern is None:
        raise BadRequest("Choose a pattern.")
    existing = db.scalar(
        select(PatternProblem).where(
            PatternProblem.problem_id == problem.id,
            PatternProblem.pattern_id == pattern.id,
        )
    )
    if existing is not None:
        return existing
    link = PatternProblem(pattern_id=pattern.id, problem_id=problem.id)
    db.add(link)
    db.flush()
    audit(
        db,
        actor,
        "system_problem.relate",
        "system_problem",
        problem.id,
        {"pattern_id": pattern.id},
    )
    db.commit()
    return link

AI_BANNER = (
    "AI-GENERATED DRAFT — not approved for public sharing. Review and edit before submitting."
)
INTERNAL_HEADINGS = {
    "internal notes",
    "internal note",
    "sources",
    "source documents",
    "architecture",
    "architecture details",
    "technical decisions",
    "technical decisions and risks",
    "risks",
    "credentials",
    "private notes",
}
SENSITIVE_KEYS = {
    "password",
    "new_password",
    "current_password",
    "token",
    "reset_token",
    "csrf_token",
    "smtp_password",
    "authorization",
    "secret",
}
STATUS_LABELS = {
    "draft": "Draft",
    "ready_for_review": "Ready for review",
    "approved": "Approved for public sharing",
    "unpublished": "Unpublished",
    "published": "Published",
    "pending": "Pending confirmation",
    "running": "Running",
    "completed": "Ingested",
    "subscribed": "Subscribed",
    "unsubscribed": "Unsubscribed",
    "suppressed": "Suppressed",
    "queued": "Queued",
    "sending": "Sending",
    "sent": "Sent",
    "partial": "Partial",
    "failed": "Failed",
    "skipped": "Skipped",
}


def label_for(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def iso(value):
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def audit(db: Session, actor, action: str, entity_type: str, entity_id, detail: dict | None = None) -> None:
    clean = {}
    for key, value in (detail or {}).items():
        if str(key).lower() in SENSITIVE_KEYS:
            continue
        clean[key] = value
    db.add(
        AuditLog(
            actor_id=None if actor is None else actor.id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            detail=json.dumps(clean, default=str),
            created_at=utcnow(),
        )
    )


def user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "role": user.role,
        "can_send_public_email": bool(user.can_send_public_email),
        "is_active": bool(user.is_active),
        "created_at": iso(user.created_at),
        "permissions": permission_map(user),
    }


def get_email_settings(db: Session) -> EmailSettings:
    row = db.get(EmailSettings, 1)
    if row is None:
        raise NotFound("Email settings are not initialized.")
    return row


def get_publication_settings(db: Session) -> PublicationSettings:
    row = db.get(PublicationSettings, 1)
    if row is None:
        raise NotFound("Publication settings are not initialized.")
    return row


def email_settings_dict(row: EmailSettings) -> dict:
    return {
        "enabled": bool(row.enabled),
        "provider": row.provider,
        "sender_email": row.sender_email,
        "sender_name": row.sender_name,
        "public_base_url": row.public_base_url,
        "smtp_host": row.smtp_host,
        "smtp_port": row.smtp_port,
        "smtp_username": row.smtp_username,
        "smtp_password_set": bool(row.smtp_password),
        "smtp_use_tls": bool(row.smtp_use_tls),
        "updated_at": iso(row.updated_at),
    }


def publication_settings_dict(row: PublicationSettings) -> dict:
    return {
        "public_site_enabled": bool(row.public_site_enabled),
        "organization_name": row.organization_name,
        "updated_at": iso(row.updated_at),
    }


def _sender(row: EmailSettings) -> str:
    if row.sender_name and row.sender_email:
        return f"{row.sender_name} <{row.sender_email}>"
    return row.sender_email or ""


def _clean_asset(url: str | None) -> str | None:
    if url is None:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise BadRequest("A public asset must be an http or https URL.")
    return cleaned


def _public_excerpt(body: str) -> str:
    kept: list[str] = []
    skip = False
    for line in (body or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().lower()
            skip = heading in INTERNAL_HEADINGS
            continue
        if skip or not stripped:
            continue
        if "http://" in stripped or "https://" in stripped:
            continue
        if re.search(r"(?i)(password|secret|api[_ ]?key|token)\s*[:=]", stripped):
            continue
        kept.append(stripped)
    return re.sub(r"\s+", " ", " ".join(kept)).strip()[:280]


def build_ai_draft(title: str, body: str) -> str:
    excerpt = _public_excerpt(body)
    explanation = excerpt or (
        "Replace this with a short public explanation. Do not copy internal notes, source links, or credentials."
    )
    return (
        f"{AI_BANNER}\n\n"
        f"# {title}\n\n"
        f"## Short explanation\n{explanation}\n\n"
        "## Problem\nThis pattern addresses a recurring design problem. Describe it without internal "
        "architecture, customer names, or private links.\n\n"
        "## High-level example\nAdd a public example. Leave out credentials and internal systems.\n\n"
        "## Benefits and tradeoffs\n- Benefit: \n- Tradeoff: \n\n"
        "## Public references\nAdd only an approved public diagram or article link.\n"
    )


def load_session(db: Session, settings: Settings, raw_token: str | None):
    if not raw_token:
        return None, None
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw_token)))
    now = utcnow()
    if row is None or row.revoked_at is not None:
        return None, None
    idle_deadline = row.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    if row.expires_at <= now or idle_deadline <= now:
        row.revoked_at = now
        db.commit()
        return None, None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        row.revoked_at = now
        db.commit()
        return None, None
    row.last_seen_at = now
    db.commit()
    return user, row


def start_session(db: Session, settings: Settings, user: User) -> tuple[str, str]:
    raw = new_secret()
    csrf = new_secret()
    now = utcnow()
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=hash_token(raw),
            csrf_token=csrf,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_in(hours=settings.session_absolute_hours),
            revoked_at=None,
        )
    )
    db.commit()
    return raw, csrf


def revoke_all_sessions(db: Session, user_id: int) -> None:
    now = utcnow()
    rows = db.scalars(
        select(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    ).all()
    for row in rows:
        row.revoked_at = now


def login_user(db: Session, settings: Settings, limiter: RateLimiter, ip: str, email_raw: str, password: str):
    limiter.hit(
        f"login:{ip}:{(email_raw or '').strip().lower()}",
        settings.login_limit,
        settings.login_window_seconds,
    )
    try:
        email = normalize_email(email_raw)
    except ValueError:
        verify_password(password or "", DUMMY_PASSWORD_HASH)
        logger.info("authentication failed")
        raise Unauthenticated("Invalid email or password.") from None
    user = db.scalar(select(User).where(User.email == email))
    stored = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    if user is None or not user.is_active or not verify_password(password or "", stored):
        logger.info("authentication failed")
        raise Unauthenticated("Invalid email or password.")
    raw, csrf = start_session(db, settings, user)
    return user, raw, csrf


def logout_session(db: Session, session_row: AuthSession) -> None:
    session_row.revoked_at = utcnow()
    db.commit()


def request_password_reset(db: Session, settings: Settings, mail_state: MailState, limiter: RateLimiter, ip: str, email_raw: str) -> None:
    limiter.hit(f"reset:{ip}", settings.reset_limit, settings.reset_window_seconds)
    logger.info("password reset requested")
    try:
        email = normalize_email(email_raw)
    except ValueError:
        return
    user = db.scalar(select(User).where(User.email == email, User.is_active.is_(True)))
    if user is None:
        return
    raw = new_secret()
    now = utcnow()
    token = PasswordResetToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        expires_at=expires_in(minutes=settings.reset_token_minutes),
        used_at=None,
        created_at=now,
    )
    db.add(token)
    db.flush()
    email_settings = get_email_settings(db)
    link = f"{email_settings.public_base_url.rstrip('/')}/reset-password/{raw}"
    sender = _sender(email_settings) or "Knowledge Library <noreply@localhost>"
    text = f"Reset your password. This link expires and can be used once.\n{link}\n"
    html = (
        "<p>Reset your password. This link expires and can be used once.</p>"
        f"<p><a href=\"{escape(link)}\">{escape(link)}</a></p>"
    )
    transactional_provider(email_settings, mail_state).send(
        db,
        Outbound(
            to_email=user.email,
            sender=sender,
            subject="Reset your password",
            text_body=text,
            html_body=html,
            idempotency_key=f"reset-{token.id}",
            kind="transactional",
        ),
    )
    db.commit()


def reset_password(db: Session, raw_token: str, new_password: str) -> None:
    row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw_token or "")))
    now = utcnow()
    if row is None or row.used_at is not None or row.expires_at <= now:
        raise BadRequest("This reset link is invalid or has expired.")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise BadRequest("This reset link is invalid or has expired.")
    try:
        validate_password(new_password, user.email)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    user.password_hash = hash_password(new_password)
    row.used_at = now
    revoke_all_sessions(db, user.id)
    audit(db, user, "user.password_reset", "user", user.id, {})
    db.commit()


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password or "", user.password_hash):
        raise BadRequest("Current password is incorrect.")
    try:
        validate_password(new_password, user.email)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    user.password_hash = hash_password(new_password)
    revoke_all_sessions(db, user.id)
    audit(db, user, "user.password_change", "user", user.id, {})
    db.commit()


def _ensure_other_admin(db: Session, user_id: int) -> None:
    others = db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == "SUPER_ADMIN", User.is_active.is_(True), User.id != user_id)
    )
    if not others:
        raise Conflict("The last active super admin cannot be removed.")


def create_initial_super_admin(db: Session, email_raw: str, password: str) -> User:
    if db.scalar(select(User.id).where(User.role == "SUPER_ADMIN")) is not None:
        raise Conflict("A super admin already exists.")
    try:
        email = normalize_email(email_raw)
        validate_password(password, email)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise Conflict("An account with this email already exists.")
    now = utcnow()
    user = User(
        email=email,
        password_hash=hash_password(password),
        role="SUPER_ADMIN",
        can_send_public_email=False,
        is_active=True,
        created_at=now,
    )
    db.add(user)
    db.flush()
    audit(db, user, "user.bootstrap", "user", user.id, {"email": email, "role": "SUPER_ADMIN"})
    db.commit()
    return user


def create_user(db: Session, actor: User, email_raw: str, password: str, role: str, can_send: bool = False) -> User:
    if not can_admin(actor):
        raise Forbidden()
    if role not in ROLES:
        raise BadRequest("Unknown role.")
    if can_send and role != "EDITOR_PLUS":
        raise BadRequest("Only an editor plus can be granted public send permission.")
    try:
        email = normalize_email(email_raw)
        validate_password(password, email)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise Conflict("A user with that email already exists.")
    user = User(
        email=email,
        password_hash=hash_password(password),
        role=role,
        can_send_public_email=bool(can_send) if role == "EDITOR_PLUS" else False,
        is_active=True,
        created_at=utcnow(),
    )
    db.add(user)
    db.flush()
    audit(
        db,
        actor,
        "user.create",
        "user",
        user.id,
        {"email": email, "role": role, "can_send_public_email": bool(user.can_send_public_email)},
    )
    db.commit()
    return user


def update_user(db: Session, actor: User, user_id: int, changes: dict) -> User:
    if not can_admin(actor):
        raise Forbidden()
    target = db.get(User, user_id)
    if target is None:
        raise NotFound()
    next_role = changes.get("role", target.role)
    if next_role not in ROLES:
        raise BadRequest("Unknown role.")
    next_active = target.is_active if changes.get("is_active") is None else bool(changes["is_active"])
    if target.role == "SUPER_ADMIN" and (next_role != "SUPER_ADMIN" or not next_active):
        _ensure_other_admin(db, target.id)
    send_flag = changes.get("can_send_public_email")
    if send_flag and next_role != "EDITOR_PLUS":
        raise BadRequest("Only an editor plus can be granted public send permission.")
    target.role = next_role
    target.is_active = next_active
    if next_role != "EDITOR_PLUS":
        target.can_send_public_email = False
    elif send_flag is not None:
        target.can_send_public_email = bool(send_flag)
    if changes.get("password"):
        try:
            validate_password(changes["password"], target.email)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        target.password_hash = hash_password(changes["password"])
    revoke_all_sessions(db, target.id)
    audit(
        db,
        actor,
        "user.update",
        "user",
        target.id,
        {
            "role": target.role,
            "is_active": bool(target.is_active),
            "can_send_public_email": bool(target.can_send_public_email),
            "password_changed": bool(changes.get("password")),
        },
    )
    db.commit()
    return target


def list_users(db: Session, actor: User) -> list[User]:
    if not can_admin(actor):
        raise Forbidden()
    return list(db.scalars(select(User).order_by(User.email)).all())


def get_managed_user(db: Session, actor: User, user_id: int) -> User:
    if not can_admin(actor):
        raise Forbidden()
    target = db.get(User, user_id)
    if target is None:
        raise NotFound()
    return target


def customer_dashboard(db: Session, actor: User, subject: User) -> dict:
    """Activity for one account, using that account's own reads and history."""
    if not can_admin(actor):
        raise Forbidden()
    favorites = list_dashboard_favorites(db, subject)
    thread_prefix = f"/users/{subject.id}/threads/"
    for item in favorites:
        href = item.get("href") or ""
        if item.get("kind") == "Answer" and href.startswith("/ask/"):
            item["href"] = thread_prefix + href[len("/ask/") :]
    return {
        "searches": list_recent_searches(db, subject),
        "favorites": favorites,
        "sent_emails": list_sent_emails(db, subject),
        "exercises": _customer_exercises(db, subject),
        "prompts": _customer_prompts(db, subject),
        "threads": list_assistant_threads(db, subject, 12),
        "pages": list(
            db.scalars(
                select(Document)
                .where(Document.created_by == subject.id)
                .order_by(Document.updated_at.desc())
                .limit(12)
            ).all()
        ),
        "ingestion": list(
            db.scalars(
                select(IngestionJob)
                .where(IngestionJob.actor_id == subject.id)
                .order_by(IngestionJob.id.desc())
                .limit(12)
            ).all()
        ),
    }


def _customer_exercises(db: Session, subject: User) -> list[dict]:
    attempts = {
        row.exercise_id: row
        for row in db.scalars(select(ExerciseAttempt).where(ExerciseAttempt.user_id == subject.id)).all()
    }
    notes = {
        row.exercise_id: row
        for row in db.scalars(select(ExerciseNote).where(ExerciseNote.user_id == subject.id)).all()
    }
    rows = []
    for exercise_id in set(attempts) | set(notes):
        exercise = db.get(Exercise, exercise_id)
        if exercise is None:
            continue
        report = exercise_report(db, subject, exercise)
        attempt = attempts.get(exercise_id)
        note = notes.get(exercise_id)
        rows.append(
            {
                "id": exercise.id,
                "title": exercise.title,
                "kind": exercise.kind,
                "summary": report["summary"],
                "note": report["note"],
                "updated_at": attempt.updated_at if attempt is not None else note.updated_at,
            }
        )
    rows.sort(key=lambda row: row["updated_at"], reverse=True)
    return rows[:12]


def _customer_prompts(db: Session, subject: User) -> list[dict]:
    from app.prompts import prompt_by_slug

    rows = []
    for use, pattern in list_prompt_uses(db, subject, limit=12):
        spec = prompt_by_slug(use.slug) or {}
        rows.append(
            {
                "id": use.id,
                "title": spec.get("title") or use.slug,
                "pattern": pattern.name,
                "focus": use.focus,
                "created_at": use.created_at,
            }
        )
    return rows


def customer_thread(db: Session, actor: User, user_id: int, thread_id: int):
    subject = get_managed_user(db, actor, user_id)
    thread = db.get(AssistantThread, thread_id)
    if thread is None or thread.user_id != subject.id:
        raise NotFound()
    messages = list(
        db.scalars(
            select(AssistantMessage).where(AssistantMessage.thread_id == thread.id).order_by(AssistantMessage.id)
        ).all()
    )
    return subject, thread, messages


def customer_prompt(db: Session, actor: User, user_id: int, use_id: int):
    from app.prompts import prompt_by_slug

    subject = get_managed_user(db, actor, user_id)
    use = db.get(PromptUse, use_id)
    if use is None or use.user_id != subject.id:
        raise NotFound()
    pattern = db.get(SharedPattern, use.pattern_id)
    if pattern is None:
        raise NotFound()
    spec = prompt_by_slug(use.slug) or {"slug": use.slug, "title": use.slug, "produces": ""}
    return subject, use, pattern, spec


def _touch_document(db: Session, document: Document, actor: User) -> None:
    document.version += 1
    document.updated_at = utcnow()
    document.updated_by = actor.id
    summaries = db.scalars(select(PublicSummary).where(PublicSummary.document_id == document.id)).all()
    for summary in summaries:
        if summary.source_document_version != document.version:
            summary.outdated = True


def create_document(
    db: Session,
    actor: User,
    title: str,
    body: str,
    publish: bool,
    category: str | None = None,
) -> Document:
    if not can_edit(actor):
        raise Forbidden()
    cleaned = (title or "").strip()
    if not cleaned or len(cleaned) > 300:
        raise BadRequest("A title is required.")
    if body is None:
        body = ""
    if len(body) > 200_000:
        raise BadRequest("The article is too long.")
    now = utcnow()
    document = Document(
        title=cleaned,
        body=body,
        version=1,
        status="published" if publish else "draft",
        category=normalize_category(db, category),
        created_by=actor.id,
        updated_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    db.add(document)
    db.flush()
    audit(db, actor, "document.create", "document", document.id, {"status": document.status, "version": 1})
    db.commit()
    return document


def list_documents(db: Session, actor: User, category: str | None = None) -> list[Document]:
    stmt = select(Document).order_by(Document.updated_at.desc())
    if category is not None:
        stmt = stmt.where(Document.category == normalize_category(db, category))
    if not can_edit(actor):
        stmt = stmt.where(Document.status == "published")
    return list(db.scalars(stmt).all())


def get_document(db: Session, actor: User, document_id: int) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise NotFound()
    if not can_read_document(actor, document):
        raise Forbidden()
    return document


def document_sources(db: Session, document_id: int) -> list[DocumentSource]:
    return list(
        db.scalars(
            select(DocumentSource).where(DocumentSource.document_id == document_id).order_by(DocumentSource.id)
        ).all()
    )


def update_document(
    db: Session,
    actor: User,
    document_id: int,
    title: str | None,
    body: str | None,
    category: str | None = None,
) -> Document:
    if not can_edit(actor):
        raise Forbidden()
    document = get_document(db, actor, document_id)
    changed = False
    if title is not None:
        cleaned = title.strip()
        if not cleaned or len(cleaned) > 300:
            raise BadRequest("A title is required.")
        if cleaned != document.title:
            document.title = cleaned
            changed = True
    if body is not None:
        if len(body) > 200_000:
            raise BadRequest("The article is too long.")
        if body != document.body:
            document.body = body
            changed = True
    if category is not None:
        slug = normalize_category(db, category)
        if slug != document.category:
            document.category = slug
            changed = True
    if changed:
        _touch_document(db, document, actor)
        audit(
            db,
            actor,
            "document.update",
            "document",
            document.id,
            {"version": document.version},
        )
        db.commit()
    return document


def set_document_publication(db: Session, actor: User, document_id: int, published: bool) -> Document:
    if not can_edit(actor):
        raise Forbidden()
    document = get_document(db, actor, document_id)
    document.status = "published" if published else "draft"
    document.updated_at = utcnow()
    document.updated_by = actor.id
    audit(
        db,
        actor,
        "document.publish" if published else "document.unpublish",
        "document",
        document.id,
        {"status": document.status, "version": document.version},
    )
    db.commit()
    return document


def add_source(db: Session, actor: User, document_id: int, label: str, url: str) -> DocumentSource:
    if not can_edit(actor):
        raise Forbidden()
    document = get_document(db, actor, document_id)
    label = (label or "").strip()
    url = (url or "").strip()
    if not label or not url:
        raise BadRequest("A source label and URL are required.")
    source = DocumentSource(
        document_id=document.id,
        label=label[:300],
        url=url[:2000],
        created_by=actor.id,
        created_at=utcnow(),
    )
    db.add(source)
    _touch_document(db, document, actor)
    db.flush()
    audit(db, actor, "source.create", "document_source", source.id, {"document_id": document.id})
    db.commit()
    return source


def delete_source(db: Session, actor: User, source_id: int) -> None:
    source = db.get(DocumentSource, source_id)
    if source is None:
        raise NotFound()
    if not can_edit(actor):
        raise Forbidden()
    document = get_document(db, actor, source.document_id)
    db.delete(source)
    _touch_document(db, document, actor)
    audit(db, actor, "source.delete", "document_source", source_id, {"document_id": document.id})
    db.commit()


def list_sources(db: Session, actor: User) -> list[DocumentSource]:
    if not can_admin(actor):
        raise Forbidden()
    return list(db.scalars(select(DocumentSource).order_by(DocumentSource.id.desc())).all())


def list_visible_sources(db: Session, actor: User) -> list[DocumentSource]:
    document_ids = [document.id for document in list_documents(db, actor)]
    if not document_ids:
        return []
    return list(
        db.scalars(
            select(DocumentSource)
            .where(DocumentSource.document_id.in_(document_ids))
            .order_by(DocumentSource.id.desc())
        ).all()
    )


def search_documents(db: Session, actor: User, query: str) -> list[Document]:
    term = (query or "").strip()
    if not term:
        raise BadRequest("Enter a search term.")
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    stmt = select(Document).where(
        or_(Document.title.ilike(pattern, escape="\\"), Document.body.ilike(pattern, escape="\\"))
    )
    if not can_edit(actor):
        stmt = stmt.where(Document.status == "published")
    return list(db.scalars(stmt.order_by(Document.updated_at.desc()).limit(20)).all())


def retrieve_passages(db: Session, actor: User, query: str) -> list[dict]:
    documents = search_documents(db, actor, query)
    needle = query.strip().lower()
    results = []
    for document in documents:
        haystack = document.body.lower()
        index = haystack.find(needle)
        if index < 0:
            snippet = document.body[:240]
        else:
            start = max(0, index - 80)
            snippet = document.body[start : start + 240]
        results.append(
            {
                "document_id": document.id,
                "title": document.title,
                "status": document.status,
                "snippet": snippet,
            }
        )
    return results


def record_search(db: Session, actor: User, query: str) -> None:
    cleaned = " ".join((query or "").split())[:300]
    if not cleaned:
        return
    db.add(SearchHistory(user_id=actor.id, query=cleaned, created_at=utcnow()))
    db.commit()


def list_recent_searches(db: Session, actor: User, limit: int = 8) -> list[SearchHistory]:
    rows = db.scalars(
        select(SearchHistory).where(SearchHistory.user_id == actor.id).order_by(SearchHistory.id.desc()).limit(40)
    ).all()
    picked = []
    seen = set()
    for row in rows:
        key = row.query.casefold()
        if key in seen:
            continue
        seen.add(key)
        picked.append(row)
        if len(picked) == limit:
            break
    return picked


def is_favorite(db: Session, actor: User, document_id: int) -> bool:
    return (
        db.scalar(select(Favorite.id).where(Favorite.user_id == actor.id, Favorite.document_id == document_id))
        is not None
    )


def toggle_favorite(db: Session, actor: User, document_id: int) -> bool:
    document = get_document(db, actor, document_id)
    existing = db.scalar(select(Favorite).where(Favorite.user_id == actor.id, Favorite.document_id == document.id))
    if existing is not None:
        db.delete(existing)
        db.commit()
        return False
    db.add(Favorite(user_id=actor.id, document_id=document.id, created_at=utcnow()))
    db.commit()
    return True


def is_saved(db: Session, actor: User, kind: str, entity_id: int) -> bool:
    return (
        db.scalar(
            select(LibraryFavorite.id).where(
                LibraryFavorite.user_id == actor.id,
                LibraryFavorite.kind == kind,
                LibraryFavorite.entity_id == entity_id,
            )
        )
        is not None
    )


def saved_ids(db: Session, actor: User, kind: str) -> set[int]:
    return set(
        db.scalars(
            select(LibraryFavorite.entity_id).where(
                LibraryFavorite.user_id == actor.id,
                LibraryFavorite.kind == kind,
            )
        ).all()
    )


def _require_saved_target(db: Session, actor: User, kind: str, entity_id: int) -> None:
    if kind == "exercise":
        get_exercise(db, entity_id)
        return
    if kind == "pattern":
        if db.get(SharedPattern, entity_id) is None:
            raise NotFound()
        return
    if kind == "problem":
        get_system_problem(db, entity_id)
        return
    if kind == "answer":
        message = db.get(AssistantMessage, entity_id)
        if message is None or message.role != "assistant":
            raise NotFound()
        get_assistant_thread(db, actor, message.thread_id)
        return
    raise BadRequest("Choose an exercise, pattern, problem, or answer.")


def toggle_saved(db: Session, actor: User, kind: str, entity_id: int) -> bool:
    _require_saved_target(db, actor, kind, entity_id)
    existing = db.scalar(
        select(LibraryFavorite).where(
            LibraryFavorite.user_id == actor.id,
            LibraryFavorite.kind == kind,
            LibraryFavorite.entity_id == entity_id,
        )
    )
    if existing is not None:
        db.delete(existing)
        db.commit()
        return False
    db.add(LibraryFavorite(user_id=actor.id, kind=kind, entity_id=entity_id, created_at=utcnow()))
    db.commit()
    return True


def list_favorites(db: Session, actor: User, limit: int = 8) -> list[Document]:
    rows = db.scalars(
        select(Favorite).where(Favorite.user_id == actor.id).order_by(Favorite.created_at.desc())
    ).all()
    visible = []
    for row in rows:
        document = db.get(Document, row.document_id)
        if document is not None and can_read_document(actor, document):
            visible.append(document)
        if len(visible) == limit:
            break
    return visible


def list_dashboard_favorites(db: Session, actor: User, limit: int = 20) -> list[dict]:
    """Saved pages plus favorited exercises, patterns, problems, and assistant answers."""
    entries = []
    for row in db.scalars(select(Favorite).where(Favorite.user_id == actor.id)).all():
        document = db.get(Document, row.document_id)
        if document is None or not can_read_document(actor, document):
            continue
        entries.append(
            (
                row.created_at,
                {"title": document.title, "href": f"/documents/{document.id}", "kind": "Page"},
            )
        )
    for row in db.scalars(select(LibraryFavorite).where(LibraryFavorite.user_id == actor.id)).all():
        item = _dashboard_favorite(db, actor, row)
        if item is not None:
            entries.append((row.created_at, item))
    entries.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _created, item in entries[:limit]]


def _dashboard_favorite(db: Session, actor: User, row: LibraryFavorite) -> dict | None:
    if row.kind == "exercise":
        exercise = db.get(Exercise, row.entity_id)
        if exercise is None:
            return None
        return {"title": exercise.title, "href": f"/exercises/{exercise.id}", "kind": "Exercise"}
    if row.kind == "pattern":
        pattern = db.get(SharedPattern, row.entity_id)
        if pattern is None:
            return None
        return {
            "title": pattern.name,
            "href": f"/patterns/{pattern.category_slug}/{pattern.id}",
            "kind": "Pattern",
        }
    if row.kind == "problem":
        problem = db.get(SystemProblem, row.entity_id)
        if problem is None:
            return None
        return {"title": problem.title, "href": f"/problems/{problem.id}", "kind": "Problem"}
    if row.kind == "answer":
        message = db.get(AssistantMessage, row.entity_id)
        if message is None or message.role != "assistant":
            return None
        thread = db.get(AssistantThread, message.thread_id)
        if thread is None or thread.user_id != actor.id:
            return None
        snippet = re.sub(r"\s+", " ", message.body or "").strip()
        title = snippet[:90] or thread.title
        return {"title": title, "href": f"/ask/{thread.id}#message-{message.id}", "kind": "Answer"}
    return None


def list_sent_emails(db: Session, actor: User, limit: int = 8) -> list[EmailCampaign]:
    return list(
        db.scalars(
            select(EmailCampaign)
            .where(EmailCampaign.sent_by == actor.id, EmailCampaign.sent_at.is_not(None))
            .order_by(EmailCampaign.sent_at.desc())
            .limit(limit)
        ).all()
    )


def ensure_practice_exercises(db: Session) -> int:
    """Load the visual match-and-question exercises already in the library."""
    library = Path(__file__).resolve().parent.parent / "data" / "exercise_notes.json"
    if not library.is_file():
        return 0
    from app.practice import load_note_specs

    notes = load_note_specs(library.read_text(encoding="utf-8"))
    columns = {row[1]: row for row in db.execute(text("PRAGMA table_info(exercises)")).all()}
    owner = None
    if columns and columns.get("created_by") is not None and columns["created_by"][3]:
        owner = db.scalar(select(func.min(User.id)))
        if owner is None:
            return 0
    existing = {
        path: row
        for path, row in (
            (row.repository_path, row)
            for row in db.scalars(select(Exercise).where(Exercise.repository_path.is_not(None))).all()
        )
    }
    added = 0
    now = utcnow()
    for repository_path, title, spec in notes:
        encoded = json.dumps(spec, ensure_ascii=False)
        current = existing.get(repository_path)
        if current is None:
            db.add(
                Exercise(
                    title=title,
                    prompt=spec["matchPrompt"],
                    kind="practice",
                    spec=encoded,
                    repository_path=repository_path,
                    created_by=owner,
                    created_at=now,
                )
            )
            added += 1
            continue
        current.title = title
        current.prompt = spec["matchPrompt"]
        current.kind = "practice"
        current.spec = encoded
    db.commit()
    _link_practice_exercises(db)
    return added


def ensure_diagram_exercises(db: Session) -> int:
    """Load a build-the-diagram exercise for every flowchart already in the library."""
    root = Path(__file__).resolve().parent.parent / "data"
    libraries = [root / "system_problem_cases.json", root / "design_concern_cases.json"]
    if not any(path.is_file() for path in libraries):
        return 0
    from app.a3 import case_title
    from app.diagrams import diagram_spec

    columns = {row[1]: row for row in db.execute(text("PRAGMA table_info(exercises)")).all()}
    owner = None
    if columns and columns.get("created_by") is not None and columns["created_by"][3]:
        owner = db.scalar(select(func.min(User.id)))
        if owner is None:
            return 0
    existing = {
        row.repository_path: row
        for row in db.scalars(select(Exercise).where(Exercise.repository_path.is_not(None))).all()
    }
    added = 0
    now = utcnow()
    for library in libraries:
        if not library.is_file():
            continue
        notes = json.loads(library.read_text(encoding="utf-8"))
        for repository_path, body in notes.items():
            spec = diagram_spec(body)
            if spec is None:
                continue
            key = f"{repository_path}#diagram"
            heading = re.sub(r"^Problem\s+\d+\s*:\s*", "", case_title(body) or repository_path)
            heading = re.sub(r"^Concern:\s*", "", heading).strip() or "Diagram"
            title = f"Build the diagram: {heading}"[:300]
            encoded = json.dumps(spec, ensure_ascii=False)
            current = existing.get(key)
            if current is None:
                current = Exercise(
                    title=title,
                    prompt=spec["prompt"],
                    kind="diagram",
                    spec=encoded,
                    repository_path=key,
                    created_by=owner,
                    created_at=now,
                )
                db.add(current)
                existing[key] = current
                added += 1
                continue
            current.title = title
            current.prompt = spec["prompt"]
            current.kind = "diagram"
            current.spec = encoded
    db.commit()
    _link_diagram_exercises(db)
    return added


def _link_diagram_exercises(db: Session) -> None:
    exercises = {
        row.repository_path[: -len("#diagram")]: row
        for row in db.scalars(select(Exercise).where(Exercise.kind == "diagram")).all()
        if row.repository_path and row.repository_path.endswith("#diagram")
    }
    problems = {
        row.repository_path: row
        for row in db.scalars(select(SystemProblem).where(SystemProblem.repository_path.is_not(None))).all()
    }
    concerns = {
        row.repository_path: row
        for row in db.scalars(select(DesignConcern).where(DesignConcern.repository_path.is_not(None))).all()
    }
    problem_patterns: dict[int, list[int]] = {}
    for link in db.scalars(select(PatternProblem)).all():
        problem_patterns.setdefault(link.problem_id, []).append(link.pattern_id)
    concern_patterns: dict[int, list[int]] = {}
    for link in db.scalars(select(PatternConcern)).all():
        concern_patterns.setdefault(link.concern_id, []).append(link.pattern_id)
    patterns = {row.id: row for row in db.scalars(select(SharedPattern)).all()}
    seen = {(row.pattern_id, row.exercise_id) for row in db.scalars(select(PatternExercise)).all()}

    def attach(pattern_id: int, exercise_id: int) -> None:
        pattern = patterns.get(pattern_id)
        if pattern is None or pattern.subcategory in {"Exercises", "Concerns"}:
            return
        key = (pattern_id, exercise_id)
        if key in seen:
            return
        seen.add(key)
        db.add(PatternExercise(pattern_id=pattern_id, exercise_id=exercise_id))

    for path, exercise in exercises.items():
        problem = problems.get(path)
        if problem is not None:
            if problem.shared_pattern_id:
                attach(problem.shared_pattern_id, exercise.id)
            for pattern_id in problem_patterns.get(problem.id, []):
                attach(pattern_id, exercise.id)
        concern = concerns.get(path)
        if concern is not None:
            if concern.shared_pattern_id:
                attach(concern.shared_pattern_id, exercise.id)
            for pattern_id in concern_patterns.get(concern.id, []):
                attach(pattern_id, exercise.id)
    db.commit()


def _link_practice_exercises(db: Session) -> None:
    library = Path(__file__).resolve().parent.parent / "data" / "exercise_notes.json"
    if not library.is_file():
        return
    from app.a3 import resolve_case_path

    notes = json.loads(library.read_text(encoding="utf-8"))
    by_path = {row.repository_path: row for row in db.scalars(select(SharedPattern)).all()}
    problems_by_path = {
        row.repository_path: row
        for row in db.scalars(select(SystemProblem).where(SystemProblem.repository_path.is_not(None))).all()
    }
    related_ids: dict[int, list[int]] = {}
    for link in db.scalars(select(PatternProblem)).all():
        related_ids.setdefault(link.problem_id, []).append(link.pattern_id)
    exercises = {
        row.repository_path: row
        for row in db.scalars(select(Exercise).where(Exercise.repository_path.is_not(None))).all()
    }
    seen = {
        (row.pattern_id, row.exercise_id) for row in db.scalars(select(PatternExercise)).all()
    }

    def attach(pattern_id: int, exercise_id: int) -> None:
        key = (pattern_id, exercise_id)
        if key in seen:
            return
        seen.add(key)
        db.add(PatternExercise(pattern_id=pattern_id, exercise_id=exercise_id))

    for repository_path, body in notes.items():
        exercise = exercises.get(repository_path)
        if exercise is None:
            continue
        for _label, url in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", body or ""):
            pattern = by_path.get(resolve_case_path(repository_path, url))
            if pattern is None or pattern.subcategory in {"Exercises", "Concerns"}:
                continue
            attach(pattern.id, exercise.id)
            problem = problems_by_path.get(pattern.repository_path)
            if problem is None:
                continue
            for pattern_id in related_ids.get(problem.id, []):
                attach(pattern_id, exercise.id)
    db.commit()


def list_pattern_exercises(db: Session, pattern_id: int) -> list[Exercise]:
    return list(
        db.scalars(
            select(Exercise)
            .join(PatternExercise, PatternExercise.exercise_id == Exercise.id)
            .where(PatternExercise.pattern_id == pattern_id, Exercise.kind.in_(("practice", "diagram")))
            .order_by(Exercise.title, Exercise.id)
        ).all()
    )


_TECHNOLOGIES = (
    "Google Cloud",
    "PostgreSQL",
    "Elasticsearch",
    "Kubernetes",
    "DynamoDB",
    "BigQuery",
    "Snowflake",
    "RabbitMQ",
    "Memcached",
    "MongoDB",
    "GraphQL",
    "Cassandra",
    "Cloudflare",
    "Terraform",
    "MySQL",
    "Kafka",
    "Redis",
    "Nginx",
    "Docker",
    "Lambda",
    "Azure",
    "gRPC",
    "etcd",
    "Lua",
    "AWS",
    "GCP",
    "SQS",
    "SNS",
    "S3",
)


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _technologies_in(text: str) -> list[str]:
    found = []
    for name in _TECHNOLOGIES:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text or ""):
            found.append(name)
    return found


def _mix_pattern_cells(body: str) -> list[str]:
    match = re.search(r"## Pattern Mix\s*(.*?)(?=\n## |\Z)", body or "", re.S)
    if not match:
        return []
    cells = []
    for line in match.group(1).splitlines():
        if not line.startswith("|") or re.match(r"^\|\s*-+", line) or line.lower().startswith("| concern"):
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) >= 2 and parts[1]:
            cells.append(parts[1])
    return cells


def _pattern_from_link(by_path, by_name, case_path: str, label: str, url: str) -> SharedPattern | None:
    from app.a3 import resolve_case_path

    resolved = resolve_case_path(case_path, url)
    pattern = by_path.get(resolved) or by_name.get(_name_key(label))
    if pattern is None:
        filename = url.split("?", 1)[0].split("#", 1)[0].rstrip("/").split("/")[-1]
        if filename.endswith(".md"):
            stem = re.sub(r"^\d+-", "", filename[:-3]).replace("-", "").replace("_", "")
            stem = {"pubsub": "publishsubscribe"}.get(stem, stem)
            pattern = by_name.get(stem)
    if pattern is None or pattern.subcategory in {"Problems", "Concerns", "Exercises"}:
        return None
    return pattern


def _exercise_focus(db: Session) -> dict[int, tuple[list[SharedPattern], list[str]]]:
    """Patterns an exercise tackles, plus a technology when the case names one."""
    notes_path = Path(__file__).resolve().parent.parent / "data" / "exercise_notes.json"
    cases_path = Path(__file__).resolve().parent.parent / "data" / "system_problem_cases.json"
    if not notes_path.is_file() or not cases_path.is_file():
        return {}
    from app.a3 import resolve_case_path

    notes = json.loads(notes_path.read_text(encoding="utf-8"))
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    shared = list(db.scalars(select(SharedPattern)).all())
    by_path = {row.repository_path: row for row in shared}
    by_name: dict[str, SharedPattern] = {}
    for row in shared:
        if row.subcategory in {"Problems", "Concerns", "Exercises"}:
            continue
        by_name.setdefault(_name_key(row.name), row)
    exercises = {
        row.repository_path: row.id
        for row in db.scalars(select(Exercise).where(Exercise.repository_path.is_not(None))).all()
    }
    focus: dict[int, tuple[list[SharedPattern], list[str]]] = {}
    for repository_path, body in notes.items():
        exercise_id = exercises.get(repository_path)
        if exercise_id is None:
            continue
        case_paths = []
        for _label, url in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", body or ""):
            resolved = resolve_case_path(repository_path, url)
            if resolved in cases and not resolved.endswith("/INDEX.md") and resolved not in case_paths:
                case_paths.append(resolved)
        patterns: dict[int, SharedPattern] = {}
        technologies: list[str] = []
        seen_tech: set[str] = set()
        for case_path in case_paths:
            for cell in _mix_pattern_cells(cases[case_path]):
                for label, url in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", cell):
                    pattern = _pattern_from_link(by_path, by_name, case_path, label, url)
                    if pattern is not None:
                        patterns[pattern.id] = pattern
                    if pattern is None or _name_key(label) != _name_key(pattern.name):
                        for name in _technologies_in(label):
                            if name not in seen_tech:
                                seen_tech.add(name)
                                technologies.append(name)
                plain = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", cell)
                for name in _technologies_in(plain):
                    if name not in seen_tech and _name_key(name) not in {_name_key(row.name) for row in patterns.values()}:
                        seen_tech.add(name)
                        technologies.append(name)
        ordered = sorted(patterns.values(), key=lambda row: (row.name.lower(), row.id))
        focus[exercise_id] = (ordered, technologies)
    return focus


def list_exercise_catalog(
    db: Session, actor: User
) -> list[tuple[Exercise, ExerciseAttempt | None, list[SharedPattern], list[str]]]:
    focus = _exercise_focus(db)
    linked: dict[int, list[SharedPattern]] = {}
    rows = db.execute(
        select(PatternExercise.exercise_id, SharedPattern)
        .join(SharedPattern, SharedPattern.id == PatternExercise.pattern_id)
        .order_by(SharedPattern.name, SharedPattern.id)
    ).all()
    for exercise_id, pattern in rows:
        bucket = linked.setdefault(exercise_id, [])
        if all(item.id != pattern.id for item in bucket):
            bucket.append(pattern)
    catalog = []
    for exercise, attempt in list_exercises(db, actor):
        patterns, technologies = focus.get(exercise.id, ([], []))
        if not patterns:
            patterns = linked.get(exercise.id, [])
        catalog.append((exercise, attempt, patterns, technologies))
    return catalog


def practice_view(exercise: Exercise) -> dict | None:
    if exercise.kind != "practice":
        return None
    from app.practice import public_practice_spec

    try:
        spec = json.loads(exercise.spec or "{}")
    except json.JSONDecodeError:
        return None
    return public_practice_spec(spec)


def diagram_view(exercise: Exercise) -> dict | None:
    if exercise.kind != "diagram":
        return None
    from app.diagrams import public_diagram_spec

    try:
        spec = json.loads(exercise.spec or "{}")
    except json.JSONDecodeError:
        return None
    return public_diagram_spec(spec)


def grade_practice_exercise(db: Session, actor: User, exercise: Exercise, links, answers) -> dict:
    if exercise.kind not in {"practice", "diagram"}:
        raise BadRequest("This exercise has no matching board.")
    try:
        spec = json.loads(exercise.spec or "{}")
    except json.JSONDecodeError:
        raise BadRequest("This exercise is not ready.") from None
    if not isinstance(links, list) or not isinstance(answers, dict):
        raise BadRequest("Send the matches and the questionnaire answers.")
    attempt = start_exercise(db, actor, exercise.id)
    if exercise.kind == "diagram":
        from app.diagrams import grade_diagram

        grade = grade_diagram(spec, links)
    else:
        from app.practice import grade_practice

        grade = grade_practice(spec, links, answers)
    attempt.result = json.dumps(
        {
            "match": grade["match"],
            "questions": grade["questions"],
            "questionsCorrect": grade["questionsCorrect"],
            "questionsTotal": grade["questionsTotal"],
            "passed": grade["passed"],
            "links": [
                {"source": str(link.get("source") or ""), "target": str(link.get("target") or "")}
                for link in links
                if isinstance(link, dict)
            ],
            "answers": {
                str(key): value
                for key, value in answers.items()
                if isinstance(key, str)
            },
        },
        ensure_ascii=False,
    )
    attempt.updated_at = utcnow()
    db.commit()
    return grade


def exercise_note(db: Session, actor: User, exercise_id: int) -> ExerciseNote | None:
    return db.scalar(
        select(ExerciseNote).where(
            ExerciseNote.user_id == actor.id,
            ExerciseNote.exercise_id == exercise_id,
        )
    )


def save_exercise_note(db: Session, actor: User, exercise: Exercise, body: str) -> ExerciseNote:
    cleaned = (body or "").strip()[:4000]
    row = exercise_note(db, actor, exercise.id)
    if row is None:
        row = ExerciseNote(
            user_id=actor.id,
            exercise_id=exercise.id,
            body=cleaned,
            updated_at=utcnow(),
        )
        db.add(row)
    else:
        row.body = cleaned
        row.updated_at = utcnow()
    db.commit()
    return row


def exercise_report(db: Session, actor: User, exercise: Exercise) -> dict:
    """The signed-in user's notes and last check, with labels and without the answer key."""
    note = exercise_note(db, actor, exercise.id)
    attempt = db.scalar(
        select(ExerciseAttempt).where(
            ExerciseAttempt.user_id == actor.id,
            ExerciseAttempt.exercise_id == exercise.id,
        )
    )
    saved = _saved_result(attempt)
    try:
        spec = json.loads(exercise.spec or "{}")
    except json.JSONDecodeError:
        spec = {}
    lines: list[str] = []
    questions: list[dict] = []
    blocks: list[dict] = []
    summary = "Not checked yet."
    if exercise.kind == "diagram":
        labels = {node["id"]: node["label"] for node in spec.get("nodes") or []}
        blocks = [
            {"label": node["label"], "explain": node.get("explain") or ""}
            for node in spec.get("nodes") or []
        ]
        if saved:
            match = saved["match"]
            summary = f"Lines {match['correct']} of {match['total']}."
            summary += " The diagram is complete." if saved.get("passed") else " The diagram is not complete."
            for link in saved.get("links") or []:
                source = labels.get(link.get("source"), "")
                target = labels.get(link.get("target"), "")
                if source and target:
                    lines.append(f"{source} -> {target}")
    elif exercise.kind == "practice" and saved:
        match = saved["match"]
        summary = (
            f"Matches {match['correct']} of {match['total']}. "
            f"Questions {saved['questionsCorrect']} of {saved['questionsTotal']}."
        )
        summary += " Completed." if saved.get("passed") else " Not complete."
        verdicts = saved.get("questions") or {}
        given = saved.get("answers") or {}
        for question in spec.get("questions") or []:
            choice = _choice_text(question, given.get(question["id"]))
            questions.append(
                {
                    "prompt": question.get("prompt") or "",
                    "choice": choice,
                    "ok": verdicts.get(question["id"]) is True,
                }
            )
    return {
        "title": exercise.title,
        "kind": exercise.kind,
        "prompt": (exercise.prompt or "")[:4000],
        "note": note.body if note and note.body else "",
        "summary": summary,
        "checked": saved is not None,
        "passed": bool(saved and saved.get("passed")),
        "lines": lines,
        "questions": questions,
        "blocks": blocks,
    }


def _saved_result(attempt: ExerciseAttempt | None) -> dict | None:
    if attempt is None or not attempt.result or attempt.result == "{}":
        return None
    try:
        data = json.loads(attempt.result)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "match" not in data:
        return None
    return data


def _choice_text(question: dict, given) -> str:
    choices = question.get("choices") or []
    try:
        index = int(given)
    except (TypeError, ValueError):
        return "No answer"
    if 0 <= index < len(choices):
        return str(choices[index])
    return "No answer"


def create_exercise(db: Session, actor: User, title: str, prompt: str) -> Exercise:
    if not can_edit(actor):
        raise Forbidden()
    cleaned = (title or "").strip()
    if not cleaned:
        raise BadRequest("A title is required.")
    exercise = Exercise(
        title=cleaned[:300],
        prompt=(prompt or "").strip()[:8000],
        created_by=actor.id,
        created_at=utcnow(),
    )
    db.add(exercise)
    db.flush()
    audit(db, actor, "exercise.create", "exercise", exercise.id, {"title": exercise.title})
    db.commit()
    return exercise


def list_exercises(db: Session, actor: User) -> list[tuple[Exercise, ExerciseAttempt | None]]:
    exercises = list(db.scalars(select(Exercise).order_by(Exercise.id.desc())).all())
    attempts = {
        attempt.exercise_id: attempt
        for attempt in db.scalars(select(ExerciseAttempt).where(ExerciseAttempt.user_id == actor.id)).all()
    }
    return [(exercise, attempts.get(exercise.id)) for exercise in exercises]


def get_exercise(db: Session, exercise_id: int) -> Exercise:
    exercise = db.get(Exercise, exercise_id)
    if exercise is None:
        raise NotFound()
    return exercise


def start_exercise(db: Session, actor: User, exercise_id: int) -> ExerciseAttempt:
    exercise = get_exercise(db, exercise_id)
    now = utcnow()
    attempt = db.scalar(
        select(ExerciseAttempt).where(
            ExerciseAttempt.user_id == actor.id,
            ExerciseAttempt.exercise_id == exercise.id,
        )
    )
    if attempt is None:
        attempt = ExerciseAttempt(
            user_id=actor.id,
            exercise_id=exercise.id,
            status="started",
            started_at=now,
            updated_at=now,
        )
        db.add(attempt)
    else:
        attempt.status = "started"
        attempt.updated_at = now
    db.commit()
    return attempt


def list_started_exercises(db: Session, actor: User, limit: int = 8) -> list[tuple[Exercise, ExerciseAttempt]]:
    attempts = db.scalars(
        select(ExerciseAttempt)
        .where(ExerciseAttempt.user_id == actor.id, ExerciseAttempt.status == "started")
        .order_by(ExerciseAttempt.updated_at.desc())
        .limit(limit)
    ).all()
    started = []
    for attempt in attempts:
        exercise = db.get(Exercise, attempt.exercise_id)
        if exercise is not None:
            started.append((exercise, attempt))
    return started


def _assistant_answer(db: Session, actor: User, question: str) -> str:
    passages = retrieve_passages(db, actor, question)
    if not passages:
        return "Nothing in the pages you can read matches that question."
    lines = ["From the pages you can read:"]
    for passage in passages[:3]:
        snippet = " ".join(passage["snippet"].split())[:180]
        lines.append(f"{passage['title']}: {snippet}")
    return "\n".join(lines)


def ask_assistant(db: Session, actor: User, question: str, thread_id: int | None = None) -> AssistantThread:
    cleaned = " ".join((question or "").split())
    if not cleaned:
        raise BadRequest("Enter a question.")
    if len(cleaned) > 500:
        raise BadRequest("Question is too long.")
    now = utcnow()
    if thread_id is None:
        thread = AssistantThread(user_id=actor.id, title=cleaned[:120], created_at=now, updated_at=now)
        db.add(thread)
        db.flush()
    else:
        thread = db.get(AssistantThread, thread_id)
        if thread is None or thread.user_id != actor.id:
            raise NotFound()
        thread.updated_at = now
    answer = _assistant_answer(db, actor, cleaned)
    db.add(AssistantMessage(thread_id=thread.id, role="user", body=cleaned, created_at=now))
    db.add(AssistantMessage(thread_id=thread.id, role="assistant", body=answer, created_at=now))
    db.commit()
    return thread


def list_assistant_threads(db: Session, actor: User, limit: int = 20) -> list[AssistantThread]:
    return list(
        db.scalars(
            select(AssistantThread)
            .where(AssistantThread.user_id == actor.id)
            .order_by(AssistantThread.updated_at.desc())
            .limit(limit)
        ).all()
    )


def get_assistant_thread(db: Session, actor: User, thread_id: int) -> tuple[AssistantThread, list[AssistantMessage]]:
    thread = db.get(AssistantThread, thread_id)
    if thread is None or thread.user_id != actor.id:
        raise NotFound()
    messages = list(
        db.scalars(
            select(AssistantMessage).where(AssistantMessage.thread_id == thread.id).order_by(AssistantMessage.id)
        ).all()
    )
    return thread, messages


def create_ingestion_job(
    db: Session,
    actor: User,
    title: str,
    body: str,
    source_label: str,
    source_url: str,
    publish: bool,
) -> IngestionJob:
    if not can_ingest(actor):
        raise Forbidden()
    cleaned = (title or "").strip()
    if not cleaned:
        raise BadRequest("A title is required.")
    job = IngestionJob(
        actor_id=actor.id,
        title=cleaned[:300],
        body=body or "",
        source_label=(source_label or "")[:300],
        source_url=(source_url or "")[:2000],
        publish=bool(publish),
        status="pending",
        document_id=None,
        error="",
        created_at=utcnow(),
        finished_at=None,
    )
    db.add(job)
    db.commit()
    return job


def process_ingestion_jobs(db: Session) -> int:
    jobs = list(db.scalars(select(IngestionJob).where(IngestionJob.status == "pending").order_by(IngestionJob.id)).all())
    for job in jobs:
        job.status = "running"
        db.commit()
        actor = db.get(User, job.actor_id)
        if actor is None or not actor.is_active or not can_ingest(actor):
            job.status = "failed"
            job.error = "Actor is not authorized to ingest knowledge."
            job.finished_at = utcnow()
            db.commit()
            continue
        now = utcnow()
        document = Document(
            title=job.title,
            body=job.body,
            version=1,
            status="published" if job.publish else "draft",
            category="architectural-patterns",
            created_by=actor.id,
            updated_by=actor.id,
            created_at=now,
            updated_at=now,
        )
        db.add(document)
        db.flush()
        if job.source_label and job.source_url:
            db.add(
                DocumentSource(
                    document_id=document.id,
                    label=job.source_label,
                    url=job.source_url,
                    created_by=actor.id,
                    created_at=now,
                )
            )
        job.document_id = document.id
        job.status = "completed"
        job.finished_at = utcnow()
        audit(db, actor, "document.ingest", "document", document.id, {"job_id": job.id, "version": 1})
        db.commit()
    return len(jobs)


def get_ingestion_job(db: Session, actor: User, job_id: int) -> IngestionJob:
    job = db.get(IngestionJob, job_id)
    if job is None:
        raise NotFound()
    if job.actor_id != actor.id and not can_admin(actor):
        raise Forbidden()
    return job


def get_summary_row(db: Session, summary_id: int) -> PublicSummary:
    return _summary_or_404(db, summary_id)


def _summary_or_404(db: Session, summary_id: int) -> PublicSummary:
    summary = db.get(PublicSummary, summary_id)
    if summary is None:
        raise NotFound()
    return summary


def _new_summary(db: Session, actor: User, document: Document, title: str, body: str, asset: str | None, ai: bool) -> PublicSummary:
    if len(body) > 20_000:
        raise BadRequest("The summary is too long.")
    now = utcnow()
    summary = PublicSummary(
        document_id=document.id,
        source_document_version=document.version,
        title=(title or document.title)[:300],
        body=body,
        public_asset_url=_clean_asset(asset),
        status="draft",
        slug=None,
        ai_generated=ai,
        outdated=False,
        publicly_listed=False,
        published_version_id=None,
        version_number=1,
        created_by=actor.id,
        updated_by=actor.id,
        approved_by=None,
        approved_at=None,
        published_at=None,
        created_at=now,
        updated_at=now,
    )
    db.add(summary)
    db.flush()
    return summary


def create_manual_summary(db: Session, actor: User, document_id: int, title: str, body: str, asset: str | None) -> PublicSummary:
    if not can_edit(actor):
        raise Forbidden()
    document = get_document(db, actor, document_id)
    summary = _new_summary(db, actor, document, title, body or "", asset, False)
    audit(db, actor, "summary.create", "public_summary", summary.id, {"document_id": document.id, "ai_generated": False})
    db.commit()
    return summary


def generate_summary_draft(db: Session, actor: User, document_id: int) -> PublicSummary:
    if not can_edit(actor):
        raise Forbidden()
    document = get_document(db, actor, document_id)
    summary = _new_summary(db, actor, document, document.title, build_ai_draft(document.title, document.body), None, True)
    audit(
        db,
        actor,
        "summary.generate_draft",
        "public_summary",
        summary.id,
        {"document_id": document.id, "source_document_version": document.version, "ai_generated": True},
    )
    db.commit()
    return summary


def update_summary(
    db: Session,
    actor: User,
    summary_id: int,
    title: str | None,
    body: str | None,
    asset: str | None,
    asset_supplied: bool,
) -> PublicSummary:
    if not can_edit(actor):
        raise Forbidden()
    summary = _summary_or_404(db, summary_id)
    changed = False
    if title is not None and title != summary.title:
        cleaned = title.strip()
        if not cleaned:
            raise BadRequest("A title is required.")
        summary.title = cleaned[:300]
        changed = True
    if body is not None and body != summary.body:
        if len(body) > 20_000:
            raise BadRequest("The summary is too long.")
        summary.body = body
        changed = True
    if asset_supplied:
        cleaned_asset = _clean_asset(asset)
        if cleaned_asset != summary.public_asset_url:
            summary.public_asset_url = cleaned_asset
            changed = True
    if changed:
        if summary.status != "draft":
            summary.status = "draft"
        summary.version_number += 1
        summary.updated_by = actor.id
        summary.updated_at = utcnow()
        audit(
            db,
            actor,
            "summary.edit",
            "public_summary",
            summary.id,
            {"version_number": summary.version_number, "status": summary.status},
        )
        db.commit()
    return summary


def submit_summary(db: Session, actor: User, summary_id: int) -> PublicSummary:
    if not can_edit(actor):
        raise Forbidden()
    summary = _summary_or_404(db, summary_id)
    if summary.status != "draft":
        raise Conflict("Only a draft can be submitted for review.")
    if len(summary.title.strip()) < 3 or len(summary.body.strip()) < 20:
        raise BadRequest("Add a title and a public summary before submitting.")
    _clean_asset(summary.public_asset_url)
    summary.status = "ready_for_review"
    summary.updated_by = actor.id
    summary.updated_at = utcnow()
    audit(db, actor, "summary.submit_review", "public_summary", summary.id, {"version_number": summary.version_number})
    db.commit()
    return summary


def _assign_slug(db: Session, summary: PublicSummary) -> None:
    if summary.slug:
        return
    base = re.sub(r"[^a-z0-9]+", "-", summary.title.lower()).strip("-")[:80] or "summary"
    slug = base
    number = 2
    while db.scalar(select(PublicSummary.id).where(PublicSummary.slug == slug)):
        slug = f"{base}-{number}"
        number += 1
    summary.slug = slug


def approve_summary(db: Session, actor: User, summary_id: int) -> PublicSummary:
    if not can_approve(actor):
        raise Forbidden()
    summary = _summary_or_404(db, summary_id)
    document = db.get(Document, summary.document_id)
    if document is None:
        raise NotFound()
    if summary.status != "ready_for_review":
        raise Conflict("Submit the summary for review before approval.")
    now = utcnow()
    version = PublicSummaryVersion(
        summary_id=summary.id,
        version_number=summary.version_number,
        title=summary.title,
        body=summary.body,
        public_asset_url=summary.public_asset_url,
        author_id=summary.updated_by or summary.created_by,
        reviewer_id=actor.id,
        created_at=now,
    )
    db.add(version)
    db.flush()
    _assign_slug(db, summary)
    summary.published_version_id = version.id
    summary.publicly_listed = True
    summary.status = "approved"
    summary.approved_by = actor.id
    summary.approved_at = now
    summary.published_at = now
    summary.source_document_version = document.version
    summary.outdated = False
    summary.updated_at = now
    audit(
        db,
        actor,
        "summary.approve",
        "public_summary",
        summary.id,
        {"version_id": version.id, "version_number": version.version_number, "slug": summary.slug},
    )
    db.commit()
    return summary


def unpublish_summary(db: Session, actor: User, summary_id: int) -> PublicSummary:
    if not can_approve(actor):
        raise Forbidden()
    summary = _summary_or_404(db, summary_id)
    if summary.published_version_id is None and not summary.publicly_listed:
        raise Conflict("This summary has not been published.")
    summary.publicly_listed = False
    if summary.status == "approved":
        summary.status = "unpublished"
    summary.updated_by = actor.id
    summary.updated_at = utcnow()
    audit(db, actor, "summary.unpublish", "public_summary", summary.id, {"version_number": summary.version_number})
    db.commit()
    return summary


def list_document_summaries(db: Session, actor: User, document_id: int) -> list[PublicSummary]:
    get_document(db, actor, document_id)
    stmt = select(PublicSummary).where(PublicSummary.document_id == document_id).order_by(PublicSummary.updated_at.desc())
    if not can_edit(actor):
        stmt = stmt.where(PublicSummary.publicly_listed.is_(True))
    return list(db.scalars(stmt).all())


def list_summaries(db: Session, actor: User, status: str | None = None) -> list[PublicSummary]:
    if not can_edit(actor):
        raise Forbidden()
    stmt = select(PublicSummary).order_by(PublicSummary.updated_at.desc())
    if status:
        stmt = stmt.where(PublicSummary.status == status)
    return list(db.scalars(stmt).all())


def published_version(db: Session, summary: PublicSummary) -> PublicSummaryVersion | None:
    if summary.published_version_id is None:
        return None
    return db.get(PublicSummaryVersion, summary.published_version_id)


def summary_dict(db: Session, actor: User, summary: PublicSummary) -> dict:
    version = published_version(db, summary)
    approver = db.get(User, summary.approved_by) if summary.approved_by else None
    if can_edit(actor):
        return {
            "id": summary.id,
            "document_id": summary.document_id,
            "source_document_version": summary.source_document_version,
            "title": summary.title,
            "body": summary.body,
            "public_asset_url": summary.public_asset_url,
            "status": summary.status,
            "status_label": label_for(summary.status),
            "slug": summary.slug,
            "ai_generated": bool(summary.ai_generated),
            "outdated": bool(summary.outdated),
            "publicly_listed": bool(summary.publicly_listed),
            "version_number": summary.version_number,
            "created_by": summary.created_by,
            "approved_by": None if approver is None else approver.email,
            "approved_at": iso(summary.approved_at),
            "published_at": iso(summary.published_at),
            "published_snapshot": None
            if version is None
            else {
                "id": version.id,
                "version_number": version.version_number,
                "title": version.title,
                "body": version.body,
                "public_asset_url": version.public_asset_url,
                "created_at": iso(version.created_at),
            },
        }
    if not summary.publicly_listed or version is None:
        raise Forbidden()
    return public_summary_dict(summary, version)


def public_summary_dict(summary: PublicSummary, version: PublicSummaryVersion) -> dict:
    return {
        "slug": summary.slug,
        "title": version.title,
        "body": version.body,
        "public_asset_url": version.public_asset_url,
        "published_at": iso(summary.published_at),
        "version_number": version.version_number,
        "status": "approved",
        "status_label": label_for("approved"),
    }


def get_public_summary(db: Session, slug: str) -> tuple[PublicSummary, PublicSummaryVersion]:
    if not get_publication_settings(db).public_site_enabled:
        raise NotFound()
    summary = db.scalar(select(PublicSummary).where(PublicSummary.slug == slug))
    if summary is None or not summary.publicly_listed or summary.published_version_id is None:
        raise NotFound()
    version = db.get(PublicSummaryVersion, summary.published_version_id)
    if version is None:
        raise NotFound()
    return summary, version


def list_public_summaries(db: Session) -> list[dict]:
    if not get_publication_settings(db).public_site_enabled:
        raise NotFound()
    rows = db.scalars(
        select(PublicSummary)
        .where(PublicSummary.publicly_listed.is_(True), PublicSummary.published_version_id.is_not(None))
        .order_by(PublicSummary.published_at.desc())
    ).all()
    payload = []
    for summary in rows:
        version = db.get(PublicSummaryVersion, summary.published_version_id)
        if version is None:
            continue
        item = public_summary_dict(summary, version)
        item["excerpt"] = version.body[:240]
        payload.append(item)
    return payload


def document_dict(db: Session, document: Document) -> dict:
    return {
        "id": document.id,
        "title": document.title,
        "body": document.body,
        "version": document.version,
        "status": document.status,
        "category": document.category,
        "created_by": document.created_by,
        "updated_by": document.updated_by,
        "created_at": iso(document.created_at),
        "updated_at": iso(document.updated_at),
        "sources": [
            {"id": source.id, "label": source.label, "url": source.url}
            for source in document_sources(db, document.id)
        ],
    }


def _campaign_or_404(db: Session, campaign_id: int) -> EmailCampaign:
    campaign = db.get(EmailCampaign, campaign_id)
    if campaign is None:
        raise NotFound()
    return campaign


def _require_campaign_reader(actor: User) -> None:
    if not can_create_campaign(actor):
        raise Forbidden()


def create_campaign(
    db: Session,
    actor: User,
    subject: str,
    email_type: str,
    summary_ids: list[int],
    recipient_emails: list[str],
    audience: str | None,
) -> EmailCampaign:
    if not can_create_campaign(actor):
        raise Forbidden()
    if email_type not in {"share", "newsletter"}:
        raise BadRequest("Email type must be an individual share or a newsletter.")
    subject = (subject or "").strip()
    if not subject:
        raise BadRequest("A subject is required.")
    if not summary_ids:
        raise BadRequest("Select at least one approved public summary.")
    version_ids = []
    for summary_id in summary_ids:
        summary = _summary_or_404(db, summary_id)
        if not summary.publicly_listed or summary.published_version_id is None:
            raise Conflict("Only an approved, published public summary can be emailed.")
        version_ids.append(summary.published_version_id)
    if email_type == "share":
        if audience:
            raise BadRequest("An individual share cannot use the newsletter audience.")
        emails = []
        seen = set()
        for raw in recipient_emails:
            try:
                email = normalize_email(raw)
            except ValueError as exc:
                raise BadRequest(str(exc)) from exc
            if email not in seen:
                seen.add(email)
                emails.append(email)
        if not emails:
            raise BadRequest("Add at least one recipient for an individual share.")
        spec = {"emails": emails}
    else:
        if recipient_emails:
            raise BadRequest("A newsletter uses subscribers. It does not take one-time share addresses.")
        if audience != "all_subscribed":
            raise BadRequest("Choose the subscribed audience for a newsletter.")
        spec = {"audience": "all_subscribed"}
    campaign = EmailCampaign(
        subject=subject[:300],
        email_type=email_type,
        summary_version_ids=json.dumps(version_ids),
        recipient_spec=json.dumps(spec),
        created_by=actor.id,
        sent_by=None,
        status="draft",
        created_at=utcnow(),
        previewed_at=None,
        scheduled_at=None,
        sent_at=None,
    )
    db.add(campaign)
    db.flush()
    audit(
        db,
        actor,
        "email.campaign_create",
        "email_campaign",
        campaign.id,
        {"email_type": email_type, "summary_version_ids": version_ids},
    )
    db.commit()
    return campaign


def eligible_recipients(db: Session, campaign: EmailCampaign) -> list[dict]:
    spec = json.loads(campaign.recipient_spec)
    if campaign.email_type == "share":
        recipients = []
        for email in spec.get("emails", []):
            subscriber = db.scalar(select(Subscriber).where(Subscriber.email == email))
            if subscriber is not None and subscriber.status == "suppressed":
                continue
            recipients.append({"email": email, "subscriber_id": None})
        return recipients
    rows = db.scalars(select(Subscriber).where(Subscriber.status == "subscribed").order_by(Subscriber.email)).all()
    return [{"email": row.email, "subscriber_id": row.id} for row in rows]


def campaign_items(db: Session, campaign: EmailCampaign) -> list[dict]:
    settings = get_email_settings(db)
    items = []
    for version_id in json.loads(campaign.summary_version_ids):
        version = db.get(PublicSummaryVersion, int(version_id))
        if version is None:
            raise Conflict("An approved summary version is missing.")
        summary = db.get(PublicSummary, version.summary_id)
        if summary is None or not summary.publicly_listed or summary.published_version_id != version.id:
            raise Conflict("Email can only include the current approved public version.")
        link = None
        if summary.slug:
            link = f"{settings.public_base_url.rstrip('/')}/public/summaries/{summary.slug}"
        items.append(
            {
                "title": version.title,
                "body": version.body,
                "asset": version.public_asset_url,
                "link": link,
                "version_number": version.version_number,
                "version_id": version.id,
                "outdated": bool(summary.outdated),
                "slug": summary.slug,
            }
        )
    return items


def render_campaign(sender: str, campaign: EmailCampaign, items: list[dict], unsubscribe_url: str | None) -> tuple[str, str, list[str]]:
    text_lines: list[str] = []
    html_parts = ["<html><body>"]
    links = []
    for item in items:
        text_lines.extend(
            [
                item["title"],
                item["body"],
            ]
        )
        html_parts.append(f"<h1>{escape(item['title'])}</h1>")
        html_parts.append("<div>" + str(escape(item["body"])).replace("\n", "<br>") + "</div>")
        if item["asset"]:
            text_lines.append(f"Public diagram: {item['asset']}")
            html_parts.append(f"<p><a href=\"{escape(item['asset'])}\">Public diagram</a></p>")
        if item["link"]:
            text_lines.append(f"Approved summary: {item['link']}")
            html_parts.append(f"<p><a href=\"{escape(item['link'])}\">Read the approved summary</a></p>")
            links.append(item["link"])
        text_lines.append(f"Approved version: {item['version_number']}")
        html_parts.append(f"<p>Approved version {int(item['version_number'])}</p>")
        text_lines.append("")
    if campaign.email_type == "share":
        note = (
            "You received this individual share of an approved public summary. "
            "You were not added to a newsletter."
        )
        text_lines.append(note)
        html_parts.append(f"<p>{escape(note)}</p>")
    else:
        text_lines.append(f"Unsubscribe: {unsubscribe_url}")
        html_parts.append(f"<p><a href=\"{escape(unsubscribe_url or '')}\">Unsubscribe</a></p>")
    html_parts.append("</body></html>")
    return "\n".join(text_lines), "".join(html_parts), links


def preview_campaign(db: Session, actor: User, campaign_id: int) -> dict:
    _require_campaign_reader(actor)
    campaign = _campaign_or_404(db, campaign_id)
    if campaign.status != "draft":
        raise Conflict("Only a draft campaign can be previewed.")
    settings = get_email_settings(db)
    items = campaign_items(db, campaign)
    recipients = eligible_recipients(db, campaign)
    unsubscribe_url = None
    if campaign.email_type == "newsletter":
        unsubscribe_url = f"{settings.public_base_url.rstrip('/')}/unsubscribe/<individual-token>"
    text, html, links = render_campaign(_sender(settings), campaign, items, unsubscribe_url)
    warnings = []
    if not settings.enabled or not settings.sender_email:
        warnings.append("Email sending is disabled until a super admin configures a provider and sender.")
    for item in items:
        if item["outdated"]:
            warnings.append(f"{item['title']} is potentially outdated because its private source changed.")
    campaign.previewed_at = utcnow()
    db.commit()
    return {
        "campaign_id": campaign.id,
        "subject": campaign.subject,
        "sender": _sender(settings) or "(not configured)",
        "text": text,
        "html": html,
        "links": links,
        "recipient_count": len(recipients),
        "recipients": [item["email"] for item in recipients],
        "summary_versions": [item["version_id"] for item in items],
        "version_numbers": [item["version_number"] for item in items],
        "warnings": warnings,
        "email_type": campaign.email_type,
    }


def _ensure_mail_enabled(settings: EmailSettings) -> None:
    if not settings.enabled or not settings.sender_email:
        raise Conflict("Email sending is disabled until a super admin configures a provider and sender.")


def _issue_link(db: Session, settings: Settings, subscriber: Subscriber, purpose: str) -> str:
    raw = new_secret()
    now = utcnow()
    if purpose == "confirm":
        expires = expires_in(hours=settings.confirm_token_hours)
        path = f"/confirm/{raw}"
    else:
        expires = expires_in(days=settings.unsubscribe_token_days)
        path = f"/unsubscribe/{raw}"
    db.add(
        SubscriptionToken(
            subscriber_id=subscriber.id,
            purpose=purpose,
            token_hash=hash_token(raw),
            expires_at=expires,
            used_at=None,
            created_at=now,
        )
    )
    db.flush()
    base = get_email_settings(db).public_base_url.rstrip("/")
    return f"{base}{path}"


def _send_transactional(db: Session, mail_state: MailState, to_email: str, subject: str, text: str, html: str, key: str) -> None:
    settings = get_email_settings(db)
    sender = _sender(settings) or "Knowledge Library <noreply@localhost>"
    transactional_provider(settings, mail_state).send(
        db,
        Outbound(
            to_email=to_email,
            sender=sender,
            subject=subject,
            text_body=text,
            html_body=html,
            idempotency_key=key,
            kind="transactional",
        ),
    )


def _deliver_one(
    db: Session,
    app_settings: Settings,
    mail_state: MailState,
    email_settings: EmailSettings,
    campaign: EmailCampaign,
    delivery: EmailDelivery,
    items: list[dict],
) -> None:
    if delivery.status == "sent" and delivery.provider_message_id:
        return
    subscriber = db.get(Subscriber, delivery.subscriber_id) if delivery.subscriber_id else None
    if subscriber is not None and subscriber.status == "suppressed":
        delivery.status = "skipped"
        delivery.failure = subscriber.suppression_reason or "suppressed"
        return
    if campaign.email_type == "newsletter" and (subscriber is None or subscriber.status != "subscribed"):
        delivery.status = "skipped"
        delivery.failure = "Recipient is not subscribed."
        return
    unsubscribe_url = None
    if campaign.email_type == "newsletter" and subscriber is not None:
        unsubscribe_url = _issue_link(db, app_settings, subscriber, "unsubscribe")
    text, html, _links = render_campaign(_sender(email_settings), campaign, items, unsubscribe_url)
    delivery.attempts += 1
    provider = campaign_provider(email_settings, mail_state)
    try:
        message_id = provider.send(
            db,
            Outbound(
                to_email=delivery.email,
                sender=_sender(email_settings),
                subject=campaign.subject,
                text_body=text,
                html_body=html,
                idempotency_key=f"delivery-{delivery.id}",
                kind="campaign",
            ),
        )
    except DeliveryError as exc:
        delivery.status = "failed"
        delivery.failure = str(exc)[:500]
        if exc.permanent and subscriber is not None:
            subscriber.status = "suppressed"
            subscriber.suppression_reason = "bounce"
        logger.info("campaign %s delivery %s failed", campaign.id, delivery.id)
        return
    delivery.status = "sent"
    delivery.failure = None
    delivery.provider_message_id = message_id
    delivery.sent_at = utcnow()
    logger.info("campaign %s delivery %s sent", campaign.id, delivery.id)


def _finalize_campaign(campaign: EmailCampaign, deliveries: list[EmailDelivery]) -> None:
    statuses = {item.status for item in deliveries}
    if "failed" in statuses and "sent" in statuses:
        campaign.status = "partial"
    elif "failed" in statuses:
        campaign.status = "failed"
    elif "sent" in statuses:
        campaign.status = "sent"
    elif statuses and statuses <= {"skipped"}:
        campaign.status = "failed"
    else:
        campaign.status = "sent"
    campaign.sent_at = utcnow()


def send_campaign(db: Session, app_settings: Settings, mail_state: MailState, actor: User, campaign_id: int, confirm: bool) -> EmailCampaign:
    if not can_send_email(actor):
        raise Forbidden()
    campaign = _campaign_or_404(db, campaign_id)
    if not confirm:
        raise BadRequest("Confirm the send before queueing email.")
    if campaign.previewed_at is None:
        raise BadRequest("Preview the campaign before sending.")
    if campaign.status != "draft":
        raise Conflict("This campaign has already been queued.")
    email_settings = get_email_settings(db)
    _ensure_mail_enabled(email_settings)
    items = campaign_items(db, campaign)
    recipients = eligible_recipients(db, campaign)
    if not recipients:
        raise BadRequest("No eligible recipients.")
    now = utcnow()
    campaign.status = "queued"
    campaign.sent_by = actor.id
    campaign.scheduled_at = now
    db.flush()
    deliveries = []
    for recipient in recipients:
        delivery = EmailDelivery(
            campaign_id=campaign.id,
            subscriber_id=recipient["subscriber_id"],
            email=recipient["email"],
            status="pending",
            provider_message_id=None,
            failure=None,
            attempts=0,
            sent_at=None,
            created_at=now,
        )
        db.add(delivery)
        deliveries.append(delivery)
    db.flush()
    campaign.status = "sending"
    for delivery in deliveries:
        _deliver_one(db, app_settings, mail_state, email_settings, campaign, delivery, items)
    _finalize_campaign(campaign, deliveries)
    audit(
        db,
        actor,
        "email.campaign_send",
        "email_campaign",
        campaign.id,
        {
            "summary_version_ids": json.loads(campaign.summary_version_ids),
            "outcome": campaign.status,
            "recipient_count": len(deliveries),
        },
    )
    db.commit()
    return campaign


def retry_delivery(db: Session, app_settings: Settings, mail_state: MailState, actor: User, campaign_id: int, delivery_id: int) -> EmailDelivery:
    if not can_send_email(actor):
        raise Forbidden()
    campaign = _campaign_or_404(db, campaign_id)
    delivery = db.get(EmailDelivery, delivery_id)
    if delivery is None or delivery.campaign_id != campaign.id:
        raise NotFound()
    if delivery.status != "failed":
        raise Conflict("Only a failed delivery can be retried.")
    email_settings = get_email_settings(db)
    _ensure_mail_enabled(email_settings)
    items = campaign_items(db, campaign)
    _deliver_one(db, app_settings, mail_state, email_settings, campaign, delivery, items)
    deliveries = list(db.scalars(select(EmailDelivery).where(EmailDelivery.campaign_id == campaign.id)).all())
    _finalize_campaign(campaign, deliveries)
    audit(
        db,
        actor,
        "email.delivery_retry",
        "email_delivery",
        delivery.id,
        {"campaign_id": campaign.id, "outcome": delivery.status},
    )
    db.commit()
    return delivery


def list_campaigns(db: Session, actor: User) -> list[EmailCampaign]:
    _require_campaign_reader(actor)
    return list(db.scalars(select(EmailCampaign).order_by(EmailCampaign.id.desc())).all())


def get_campaign(db: Session, actor: User, campaign_id: int) -> EmailCampaign:
    _require_campaign_reader(actor)
    return _campaign_or_404(db, campaign_id)


def list_deliveries(db: Session, actor: User, campaign_id: int) -> list[EmailDelivery]:
    get_campaign(db, actor, campaign_id)
    return list(
        db.scalars(select(EmailDelivery).where(EmailDelivery.campaign_id == campaign_id).order_by(EmailDelivery.id)).all()
    )


def campaign_dict(db: Session, campaign: EmailCampaign) -> dict:
    return {
        "id": campaign.id,
        "subject": campaign.subject,
        "email_type": campaign.email_type,
        "summary_version_ids": json.loads(campaign.summary_version_ids),
        "created_by": campaign.created_by,
        "sent_by": campaign.sent_by,
        "status": campaign.status,
        "status_label": label_for(campaign.status),
        "created_at": iso(campaign.created_at),
        "previewed_at": iso(campaign.previewed_at),
        "scheduled_at": iso(campaign.scheduled_at),
        "sent_at": iso(campaign.sent_at),
    }


def delivery_dict(delivery: EmailDelivery) -> dict:
    return {
        "id": delivery.id,
        "campaign_id": delivery.campaign_id,
        "subscriber_id": delivery.subscriber_id,
        "email": delivery.email,
        "status": delivery.status,
        "status_label": label_for(delivery.status),
        "provider_message_id": delivery.provider_message_id,
        "failure": delivery.failure,
        "attempts": delivery.attempts,
        "sent_at": iso(delivery.sent_at),
    }


def subscribe_address(
    db: Session,
    app_settings: Settings,
    mail_state: MailState,
    limiter: RateLimiter,
    ip: str,
    email_raw: str,
    consent: bool,
    source: str,
) -> None:
    limiter.hit(f"subscribe:{ip}", app_settings.public_limit, app_settings.public_window_seconds)
    if consent is not True:
        raise BadRequest("Consent is required to subscribe.")
    try:
        email = normalize_email(email_raw)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    now = utcnow()
    subscriber = db.scalar(select(Subscriber).where(Subscriber.email == email))
    if subscriber is not None and subscriber.status == "suppressed":
        return
    if subscriber is not None and subscriber.status == "subscribed":
        return
    if subscriber is None:
        subscriber = Subscriber(
            email=email,
            status="pending",
            consent_source=(source or "public_form")[:120],
            consent_at=now,
            confirmation_at=None,
            unsubscribe_at=None,
            suppression_reason=None,
            created_at=now,
        )
        db.add(subscriber)
        db.flush()
    else:
        subscriber.status = "pending"
        subscriber.consent_source = (source or "public_form")[:120]
        subscriber.consent_at = now
        subscriber.confirmation_at = None
    link = _issue_link(db, app_settings, subscriber, "confirm")
    text = f"Confirm your newsletter subscription. This link expires and can be used once.\n{link}\n"
    html = (
        "<p>Confirm your newsletter subscription. This link expires and can be used once.</p>"
        f"<p><a href=\"{escape(link)}\">{escape(link)}</a></p>"
    )
    _send_transactional(db, mail_state, subscriber.email, "Confirm your subscription", text, html, f"confirm-{subscriber.id}-{hash_token(link)[:12]}")
    db.commit()


def _subscription_token(db: Session, raw: str, purpose: str) -> SubscriptionToken:
    row = db.scalar(select(SubscriptionToken).where(SubscriptionToken.token_hash == hash_token(raw or "")))
    if row is None or row.purpose != purpose or row.used_at is not None or row.expires_at <= utcnow():
        raise BadRequest("This link is invalid or has expired.")
    return row


def confirm_subscription(db: Session, raw_token: str) -> None:
    row = _subscription_token(db, raw_token, "confirm")
    subscriber = db.get(Subscriber, row.subscriber_id)
    if subscriber is None:
        raise BadRequest("This link is invalid or has expired.")
    row.used_at = utcnow()
    if subscriber.status == "suppressed":
        db.commit()
        raise BadRequest("This address cannot be subscribed.")
    if subscriber.status == "unsubscribed":
        db.commit()
        raise BadRequest("This subscription was cancelled. Sign up again to receive newsletters.")
    subscriber.status = "subscribed"
    subscriber.confirmation_at = utcnow()
    subscriber.unsubscribe_at = None
    db.commit()


def unsubscribe_address(db: Session, raw_token: str) -> None:
    row = _subscription_token(db, raw_token, "unsubscribe")
    subscriber = db.get(Subscriber, row.subscriber_id)
    if subscriber is None:
        raise BadRequest("This link is invalid or has expired.")
    row.used_at = utcnow()
    subscriber.status = "unsubscribed"
    subscriber.unsubscribe_at = utcnow()
    db.commit()


def list_subscribers(db: Session, actor: User) -> list[Subscriber]:
    if not can_admin(actor):
        raise Forbidden()
    return list(db.scalars(select(Subscriber).order_by(Subscriber.email)).all())


def subscriber_dict(row: Subscriber) -> dict:
    return {
        "id": row.id,
        "email": row.email,
        "status": row.status,
        "status_label": label_for(row.status),
        "consent_source": row.consent_source,
        "consent_at": iso(row.consent_at),
        "confirmation_at": iso(row.confirmation_at),
        "unsubscribe_at": iso(row.unsubscribe_at),
        "suppression_reason": row.suppression_reason,
    }


def suppress_subscriber(db: Session, actor: User, subscriber_id: int, reason: str) -> Subscriber:
    if not can_admin(actor):
        raise Forbidden()
    subscriber = db.get(Subscriber, subscriber_id)
    if subscriber is None:
        raise NotFound()
    subscriber.status = "suppressed"
    subscriber.suppression_reason = (reason or "suppressed by administrator")[:300]
    audit(db, actor, "subscriber.suppress", "subscriber", subscriber.id, {"reason": subscriber.suppression_reason})
    db.commit()
    return subscriber


def update_email_settings(db: Session, actor: User, payload: dict) -> EmailSettings:
    if not can_admin(actor):
        raise Forbidden()
    row = get_email_settings(db)
    provider = row.provider
    if "provider" in payload:
        provider = payload.get("provider") or "local"
        if provider not in {"local", "smtp"}:
            raise BadRequest("Provider must be local or smtp.")
    sender_email = row.sender_email
    if "sender_email" in payload:
        sender_email = (payload.get("sender_email") or "").strip().lower()
        if sender_email:
            try:
                sender_email = normalize_email(sender_email)
            except ValueError as exc:
                raise BadRequest(str(exc)) from exc
    enabled = bool(row.enabled) if "enabled" not in payload else bool(payload.get("enabled"))
    smtp_host = row.smtp_host
    if "smtp_host" in payload:
        smtp_host = (payload.get("smtp_host") or "").strip()
    if enabled and not sender_email:
        raise BadRequest("Set a sender address before enabling email.")
    if enabled and provider == "smtp" and not smtp_host:
        raise BadRequest("An SMTP host is required for the SMTP provider.")
    if "public_base_url" in payload:
        base = (payload.get("public_base_url") or row.public_base_url).strip().rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise BadRequest("Public base URL must be an http or https URL.")
        row.public_base_url = base
    if "sender_name" in payload:
        row.sender_name = (payload.get("sender_name") or "")[:200]
    if "smtp_port" in payload:
        row.smtp_port = int(payload.get("smtp_port") or 587)
    if "smtp_username" in payload:
        row.smtp_username = payload.get("smtp_username") or ""
    if payload.get("smtp_password"):
        row.smtp_password = payload["smtp_password"]
    if "smtp_use_tls" in payload:
        row.smtp_use_tls = bool(payload.get("smtp_use_tls"))
    row.enabled = enabled
    row.provider = provider
    row.sender_email = sender_email
    row.smtp_host = smtp_host
    row.updated_by = actor.id
    row.updated_at = utcnow()
    audit(
        db,
        actor,
        "settings.email",
        "email_settings",
        row.id,
        {"enabled": bool(row.enabled), "provider": row.provider, "sender_email": row.sender_email},
    )
    db.commit()
    return row


def update_publication_settings(db: Session, actor: User, public_site_enabled: bool, organization_name: str) -> PublicationSettings:
    if not can_admin(actor):
        raise Forbidden()
    name = (organization_name or "").strip()
    if not name:
        raise BadRequest("An organization name is required.")
    row = get_publication_settings(db)
    row.public_site_enabled = bool(public_site_enabled)
    row.organization_name = name[:200]
    row.updated_by = actor.id
    row.updated_at = utcnow()
    audit(
        db,
        actor,
        "settings.publication",
        "publication_settings",
        row.id,
        {"public_site_enabled": bool(row.public_site_enabled)},
    )
    db.commit()
    return row


def list_audit(db: Session, actor: User) -> list[AuditLog]:
    if not can_admin(actor):
        raise Forbidden()
    return list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(200)).all())


def audit_dict(row: AuditLog) -> dict:
    return {
        "id": row.id,
        "actor_id": row.actor_id,
        "action": row.action,
        "entity_type": row.entity_type,
        "entity_id": row.entity_id,
        "detail": json.loads(row.detail or "{}"),
        "created_at": iso(row.created_at),
    }


def list_outbox(db: Session, actor: User) -> list[OutboxMessage]:
    if not can_admin(actor):
        raise Forbidden()
    return list(db.scalars(select(OutboxMessage).order_by(OutboxMessage.id.desc())).all())


def job_dict(job: IngestionJob) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "title": job.title,
        "document_id": job.document_id,
        "error": job.error,
        "created_at": iso(job.created_at),
        "finished_at": iso(job.finished_at),
    }
