from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from markupsafe import escape

from app.api import router as api_router
from app.config import Settings
from app.db import init_database, make_engine, make_session_factory
from app.errors import AppError
from app.limiter import RateLimiter
from app.mailer import MailState
from app.redact import install_redaction
from app.web import router as web_router

PACKAGE_DIR = Path(__file__).resolve().parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    install_redaction()
    engine = make_engine(settings.database_url)
    init_database(engine)
    app = FastAPI(title="Knowledge Library", version="1.0.0")
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.mail_state = MailState()
    app.state.limiter = RateLimiter()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' https: data:; style-src 'self'; style-src-attr 'unsafe-inline'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        if request.url.path.startswith("/api"):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if exc.status_code == 401:
            return RedirectResponse("/login", status_code=303)
        body = (
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Request blocked</title><link rel=\"stylesheet\" href=\"/static/app.css\"></head>"
            f"<body><main class=\"narrow\"><h1>{exc.status_code}</h1><p>{escape(exc.detail)}</p>"
            "<p><a href=\"/\">Back to the library</a></p></main></body></html>"
        )
        return HTMLResponse(body, status_code=exc.status_code)

    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    app.include_router(api_router)
    app.include_router(web_router)
    return app
