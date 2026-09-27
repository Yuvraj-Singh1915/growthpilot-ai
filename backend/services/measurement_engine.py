from datetime import datetime
from typing import Any, Sequence

from services.opportunity_engine import razorpay_order_paid_at


MIN_CUSTOMERS_PER_VARIANT = 10
MIN_OBSERVATION_DAYS = 7


def _group_metrics(
    customer_ids: set[int],
    orders_by_customer: dict[int, list[Any]],
    revenue_by_order: dict[int, float],
) -> dict[str, Any]:
    observed_orders = [
        order
        for customer_id in customer_ids
        for order in orders_by_customer.get(customer_id, [])
    ]
    converted_customers = sum(
        bool(orders_by_customer.get(customer_id)) for customer_id in customer_ids
    )
    revenue = sum(
        revenue_by_order.get(order.id, order.amount)
        for order in observed_orders
    )
    margins = [
        order.margin_percent
        for order in observed_orders
        if order.margin_percent is not None
    ]
    return {
        "customers": len(customer_ids),
        "conversions": converted_customers,
        "orders": len(observed_orders),
        "revenue": round(revenue, 2),
        "conversion_rate": (
            round(converted_customers / len(customer_ids) * 100, 2)
            if customer_ids
            else None
        ),
        "average_revenue_per_customer": (
            round(revenue / len(customer_ids), 2) if customer_ids else None
        ),
        "average_order_value": (
            round(revenue / len(observed_orders), 2)
            if observed_orders
            else None
        ),
        "margin_percent": (
            round(sum(margins) / len(margins), 2) if margins else None
        ),
    }


