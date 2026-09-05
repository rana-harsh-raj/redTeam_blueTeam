"""wait_until: poll a predicate until it returns a truthy value or a timeout
elapses. Used for the async paths the VERIFIER_SPEC.md calls out explicit max
waits for (FTS webhook round trips, ledger async reversal retry, cron
dequeue). Never used to paper over a synchronous assertion (see V11's
deliberate 0s wait for the MerchantBalance-sync claim).
"""
import os
import time


class WaitTimeout(Exception):
    pass


def wait_until(predicate, timeout=10.0, interval=0.5, desc="condition"):
    # The arena is slower than the spec's production-derived windows (cron-driven FTS status checks,
    # single-replica workers); optional ARENA_WAIT_SCALE is explicit and defaults to 1.
    timeout = timeout * float(os.environ.get("ARENA_WAIT_SCALE", "1"))
    deadline = time.monotonic() + timeout
    last_exc = None
    last_result = None
    while time.monotonic() < deadline:
        try:
            last_result = predicate()
        except Exception as exc:  # noqa: BLE001 -- keep polling past transient errors (e.g. a row not yet committed)
            last_exc = exc
            last_result = None
        if last_result:
            return last_result
        time.sleep(interval)
    if last_exc:
        raise WaitTimeout(
            "timed out after %.1fs waiting for %s (last error: %r)" % (timeout, desc, last_exc)
        )
    raise WaitTimeout("timed out after %.1fs waiting for %s (last value: %r)" % (timeout, desc, last_result))
