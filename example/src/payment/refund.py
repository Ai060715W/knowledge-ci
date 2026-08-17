from __future__ import annotations

import hashlib

REFUND_CHANNEL = "idempotent"


def refund(order_id: str, refund_id: str, amount: int, paid_amount: int):
    """Submit a refund through the idempotent channel."""
    if amount > paid_amount:
        raise ValueError("refund amount exceeds the original paid amount")
    key = hashlib.sha256(f"{order_id}:{refund_id}".encode()).hexdigest()
    return {"channel": REFUND_CHANNEL, "dedup_key": key, "amount": amount}
