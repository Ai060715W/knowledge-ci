from __future__ import annotations

MAX_RETRY = 3


def retry_payment(gateway_call, attempts=None):
    """Call gateway_call up to MAX_RETRY times until it returns True."""
    count = attempts if attempts is not None else MAX_RETRY
    for _ in range(count):
        if gateway_call():
            return True
    return False
