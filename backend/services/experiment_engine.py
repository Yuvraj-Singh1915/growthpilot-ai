import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Sequence

from services.opportunity_engine import (
    CommerceCartEvent,
    CommerceOrder,
    CommercePayment,
    CUSTOMER_INACTIVE_DAYS,
    HIGH_VALUE_CUSTOMER_REVENUE,
    REPEAT_PURCHASE_INACTIVE_DAYS,
    TARGET_AVERAGE_ORDER_VALUE,
    commerce_order_amount,
    commerce_payment_amount,
    is_supported_commerce_order,
    is_supported_commerce_payment,
)


def canonical_strategy_code(
    opportunity_title: str,
    action: str,
    explicit_code: str | None = None,
) -> str:
    if explicit_code and explicit_code.strip():
        return re.sub(r"[^a-z0-9_.-]+", "_", explicit_code.strip().lower()).strip("_")

    title = re.sub(r"[^a-z0-9]+", " ", opportunity_title.lower()).strip()
    text = re.sub(r"[^a-z0-9]+", " ", action.lower()).strip()
    words = set(text.split())

    if "average order value" in title:
        if words & {"bundle", "bundles", "recommendation", "recommendations", "crosssell"}:
            return "aov.product_bundle"
        if words & {"offer", "offers", "discount", "coupon", "incentive", "targeted"}:
            return "aov.targeted_offer"
        if "upsell" in words:
            return "aov.upsell"
    elif "abandoned cart" in title:
        if words & {"reminder", "email", "message", "notification"}:
            return "cart.recovery_reminder"
        if words & {"offer", "discount", "coupon", "incentive", "personalized", "targeted"}:
            return "cart.recovery_offer"
    elif "repeat purchase" in title:
        if words & {"reminder", "reorder", "repurchase"} or {"re", "order"} <= words:
            return "repeat.reorder_reminder"
        if words & {"offer", "discount", "coupon", "incentive"}:
            return "repeat.targeted_offer"
    elif "failed payment" in title:
        if "retry" in words or "retrying" in words:
            return "payment.retry"
        if words & {"reminder", "notification", "message"}:
            return "payment.reminder"
    elif "high value" in title:
        if words & {"winback", "reactivate", "reactivation"} or {"win", "back"} <= words:
            return "customer.winback"
        if words & {"offer", "discount", "coupon", "incentive"}:
            return "customer.targeted_offer"

    return f"custom:{text}"


def build_experiment_fingerprint(
    opportunity_id: int,
    opportunity_title: str,
    strategy_code: str,
    risk: str,
    budget: float,
    max_discount: float,
    suggested_discount: float,
    min_margin: float,
    target_growth: float,
    target_segment: str | None = None,
    control_percentage: float = 90,
    treatment_percentage: float = 10,
) -> str:
    identity = {
        "version": 1,
        "opportunity_id": opportunity_id,
        "opportunity": opportunity_title.strip().lower(),
        "strategy_code": strategy_code,
        "risk": risk.strip().upper(),
        "budget": round(budget, 4),
        "max_discount": round(max_discount, 4),
        "suggested_discount": round(suggested_discount, 4),
        "min_margin": round(min_margin, 4),
        "target_growth": round(target_growth, 4),
        "target_segment": (target_segment or "").strip().lower(),
        "control_percentage": round(control_percentage, 4),
        "treatment_percentage": round(treatment_percentage, 4),
    }
    serialized = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_hypothesis(opportunity_title: str, suggested_discount: float) -> str:
    incentive = (
        f"using an incentive of up to {suggested_discount:g}%"
        if suggested_discount > 0
        else "using relevant product recommendations without a discount"
    )
    if "abandoned cart" in opportunity_title.lower():
        return (
            "Offering a personalized recovery incentive to eligible abandoned-cart "
            "customers will increase recovered revenue without violating the "
            "merchant's discount and margin constraints."
        )
    if "repeat purchase" in opportunity_title.lower():
        return (
            "A targeted reorder campaign for eligible repeat-purchase customers "
            "will increase repeat purchase rate while maintaining the merchant's "
            "minimum margin."
        )
    if "average order value" in opportunity_title.lower():
        return (
            "Relevant product bundles will increase average order value without "
            "requiring an excessive discount."
        )
    if "failed payment" in opportunity_title.lower():
        return (
            "A targeted payment-retry reminder for customers with failed payments "
            "will recover payment value while respecting merchant constraints."
        )
    if "high-value" in opportunity_title.lower():
        return (
            "A tailored win-back campaign for inactive high-value customers will "
            "generate repeat purchases while preserving the merchant's minimum margin."
        )
    return (
        f"Applying {incentive} to eligible customers will improve the measured "
        "outcome while remaining within the merchant's configured constraints."
    )


