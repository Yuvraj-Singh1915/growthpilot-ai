from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Sequence


ABANDONED_CART_RECOVERY_RATE = 0.35
REPEAT_PURCHASE_RECOVERY_RATE = 0.20
FAILED_PAYMENT_RECOVERY_RATE = 0.60
REACTIVATION_RECOVERY_RATE = 0.15
TARGET_AVERAGE_ORDER_VALUE = 4000.0
REPEAT_PURCHASE_INACTIVE_DAYS = 60
CUSTOMER_INACTIVE_DAYS = 90
HIGH_VALUE_CUSTOMER_REVENUE = 10000.0


@dataclass(frozen=True)
class CommerceOrder:
    customer_id: int
    amount: float
    status: str
    created_at: datetime
    order_id: int | None = None
    provider: str | None = None
    currency: str | None = None
    amount_minor: int | None = None
    provider_account: str | None = None
    provider_order_id: str | None = None


@dataclass(frozen=True)
class CommerceCartEvent:
    customer_id: int
    cart_value: float
    event_type: str
    created_at: datetime


@dataclass(frozen=True)
class CommercePayment:
    order_id: int
    customer_id: int
    amount: float
    status: str
    provider: str | None = None
    provider_account: str | None = None
    external_payment_id: str | None = None
    currency: str | None = None
    amount_minor: int | None = None


def is_supported_commerce_order(order: CommerceOrder) -> bool:
    if order.provider == "razorpay":
        return (
            order.currency == "INR"
            and isinstance(order.amount_minor, int)
            and order.amount_minor > 0
        )
    return order.currency in (None, "INR")


def commerce_order_amount(order: CommerceOrder) -> float:
    if order.currency == "INR" and order.amount_minor is not None:
        return order.amount_minor / 100
    return order.amount


def is_supported_commerce_payment(payment: CommercePayment) -> bool:
    if payment.provider == "razorpay":
        return (
            payment.currency == "INR"
            and isinstance(payment.amount_minor, int)
            and payment.amount_minor > 0
        )
    return payment.currency in (None, "INR")


def commerce_payment_amount(payment: CommercePayment) -> float:
    if payment.currency == "INR" and payment.amount_minor is not None:
        return payment.amount_minor / 100
    return payment.amount


def razorpay_order_paid_at(order: Any, payments: Sequence[Any]) -> Any | None:
    amount_minor = getattr(order, "amount_minor", None)
    if (
        getattr(order, "provider", None) != "razorpay"
        or getattr(order, "currency", None) != "INR"
        or not isinstance(amount_minor, int)
        or amount_minor <= 0
    ):
        return None

    eligible = []
    seen_payment_ids = set()
    for payment in payments:
        payment_id = getattr(payment, "external_payment_id", None)
        if (
            getattr(payment, "provider", None) != "razorpay"
            or getattr(payment, "provider_account", None)
            != getattr(order, "provider_account", None)
            or getattr(payment, "currency", None) != "INR"
            or payment.status.lower()
            not in {"succeeded", "successful", "paid", "captured"}
            or not isinstance(getattr(payment, "amount_minor", None), int)
            or payment.amount_minor <= 0
            or getattr(payment, "provider_event_at", None) is None
            or not payment_id
        ):
            continue
        key = (
            getattr(payment, "provider", None),
            getattr(payment, "provider_account", None),
            payment_id,
        )
        if key in seen_payment_ids:
            continue
        seen_payment_ids.add(key)
        eligible.append(payment)

    eligible.sort(key=lambda item: item.provider_event_at)
    paid_amount_minor = 0
    for payment in eligible:
        paid_amount_minor += payment.amount_minor
        if paid_amount_minor >= amount_minor:
            return payment.provider_event_at
    return None


@dataclass(frozen=True)
class OpportunityCandidate:
    title: str
    potential_revenue: float
    recommended_action: str
    suggested_discount: float
    affected_customers: int
    affected_orders: int
    evidence: str


def _risk_level(affected_entities: int) -> str:
    if affected_entities >= 20:
        return "LOW"
    if affected_entities >= 8:
        return "MEDIUM"
    return "HIGH"


