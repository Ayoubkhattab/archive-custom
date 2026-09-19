import time

from django.conf import settings
from django.core.cache import cache

_PERIOD_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _parse_rate(rate: str) -> tuple[int, int]:
    """Parse DRF-style rates such as "30/min" into (requests, seconds)."""
    count, _, period = rate.partition("/")
    return int(count), _PERIOD_SECONDS[period.strip()[:1].lower() or "m"]


def allow_request(user_id: int) -> bool:
    """
    Fixed-window limit on LLM calls per user. The model runs on the server's
    own CPU, so without a limit a single account can starve everyone else.
    """
    limit, seconds = _parse_rate(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["ai"])
    key = f"ai_rate:{user_id}:{int(time.time() // seconds)}"
    cache.add(key, 0, timeout=seconds)
    try:
        return cache.incr(key) <= limit
    except ValueError:  # key expired between add() and incr()
        return True
