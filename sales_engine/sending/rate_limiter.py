import os
import time
import threading
from typing import Tuple, Optional, List


class RateLimiter:
    """
    In-memory rate limiter with sliding window for outbound emails.
    Prevents flooding SMTP servers and enforces conservative pacing.
    """

    def __init__(
        self,
        max_per_minute: Optional[int] = None,
        max_per_request: Optional[int] = None,
    ):
        self.max_per_minute = max_per_minute or int(os.environ.get("RATE_LIMIT_MAX_PER_MINUTE", 5))
        self.max_per_request = max_per_request or int(os.environ.get("RATE_LIMIT_MAX_PER_REQUEST", 20))
        self._timestamps: List[float] = []
        self._lock = threading.Lock()

    def check_rate_limit(self, count: int = 1) -> Tuple[bool, Optional[str]]:
        if count > self.max_per_request:
            return False, f"Batch size {count} exceeds max allowed per request ({self.max_per_request})"

        now = time.time()
        with self._lock:
            # Purge timestamps older than 60 seconds
            self._timestamps = [ts for ts in self._timestamps if now - ts < 60.0]

            if len(self._timestamps) + count > self.max_per_minute:
                return False, f"Rate limit exceeded: max {self.max_per_minute} sends per minute"

            return True, None

    def record_send(self, count: int = 1):
        now = time.time()
        with self._lock:
            for _ in range(count):
                self._timestamps.append(now)

    def reset(self):
        with self._lock:
            self._timestamps.clear()
