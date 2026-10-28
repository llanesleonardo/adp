from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.domain import load_session
from app.errors import Forbidden, Unauthenticated
from app.security import SAFE_METHODS, tokens_match


def get_db(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


def session_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return request.cookies.get("adp_session")


async def submitted_csrf(request: Request) -> str:
    header = request.headers.get("x-csrf-token", "")
    if header:
        return header
    if request.method in SAFE_METHODS:
        return ""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return ""
    form = await request.form()
    return str(form.get("csrf") or "")


def attach_user(request: Request, db: Session):
    user, session_row = load_session(db, request.app.state.settings, session_token(request))
    request.state.user = user
    request.state.session = session_row
    return user, session_row


async def require_user(request: Request, db: Session = Depends(get_db)):
    user, session_row = attach_user(request, db)
    if user is None or session_row is None:
        raise Unauthenticated()
    if request.method not in SAFE_METHODS:
        supplied = await submitted_csrf(request)
        if not tokens_match(session_row.csrf_token, supplied):
            raise Forbidden("Invalid or missing CSRF token.")
    return user


async def optional_user(request: Request, db: Session = Depends(get_db)):
    user, _session_row = attach_user(request, db)
    return user
