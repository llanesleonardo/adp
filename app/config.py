import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    environment: str
    cookie_secure: bool
    session_idle_minutes: int = 120
    session_absolute_hours: int = 12
    reset_token_minutes: int = 30
    confirm_token_hours: int = 48
    unsubscribe_token_days: int = 180
    login_limit: int = 8
    login_window_seconds: int = 900
    reset_limit: int = 5
    reset_window_seconds: int = 3600
    public_limit: int = 5
    public_window_seconds: int = 3600

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.environ.get("ADP_DATABASE_URL", "sqlite:///./data/adp.db"),
            environment=os.environ.get("ADP_ENV", "development"),
            cookie_secure=os.environ.get("ADP_COOKIE_SECURE", "0") == "1",
        )