def calculate_experiment_metrics(
    experiment: Any,
    assignments: Sequence[Any],
    orders: Sequence[Any],
    payments: Sequence[Any] = (),
    measured_at: datetime | None = None,
) -> dict[str, Any]:
    """Calculate observed outcomes from orders after experiment enrollment begins."""
    now = measured_at or datetime.utcnow()
    observation_days = max(
        0,
        (now - experiment.started_at).total_seconds() / 86400,
    )
    control_customers = {
        item.customer_id for item in assignments if item.variant == "CONTROL"
    }
    treatment_customers = {
        item.customer_id for item in assignments if item.variant == "TREATMENT"
    }
    assigned_customers = control_customers | treatment_customers

    payments_by_order: dict[int, list[Any]] = {}
    for payment in payments:
        payments_by_order.setdefault(payment.order_id, []).append(payment)

    orders_by_customer: dict[int, list[Any]] = {}
    revenue_by_order: dict[int, float] = {}
    for order in orders:
        order_payments = payments_by_order.get(order.id, [])
        currency = getattr(order, "currency", None)
        provider = getattr(order, "provider", None)
        amount_minor = getattr(order, "amount_minor", None)
        payment_event_at = None
        if provider == "razorpay":
            if currency != "INR" or not isinstance(amount_minor, int) or amount_minor <= 0:
                continue
            payment_event_at = razorpay_order_paid_at(order, order_payments)
            if payment_event_at is None:
                continue
            order_revenue = amount_minor / 100
        else:
            if currency not in (None, "INR"):
                continue
            payment_is_valid = not order_payments or any(
                payment.status.lower() in {"succeeded", "successful", "paid", "captured"}
                and getattr(payment, "currency", None) in (None, "INR")
                for payment in order_payments
            )
            if not payment_is_valid:
                continue
            order_revenue = (
                amount_minor / 100
                if currency == "INR" and isinstance(amount_minor, int)
                else order.amount
            )
        observed_at = payment_event_at or order.created_at
        if (
            order.customer_id in assigned_customers
            and order.status.lower() == "completed"
            and experiment.started_at <= observed_at <= now
        ):
            orders_by_customer.setdefault(order.customer_id, []).append(order)
            revenue_by_order[order.id] = order_revenue

    control = _group_metrics(control_customers, orders_by_customer, revenue_by_order)
    treatment = _group_metrics(
        treatment_customers,
        orders_by_customer,
        revenue_by_order,
    )
    control_arpc = control["average_revenue_per_customer"]
    treatment_arpc = treatment["average_revenue_per_customer"]
    control_rate = control["conversion_rate"]
    treatment_rate = treatment["conversion_rate"]
    control_margin = control["margin_percent"]
    treatment_margin = treatment["margin_percent"]

    incremental_revenue = (
        round(
            (treatment_arpc - control_arpc) * treatment["customers"],
            2,
        )
        if control_arpc is not None and treatment_arpc is not None
        else None
    )
    revenue_growth = (
        round((treatment_arpc - control_arpc) / control_arpc * 100, 2)
        if control_arpc not in (None, 0) and treatment_arpc is not None
        else None
    )
    conversion_lift = (
        round(treatment_rate - control_rate, 2)
        if treatment_rate is not None and control_rate is not None
        else None
    )
    margin_change = (
        round(treatment_margin - control_margin, 2)
        if treatment_margin is not None and control_margin is not None
        else None
    )
    sample_issues = []
    if len(assigned_customers) < MIN_CUSTOMERS_PER_VARIANT * 2:
        sample_issues.append("fewer than 20 customers are assigned")
    if control["customers"] < MIN_CUSTOMERS_PER_VARIANT:
        sample_issues.append("fewer than 10 customers are assigned to control")
    if treatment["customers"] < MIN_CUSTOMERS_PER_VARIANT:
        sample_issues.append("fewer than 10 customers are assigned to treatment")
    if observation_days < MIN_OBSERVATION_DAYS:
        sample_issues.append("the 7-day observation window is not complete")
    observation_complete = observation_days >= MIN_OBSERVATION_DAYS
    sample_sufficient = (
        control["customers"] >= MIN_CUSTOMERS_PER_VARIANT
        and treatment["customers"] >= MIN_CUSTOMERS_PER_VARIANT
    )
    sample_status = (
        "insufficient_observation_window"
        if not observation_complete
        else "insufficient_sample"
        if not sample_sufficient
        else "sufficient_sample"
    )

    return {
        "status": "success",
        "experiment_id": experiment.experiment_id,
        "opportunity": experiment.opportunity_title,
        "incremental_revenue": incremental_revenue,
        "revenue_growth_percent": revenue_growth,
        "conversion_lift": conversion_lift,
        "margin_change": margin_change,
        "metrics": {
            "baseline_revenue": control["revenue"],
            "experiment_revenue": treatment["revenue"],
            "baseline_conversion": control_rate,
            "experiment_conversion": treatment_rate,
            "baseline_margin": control_margin,
            "experiment_margin": treatment_margin,
            "eligible_customers": len(assigned_customers),
            "control_customers": control["customers"],
            "treatment_customers": treatment["customers"],
            "control_conversions": control["conversions"],
            "treatment_conversions": treatment["conversions"],
            "control_orders": control["orders"],
            "treatment_orders": treatment["orders"],
            "control_revenue": control["revenue"],
            "treatment_revenue": treatment["revenue"],
            "control_conversion_rate": control_rate,
            "treatment_conversion_rate": treatment_rate,
            "control_average_revenue_per_customer": control_arpc,
            "treatment_average_revenue_per_customer": treatment_arpc,
            "control_average_order_value": control["average_order_value"],
            "treatment_average_order_value": treatment["average_order_value"],
            "incremental_revenue_estimate": incremental_revenue,
            "relative_lift_percent": revenue_growth,
            "control_margin_percent": control_margin,
            "treatment_margin_percent": treatment_margin,
            "sample_status": sample_status,
            "sample_issues": sample_issues,
            "minimum_customers_per_variant": MIN_CUSTOMERS_PER_VARIANT,
            "minimum_observation_days": MIN_OBSERVATION_DAYS,
            "observation_days": round(observation_days, 2),
        },
    }
