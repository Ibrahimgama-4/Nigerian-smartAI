import time
from collections import defaultdict, deque

class RateLimiter:
    """Sliding-window limiter (in-memory; use Redis/Supabase edge for multi-instance)."""
    def __init__(self, max_calls: int, per_seconds: float, clock=time.monotonic):
        self.max_calls, self.per, self.clock = max_calls, per_seconds, clock
        self._hits = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = self.clock()
        q = self._hits[key]
        while q and now - q[0] >= self.per:
            q.popleft()
        if len(q) >= self.max_calls:
            return False
        q.append(now)
        return True
