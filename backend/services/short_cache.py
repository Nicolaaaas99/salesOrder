"""Tiny in-process TTL cache for read endpoints that many reps hit with the
SAME question (e.g. "which customers have an order today?").

Why: N reps × every window-focus × a 1-2 s InvNum query adds up fast. With a
short shared cache it's at most one query per TTL no matter how many reps are
online. Writes that change the answer call `invalidate(namespace)` so the rep
who just saved an order still sees their badge update immediately.

Process-local on purpose — the app runs as one backend process under IIS.
"""
import threading
import time
from typing import Any, Callable, Dict, Hashable, Tuple

_lock = threading.Lock()
_store: Dict[Tuple[str, Hashable], Tuple[float, Any]] = {}


def get_or_compute(namespace: str, key: Hashable, ttl_seconds: float, compute: Callable[[], Any]) -> Any:
    now = time.monotonic()
    with _lock:
        hit = _store.get((namespace, key))
        if hit and hit[0] > now:
            return hit[1]
    value = compute()
    with _lock:
        _store[(namespace, key)] = (now + ttl_seconds, value)
    return value


def invalidate(namespace: str) -> None:
    with _lock:
        for k in [k for k in _store if k[0] == namespace]:
            del _store[k]
