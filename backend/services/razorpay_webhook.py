import hashlib
import hmac
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


_SIGNATURE_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class NormalizedRazorpayPayment:
    event_type: str
    external_order_id: str | None
    external_payment_id: str
    customer_id: int | None
    status: str
    amount: float
    occurred_at: datetime | None


@dataclass(frozen=True)
class NormalizedRazorpayEvent:
    event_type: str
    event_timestamp: datetime | None
    account_reference: str | None
    payment: NormalizedRazorpayPayment | None


def verify_razorpay_signature(
    raw_body: bytes,
    signature: str | None,
    secret: str | None,
) -> bool:
    if not secret or not signature or not _SIGNATURE_PATTERN.fullmatch(signature):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


def _unix_timestamp(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _customer_id(notes: Any) -> int | None:
    if not isinstance(notes, dict):
        return None
    value = notes.get("growthpilot_customer_id")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdecimal():
        result = int(value.strip())
    else:
        return None
    return result if result > 0 else None


def normalize_razorpay_event(payload: Any) -> NormalizedRazorpayEvent | None:
    if not isinstance(payload, dict):
        return None

    event_type = payload.get("event")
    if not isinstance(event_type, str) or not event_type.strip():
        return None
    event_type = event_type.strip()
    event_timestamp = _unix_timestamp(payload.get("created_at"))
    account_reference = payload.get("account_id")
    if not isinstance(account_reference, str) or not account_reference.strip():
        account_reference = None

    if event_type not in {"payment.captured", "payment.failed"}:
        return NormalizedRazorpayEvent(
            event_type=event_type,
            event_timestamp=event_timestamp,
            account_reference=account_reference,
            payment=None,
        )

    try:
        entity = payload["payload"]["payment"]["entity"]
        if not isinstance(entity, dict):
            return None
        payment_id = entity["id"]
        order_id = entity.get("order_id")
        amount_minor = entity["amount"]
        currency = entity["currency"]
        status = entity["status"]
    except (KeyError, TypeError):
        return None

    expected_status = "captured" if event_type == "payment.captured" else "failed"
    if (
        not isinstance(payment_id, str)
        or not payment_id.strip()
        or (order_id is not None and not isinstance(order_id, str))
        or isinstance(amount_minor, bool)
        or not isinstance(amount_minor, int)
        or amount_minor <= 0
        or currency != "INR"
        or status != expected_status
    ):
        return None

    occurred_at = event_timestamp or _unix_timestamp(entity.get("created_at"))
    return NormalizedRazorpayEvent(
        event_type=event_type,
        event_timestamp=event_timestamp,
        account_reference=account_reference,
        payment=NormalizedRazorpayPayment(
            event_type=event_type,
            external_order_id=order_id.strip() if order_id and order_id.strip() else None,
            external_payment_id=payment_id.strip(),
            customer_id=_customer_id(entity.get("notes")),
            status="succeeded" if expected_status == "captured" else "failed",
            amount=amount_minor / 100,
            occurred_at=occurred_at,
        ),
    )


def payload_sha256(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()
