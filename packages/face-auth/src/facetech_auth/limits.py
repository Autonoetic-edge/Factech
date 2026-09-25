"""Per-principal, per-source and admission limits for the hardened profile.

Three separate controls, each refusing rather than queueing without bound:

* ``Window`` — a sliding-window counter per key. Keys are the server's own
  identifiers (actor id, or a source address resolved from the transport peer by
  the trusted-proxy layer), never a caller-supplied header, so session churn,
  spoofed ``X-Forwarded-For`` and a peer tenant cannot reset or consume another
  principal's allowance.
* ``Admission`` — a bounded count of in-flight expensive work. Over the bound the
  request is refused with ``BUSY`` and a ``Retry-After``, not parked in an
  unbounded queue.
* ``Limits`` — the profile's fixed policy, injectable so tests can drive a clock.

Refusals carry ``Retry-After`` so a client backs off instead of retrying hot.
"""

import contextlib
import math
import time
from collections import OrderedDict, deque

from .contracts import Denied

BODY_LIMIT = 481_964  # bytes; the architecture's streaming cap, both services
# Whole-body deadline: a slow body must not hold a worker. Unmeasured: no upload
# from a real participant's network has been timed, so this is a conservative
# engineering default, not a measured number.
BODY_SECONDS = 15
BUSY_RETRY_AFTER = 2


class Window:
    """Sliding-window counter. ``keys`` bounds the limiter's own memory.

    ponytail: process-local counters. One gateway and one engine process per host
    today, so a shared store would be pure overhead; move to a shared counter
    (Postgres row or Redis) the day a second replica exists.
    """

    def __init__(self, limit, seconds, *, keys=4096, clock=time.monotonic):
        self.limit, self.seconds, self.keys, self.clock = limit, seconds, keys, clock
        self._hits: OrderedDict[str, deque] = OrderedDict()

    def retry_after(self, key):
        """Seconds to wait, or 0 when the call is allowed and counted."""
        now = self.clock()
        hits = self._hits.get(key)
        if hits is None:
            hits = self._hits[key] = deque()
        self._hits.move_to_end(key)
        while hits and hits[0] <= now - self.seconds:
            hits.popleft()
        if len(hits) >= self.limit:
            # A zero limit refuses everything; there is no earlier hit to expire.
            oldest = hits[0] if hits else now
            return max(1, math.ceil(oldest + self.seconds - now))
        hits.append(now)
        # Bounded key table: evict the least recently used key.
        while len(self._hits) > self.keys:
            self._hits.popitem(last=False)
        return 0

    def enforce(self, key):
        wait = self.retry_after(key)
        if wait:
            raise Denied("RATE_LIMITED", 429, retry_after=wait)


class Admission:
    """Bounded in-flight work. Single event loop, so a plain counter is enough."""

    def __init__(self, limit, *, retry_after=BUSY_RETRY_AFTER):
        self.limit, self.retry_after, self.active = limit, retry_after, 0

    @contextlib.asynccontextmanager
    async def hold(self):
        if self.active >= self.limit:
            raise Denied(
                "BUSY",
                503,
                "service is at capacity; retry shortly",
                retry_after=self.retry_after,
            )
        self.active += 1
        try:
            yield
        finally:
            self.active -= 1


class Limits:
    """The isolated profile's fixed policy.

    Measured on the owned loopback stack, 20 September 2026
    (`docs/hardening/evidence/m4-capacity.json`, `tools/hardening_m4_capacity.py`):

    * the gateway served 36.1 unauthenticated auth requests and 23.6
      authenticated requests per second, so every ``Window`` below sits two
      orders of magnitude under what the service can serve. They bound demand,
      not capacity;
    * one OIDC sign-in took 4.06 seconds and costs two auth-window calls;
    * the frozen engine is strictly serial — one worker lock — at 2.17-2.51 s
      per accepted 12-frame scan as the live engine recorded it, and 4.03-4.51 s
      on the dev laptop that ran this measurement.

    **Unmeasured:** honest demand. Nobody has counted what a real round of
    testers actually asks for per minute, so each ``Window`` below is sized off
    capture arithmetic and the sign-in cost, not off observed usage.
    """

    def __init__(self, *, clock=time.monotonic):
        self.source = Window(300, 60, clock=clock)  # any request, per source
        # Unauthenticated /auth/*. One sign-in costs two calls, and a whole team
        # can share one NAT address, so 60/min is 30 sign-ins a minute from one
        # address. 20/min was measured too tight: it refused the real-provider
        # test suite's own logins.
        self.auth_source = Window(60, 60, clock=clock)
        self.actor = Window(120, 60, clock=clock)  # any request, per principal
        # Inference-bearing, per principal. A capture takes about 16 seconds, so
        # an honest participant cannot reach 10; at the measured 2.5 s per scan
        # the whole engine ceiling is about 24 scans a minute, and Admission
        # below is what actually stops one principal taking all of it.
        self.scan = Window(10, 60, clock=clock)
        # Concurrent scans admitted by the gateway. The engine runs scans one at
        # a time, so the Nth admitted caller waits N x the per-scan time, and the
        # gateway's own engine call gives up after 10 seconds (http.py). At the
        # measured 2.51 s worst case that allows 3: 3 x 2.51 = 7.5 s, inside the
        # timeout, where the 5 this shipped with meant 12.5 s -- the gateway
        # abandoning a scan the engine was still running. Measured at 5 on this
        # laptop the last caller waited 19.8 s. Past 3, an immediate BUSY with a
        # Retry-After serves the caller better than a stall.
        self.scans = Admission(3)
