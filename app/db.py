from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, EmailSettings, PublicationSettings
from app.security import utcnow


def make_engine(database_url: str):
    connect_args = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if database_url not in {"sqlite://", "sqlite:///:memory:"}:
            raw = database_url.removeprefix("sqlite:///")
            if raw and raw != ":memory:":
                Path(raw).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, connect_args=connect_args, future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        if engine.dialect.name == "sqlite":
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, class_=Session)


def init_database(engine) -> None:
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    with factory() as db:
        now = utcnow()
        if db.get(EmailSettings, 1) is None:
            db.add(
                EmailSettings(
                    id=1,
                    enabled=False,
                    provider="local",
                    sender_email="",
                    sender_name="",
                    public_base_url="http://127.0.0.1:8000",
                    smtp_host="",
                    smtp_port=587,
                    smtp_username="",
                    smtp_password="",
                    smtp_use_tls=True,
                    updated_at=now,
                )
            )
        if db.get(PublicationSettings, 1) is None:
            db.add(
                PublicationSettings(
                    id=1,
                    public_site_enabled=True,
                    organization_name="Knowledge Library",
                    updated_at=now,
                )
            )
        db.commit()
        columns = {row[1] for row in db.execute(text("PRAGMA table_info(documents)")).all()}
        if "category" not in columns:
            db.execute(
                text(
                    "ALTER TABLE documents ADD COLUMN category VARCHAR(64) "
                    "NOT NULL DEFAULT 'architectural-patterns'"
                )
            )
            db.commit()
        from app.domain import (
            ensure_design_concerns,
            ensure_learning_sections,
            ensure_diagram_exercises,
            ensure_practice_exercises,
            ensure_system_problems,
            import_shared_patterns,
        )

        import_shared_patterns(db)
        ensure_learning_sections(db)
        problem_columns = {row[1] for row in db.execute(text("PRAGMA table_info(system_problems)")).all()}
        if problem_columns and "body" not in problem_columns:
            db.execute(text("ALTER TABLE system_problems ADD COLUMN body TEXT NOT NULL DEFAULT ''"))
            db.commit()
        ensure_system_problems(db)
        ensure_design_concerns(db)
        exercise_columns = {row[1] for row in db.execute(text("PRAGMA table_info(exercises)")).all()}
        if exercise_columns:
            if "kind" not in exercise_columns:
                db.execute(text("ALTER TABLE exercises ADD COLUMN kind VARCHAR(32) NOT NULL DEFAULT 'prompt'"))
            if "spec" not in exercise_columns:
                db.execute(text("ALTER TABLE exercises ADD COLUMN spec TEXT NOT NULL DEFAULT '{}'"))
            if "repository_path" not in exercise_columns:
                db.execute(text("ALTER TABLE exercises ADD COLUMN repository_path VARCHAR(500)"))
            db.commit()
        attempt_columns = {row[1] for row in db.execute(text("PRAGMA table_info(exercise_attempts)")).all()}
        if attempt_columns and "result" not in attempt_columns:
            db.execute(text("ALTER TABLE exercise_attempts ADD COLUMN result TEXT NOT NULL DEFAULT '{}'"))
            db.commit()
        ensure_practice_exercises(db)
        ensure_diagram_exercises(db)