def get_eligible_customers(
    opportunity_title: str,
    customer_ids: Sequence[int],
    orders: Sequence[CommerceOrder],
    cart_events: Sequence[CommerceCartEvent],
    payments: Sequence[CommercePayment],
    as_of: datetime | None = None,
) -> list[int]:
    now = as_of or datetime.utcnow()
    title = opportunity_title.lower()
    completed_orders = [
        order
        for order in orders
        if order.status.lower() == "completed" and is_supported_commerce_order(order)
    ]
    orders_by_customer: dict[int, list[CommerceOrder]] = {}
    for order in completed_orders:
        orders_by_customer.setdefault(order.customer_id, []).append(order)

    if "abandoned cart" in title:
        return sorted({
            cart.customer_id
            for cart in cart_events
            if cart.event_type.lower()
            in {"checkout_started", "cart_created", "abandoned"}
            and not any(
                order.created_at > cart.created_at
                for order in orders_by_customer.get(cart.customer_id, [])
            )
        })

    if "repeat purchase" in title:
        cutoff = now - timedelta(days=REPEAT_PURCHASE_INACTIVE_DAYS)
        return sorted(
            customer_id
            for customer_id, customer_orders in orders_by_customer.items()
            if max(order.created_at for order in customer_orders) < cutoff
        )

    if "average order value" in title:
        return sorted(
            customer_id
            for customer_id, customer_orders in orders_by_customer.items()
            if (
                sum(commerce_order_amount(order) for order in customer_orders)
                / len(customer_orders)
            ) < TARGET_AVERAGE_ORDER_VALUE
        )

    if "failed payment" in title:
        orders_by_id = {
            order.order_id: order
            for order in orders
            if order.order_id is not None
        }
        return sorted({
            payment.customer_id
            for payment in payments
            if payment.status.lower() in {"failed", "declined"}
            and is_supported_commerce_payment(payment)
            and commerce_payment_amount(payment) > 0
            and payment.customer_id in customer_ids
            and payment.order_id in orders_by_id
            and orders_by_id[payment.order_id].customer_id == payment.customer_id
            and (
                payment.provider != "razorpay"
                or (
                    orders_by_id[payment.order_id].provider == "razorpay"
                    and orders_by_id[payment.order_id].provider_account
                    == payment.provider_account
                    and orders_by_id[payment.order_id].currency == payment.currency
                )
            )
        })

    if "high-value" in title:
        cutoff = now - timedelta(days=CUSTOMER_INACTIVE_DAYS)
        eligible = []
        for customer_id in customer_ids:
            customer_orders = orders_by_customer.get(customer_id, [])
            if not customer_orders:
                continue
            lifetime_value = sum(
                commerce_order_amount(order) for order in customer_orders
            )
            last_purchase = max(order.created_at for order in customer_orders)
            if (
                lifetime_value >= HIGH_VALUE_CUSTOMER_REVENUE
                and last_purchase < cutoff
            ):
                eligible.append(customer_id)
        return sorted(eligible)

    return []


def assign_variants(
    experiment_public_id: str,
    customer_ids: Sequence[int],
) -> list[tuple[int, str]]:
    ordered_customers = sorted(
        set(customer_ids),
        key=lambda customer_id: hashlib.sha256(
            f"{experiment_public_id}:{customer_id}".encode("utf-8")
        ).digest(),
    )
    customer_count = len(ordered_customers)
    control_count = round(customer_count * 0.9)

    assignments = []
    for index, customer_id in enumerate(ordered_customers):
        variant = "CONTROL" if index < control_count else "TREATMENT"
        assignments.append((customer_id, variant))
    return assignments


def variant_definition(opportunity_title: str, action: str, discount: float) -> tuple[str, str]:
    title = opportunity_title.lower()
    if "abandoned cart" in title:
        control = "Standard cart experience with no recovery incentive"
        treatment = action
    elif "repeat purchase" in title:
        control = "Standard reorder experience"
        treatment = action
    elif "average order value" in title:
        control = "Standard product listing"
        treatment = action
    elif "failed payment" in title:
        control = "Standard failed-payment experience"
        treatment = action
    elif "high-value" in title:
        control = "Standard customer experience"
        treatment = action
    else:
        control = "Existing customer experience"
        treatment = action
    if discount > 0:
        treatment = f"{treatment} (up to {discount:g}% discount)"
    return control, treatment


def encode_variant(variant: str) -> str:
    return json.dumps({"name": variant}, separators=(",", ":"))
