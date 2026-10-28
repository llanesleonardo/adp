import threading
import time
from collections import defaultdict

from app.errors import RateLimited


class RateLimiter:
    def __init__(self) -> None:
        self._events: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            recent = [stamp for stamp in self._events[key] if now - stamp < window_seconds]
            if len(recent) >= limit:
                self._events[key] = recent
                raise RateLimited()
            recent.append(now)
            self._events[key] = recent