def _score_opportunity(
    potential_revenue: float,
    affected_customers: int,
    affected_orders: int,
    risk: str,
) -> float:
    # Revenue, reach, and sample size are independently capped so one large
    # opportunity cannot dominate the ranking without supporting evidence.
    revenue_score = min(max(potential_revenue, 0) / 50000, 1) * 50
    customer_impact_score = min(affected_customers / 30, 1) * 25
    sample_confidence_score = min(affected_orders / 30, 1) * 25
    risk_penalty = {"LOW": 0, "MEDIUM": 10, "HIGH": 20}[risk]
    return round(max(0, revenue_score + customer_impact_score + sample_confidence_score - risk_penalty), 2)


def detect_opportunities(
    customer_ids: Sequence[int],
    orders: Sequence[CommerceOrder],
    cart_events: Sequence[CommerceCartEvent],
    payments: Sequence[CommercePayment],
    max_discount: float,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    now = as_of or datetime.utcnow()
    completed_orders = [
        order
        for order in orders
        if order.status.lower() == "completed" and is_supported_commerce_order(order)
    ]
    orders_by_customer: dict[int, list[CommerceOrder]] = {}
    for order in completed_orders:
        orders_by_customer.setdefault(order.customer_id, []).append(order)

    order_by_id_customer = {
        order.order_id: order.customer_id
        for order in orders
        if order.order_id is not None and order.customer_id in customer_ids
    }
    candidates: list[OpportunityCandidate] = []
    discount_limit = max(0, max_discount)

    abandoned_carts = [
        cart
        for cart in cart_events
        if cart.event_type.lower() in {
            "checkout_started",
            "cart_created",
            "abandoned",
        }
        and not any(
            order.created_at > cart.created_at
            for order in orders_by_customer.get(cart.customer_id, [])
        )
    ]
    if abandoned_carts:
        cart_value = sum(cart.cart_value for cart in abandoned_carts)
        cart_customers = {cart.customer_id for cart in abandoned_carts}
        candidates.append(
            OpportunityCandidate(
                title="Recover Abandoned Carts",
                potential_revenue=cart_value * ABANDONED_CART_RECOVERY_RATE,
                recommended_action="Send personalized recovery offer",
                suggested_discount=min(discount_limit, 10),
                affected_customers=len(cart_customers),
                affected_orders=len(abandoned_carts),
                evidence=(
                    f"{len(abandoned_carts)} checkout carts were not followed by "
                    f"a completed order; their combined value is ₹{cart_value:,.0f}."
                ),
            )
        )

    repeat_cutoff = now - timedelta(days=REPEAT_PURCHASE_INACTIVE_DAYS)
    repeat_customers = {
        customer_id: customer_orders
        for customer_id, customer_orders in orders_by_customer.items()
        if max(order.created_at for order in customer_orders) < repeat_cutoff
    }
    if repeat_customers:
        previous_revenue = sum(
            commerce_order_amount(order)
            for customer_orders in repeat_customers.values()
            for order in customer_orders
        )
        eligible_order_count = sum(
            len(customer_orders) for customer_orders in repeat_customers.values()
        )
        repeat_value = sum(
            max(commerce_order_amount(order) for order in customer_orders)
            for customer_orders in repeat_customers.values()
        )
        candidates.append(
            OpportunityCandidate(
                title="Increase Repeat Purchases",
                potential_revenue=repeat_value * REPEAT_PURCHASE_RECOVERY_RATE,
                recommended_action="Launch personalized re-order campaign",
                suggested_discount=min(discount_limit, 5),
                affected_customers=len(repeat_customers),
                affected_orders=eligible_order_count,
                evidence=(
                    f"{len(repeat_customers)} past customers have not purchased "
                    f"in {REPEAT_PURCHASE_INACTIVE_DAYS}+ days; their prior "
                    f"completed revenue was ₹{previous_revenue:,.0f}."
                ),
            )
        )

    if completed_orders:
        current_aov = (
            sum(commerce_order_amount(order) for order in completed_orders)
            / len(completed_orders)
        )
        if current_aov < TARGET_AVERAGE_ORDER_VALUE:
            aov_customers = {order.customer_id for order in completed_orders}
            candidates.append(
                OpportunityCandidate(
                    title="Increase Average Order Value",
                    potential_revenue=(
                        TARGET_AVERAGE_ORDER_VALUE - current_aov
                    ) * len(completed_orders),
                    recommended_action="Recommend relevant product bundles",
                    suggested_discount=0,
                    affected_customers=len(aov_customers),
                    affected_orders=len(completed_orders),
                    evidence=(
                        f"Completed-order AOV is ₹{current_aov:,.0f}, below the "
                        f"₹{TARGET_AVERAGE_ORDER_VALUE:,.0f} demo benchmark "
                        f"across {len(completed_orders)} orders."
                    ),
                )
            )

    orders_by_id = {
        order.order_id: order
        for order in orders
        if order.order_id is not None
    }
    seen_payment_ids: set[tuple[str | None, str | None, str]] = set()
    failed_payments = []
    for payment in payments:
        mapped_order = orders_by_id.get(payment.order_id)
        if (
            payment.status.lower() not in {"failed", "declined"}
            or not is_supported_commerce_payment(payment)
            or mapped_order is None
            or mapped_order.customer_id != payment.customer_id
            or payment.customer_id not in customer_ids
            or (
                payment.provider == "razorpay"
                and (
                    mapped_order.provider != "razorpay"
                    or mapped_order.provider_account != payment.provider_account
                    or mapped_order.currency != payment.currency
                )
            )
            or commerce_payment_amount(payment) <= 0
        ):
            continue
        if payment.external_payment_id:
            payment_key = (
                payment.provider,
                payment.provider_account,
                payment.external_payment_id,
            )
            if payment_key in seen_payment_ids:
                continue
            seen_payment_ids.add(payment_key)
        failed_payments.append(payment)
    if failed_payments:
        failed_value = sum(
            commerce_payment_amount(payment) for payment in failed_payments
        )
        failed_customers = {
            order_by_id_customer[payment.order_id]
            for payment in failed_payments
            if payment.order_id in order_by_id_customer
        }
        candidates.append(
            OpportunityCandidate(
                title="Recover Failed Payments",
                potential_revenue=failed_value * FAILED_PAYMENT_RECOVERY_RATE,
                recommended_action="Retry failed payments with a secure checkout reminder",
                suggested_discount=min(discount_limit, 3),
                affected_customers=len(failed_customers),
                affected_orders=len(failed_payments),
                evidence=(
                    f"{len(failed_payments)} payment attempts failed, representing "
                    f"₹{failed_value:,.0f} in payment value."
                ),
            )
        )

    high_value_inactive: dict[int, list[CommerceOrder]] = {}
    inactive_cutoff = now - timedelta(days=CUSTOMER_INACTIVE_DAYS)
    for customer_id in customer_ids:
        customer_orders = orders_by_customer.get(customer_id, [])
        lifetime_value = sum(
            commerce_order_amount(order) for order in customer_orders
        )
        last_purchase = max(
            (order.created_at for order in customer_orders),
            default=None,
        )
        if (
            lifetime_value >= HIGH_VALUE_CUSTOMER_REVENUE
            and last_purchase is not None
            and last_purchase < inactive_cutoff
        ):
            high_value_inactive[customer_id] = customer_orders

    if high_value_inactive:
        lifetime_revenue = sum(
            commerce_order_amount(order)
            for customer_orders in high_value_inactive.values()
            for order in customer_orders
        )
        inactive_order_count = sum(
            len(customer_orders) for customer_orders in high_value_inactive.values()
        )
        candidates.append(
            OpportunityCandidate(
                title="Reactivate High-Value Customers",
                potential_revenue=lifetime_revenue * REACTIVATION_RECOVERY_RATE,
                recommended_action="Launch a tailored win-back campaign",
                suggested_discount=min(discount_limit, 5),
                affected_customers=len(high_value_inactive),
                affected_orders=inactive_order_count,
                evidence=(
                    f"{len(high_value_inactive)} inactive customers each have "
                    f"₹{HIGH_VALUE_CUSTOMER_REVENUE:,.0f}+ in historical completed "
                    f"orders; combined lifetime value is ₹{lifetime_revenue:,.0f}."
                ),
            )
        )

    scored: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.potential_revenue <= 0:
            continue

        affected_entities = max(
            candidate.affected_customers,
            candidate.affected_orders,
        )
        risk = _risk_level(affected_entities)
        score = _score_opportunity(
            candidate.potential_revenue,
            candidate.affected_customers,
            candidate.affected_orders,
            risk,
        )
        priority = "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW"
        scored.append(
            {
                "title": candidate.title,
                "priority": priority,
                "score": score,
                "potential_revenue": round(candidate.potential_revenue, 2),
                "affected_customers": candidate.affected_customers,
                "affected_orders": candidate.affected_orders,
                "recommended_action": candidate.recommended_action,
                "suggested_discount": candidate.suggested_discount,
                "risk": risk,
                "evidence": candidate.evidence,
            }
        )

    return sorted(scored, key=lambda item: (-item["score"], item["title"]))
