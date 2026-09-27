import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.experiment_engine import get_eligible_customers
from services.measurement_engine import calculate_experiment_metrics
from services.opportunity_engine import (
    CommerceCartEvent,
    CommerceOrder,
    CommercePayment,
    detect_opportunities,
)


class RazorpayOpportunityTests(unittest.TestCase):
    def test_failed_payment_opportunity_uses_only_mapped_inr_payment_attempts(self):
        now = datetime(2026, 9, 27, 12)
        orders = [
            CommerceOrder(
                customer_id=1,
                amount=0,
                status="payment_failed",
                created_at=now,
                order_id=10,
                provider="razorpay",
                currency="INR",
                amount_minor=10000,
                provider_account="merchant-a",
            )
        ]
        payments = [
            CommercePayment(
                order_id=10,
                customer_id=1,
                amount=0,
                status="failed",
                provider="razorpay",
                provider_account="merchant-a",
                external_payment_id="pay-failed-1",
                currency="INR",
                amount_minor=10000,
            ),
            CommercePayment(
                order_id=10,
                customer_id=1,
                amount=100,
                status="failed",
                provider="razorpay",
                provider_account="merchant-a",
                external_payment_id="pay-failed-1",
                currency="INR",
                amount_minor=10000,
            ),
            CommercePayment(
                order_id=999,
                customer_id=1,
                amount=200,
                status="failed",
                provider="razorpay",
                provider_account="merchant-a",
                external_payment_id="pay-unmapped",
                currency="INR",
                amount_minor=20000,
            ),
            CommercePayment(
                order_id=10,
                customer_id=1,
                amount=300,
                status="failed",
                provider="razorpay",
                provider_account="merchant-a",
                external_payment_id="pay-usd",
                currency="USD",
                amount_minor=30000,
            ),
            CommercePayment(
                order_id=10,
                customer_id=2,
                amount=400,
                status="failed",
                provider="razorpay",
                provider_account="merchant-a",
                external_payment_id="pay-wrong-customer",
                currency="INR",
                amount_minor=40000,
            ),
        ]
        opportunities = detect_opportunities(
            customer_ids=[1, 2],
            orders=orders,
            cart_events=[],
            payments=payments,
            max_discount=10,
            as_of=now,
        )
        failed = next(
            item
            for item in opportunities
            if item["title"] == "Recover Failed Payments"
        )
        self.assertEqual(failed["affected_customers"], 1)
        self.assertEqual(failed["affected_orders"], 1)
        self.assertEqual(failed["potential_revenue"], 60)

    def test_paid_razorpay_orders_contribute_to_purchase_history_and_aov(self):
        now = datetime(2026, 9, 27, 12)
        old_purchase = now - timedelta(days=120)
        orders = [
            CommerceOrder(
                1,
                999999,
                "completed",
                old_purchase,
                order_id=1,
                provider="razorpay",
                currency="INR",
                amount_minor=600000,
                provider_account="merchant-a",
            ),
            CommerceOrder(
                1,
                999999,
                "completed",
                old_purchase + timedelta(days=1),
                order_id=2,
                provider="razorpay",
                currency="INR",
                amount_minor=600000,
                provider_account="merchant-a",
            ),
            CommerceOrder(
                2,
                999999,
                "completed",
                old_purchase,
                order_id=3,
                provider="razorpay",
                currency="USD",
                amount_minor=900000,
                provider_account="merchant-a",
            ),
            CommerceOrder(
                3,
                999999,
                "completed",
                old_purchase,
                order_id=4,
                provider="razorpay",
                currency="INR",
                amount_minor=350000,
                provider_account="merchant-a",
            ),
            CommerceOrder(
                4,
                999999,
                "payment_failed",
                old_purchase,
                order_id=5,
                provider="razorpay",
                currency="INR",
                amount_minor=900000,
                provider_account="merchant-a",
            ),
        ]
        orders.extend(
            CommerceOrder(
                customer_id,
                999999,
                "completed",
                old_purchase,
                order_id=customer_id + 10,
                provider="razorpay",
                currency="INR",
                amount_minor=100000,
                provider_account="merchant-a",
            )
            for customer_id in range(5, 14)
        )
        opportunities = detect_opportunities(
            customer_ids=[1, 2, 3, 4],
            orders=orders,
            cart_events=[],
            payments=[
                CommercePayment(
                    5,
                    4,
                    9000,
                    "failed",
                    provider="razorpay",
                    provider_account="merchant-a",
                    external_payment_id="pay-failed",
                    currency="INR",
                    amount_minor=900000,
                )
            ],
            max_discount=10,
            as_of=now,
        )
        by_title = {item["title"]: item for item in opportunities}
        self.assertEqual(
            by_title["Increase Repeat Purchases"]["affected_orders"],
            12,
        )
        self.assertEqual(
            by_title["Reactivate High-Value Customers"]["affected_customers"],
            1,
        )
        self.assertEqual(
            by_title["Reactivate High-Value Customers"]["potential_revenue"],
            1800,
        )
        self.assertEqual(
            by_title["Increase Average Order Value"]["affected_orders"],
            12,
        )
        self.assertEqual(
            by_title["Increase Average Order Value"]["evidence"],
            "Completed-order AOV is ₹2,042, below the ₹4,000 demo benchmark across 12 orders.",
        )

    def test_failed_payment_opportunity_needs_valid_linked_order_and_currency(self):
        now = datetime(2026, 9, 27, 12)
        opportunities = detect_opportunities(
            customer_ids=[1],
            orders=[
                CommerceOrder(
                    1,
                    100,
                    "payment_failed",
                    now,
                    order_id=1,
                    provider="razorpay",
                    currency="INR",
                    amount_minor=10000,
                    provider_account="merchant-a",
                )
            ],
            cart_events=[],
            payments=[
                CommercePayment(
                    1,
                    1,
                    100,
                    "failed",
                    provider="razorpay",
                    provider_account="merchant-a",
                    external_payment_id="pay-unsupported",
                    currency="USD",
                    amount_minor=10000,
                )
            ],
            max_discount=10,
            as_of=now,
        )
        self.assertNotIn(
            "Recover Failed Payments",
            {item["title"] for item in opportunities},
        )

    def test_experiment_eligibility_uses_supported_normalized_commerce(self):
        now = datetime(2026, 9, 27, 12)
        orders = [
            CommerceOrder(
                1, 1, "completed", now - timedelta(days=120), 1,
                "razorpay", "INR", 600000,
            ),
            CommerceOrder(
                2, 1, "completed", now - timedelta(days=120), 2,
                "razorpay", "USD", 600000,
            ),
            CommerceOrder(
                3, 1, "completed", now - timedelta(days=120), 3,
                "razorpay", "INR", 10000,
            ),
            CommerceOrder(
                4, 1, "payment_failed", now, 4,
                "razorpay", "INR", 20000, "merchant-a",
            ),
        ]
        payments = [
            CommercePayment(
                4, 4, 20, "failed",
                provider="razorpay",
                provider_account="merchant-a",
                external_payment_id="pay-valid-failure",
                currency="INR",
                amount_minor=2000,
            ),
            CommercePayment(
                404, 1, 20, "failed",
                provider="razorpay",
                provider_account="merchant-a",
                external_payment_id="pay-unmapped-failure",
                currency="INR",
                amount_minor=2000,
            ),
        ]
        failed_customers = get_eligible_customers(
            "Recover Failed Payments",
            [1, 2, 3, 4],
            orders,
            [],
            payments,
            as_of=now,
        )
        self.assertEqual(failed_customers, [4])
        repeat_customers = get_eligible_customers(
            "Increase Repeat Purchases",
            [1, 2, 3, 4],
            orders,
            [],
            payments,
            as_of=now,
        )
        self.assertEqual(repeat_customers, [1, 3])


class RazorpayMeasurementTests(unittest.TestCase):
    def test_measurement_uses_only_paid_assigned_inr_orders_at_provider_event_time(self):
        started_at = datetime(2026, 9, 20, 12)
        now = started_at + timedelta(days=8)
        assignments = [
            SimpleNamespace(
                customer_id=customer_id,
                variant="CONTROL" if customer_id <= 10 else "TREATMENT",
            )
            for customer_id in range(1, 21)
        ]
        experiment = SimpleNamespace(
            experiment_id="EXP-RAZORPAY-MEASUREMENT",
            opportunity_title="Increase Average Order Value",
            started_at=started_at,
        )
        orders = [
            SimpleNamespace(
                id=1,
                customer_id=1,
                amount=999999,
                amount_minor=12345,
                currency="INR",
                provider="razorpay",
                provider_account="merchant-a",
                status="completed",
                created_at=started_at - timedelta(days=2),
                margin_percent=None,
            ),
            SimpleNamespace(
                id=2,
                customer_id=11,
                amount=999999,
                amount_minor=24690,
                currency="INR",
                provider="razorpay",
                provider_account="merchant-a",
                status="completed",
                created_at=started_at - timedelta(days=2),
                margin_percent=None,
            ),
            SimpleNamespace(
                id=3,
                customer_id=2,
                amount=999999,
                amount_minor=90000,
                currency="INR",
                provider="razorpay",
                provider_account="merchant-a",
                status="completed",
                created_at=started_at + timedelta(days=1),
                margin_percent=99,
            ),
            SimpleNamespace(
                id=4,
                customer_id=21,
                amount=999999,
                amount_minor=90000,
                currency="INR",
                provider="razorpay",
                provider_account="merchant-a",
                status="completed",
                created_at=started_at + timedelta(days=1),
                margin_percent=99,
            ),
            SimpleNamespace(
                id=5,
                customer_id=3,
                amount=999999,
                amount_minor=90000,
                currency="USD",
                provider="razorpay",
                provider_account="merchant-a",
                status="completed",
                created_at=started_at + timedelta(days=1),
                margin_percent=99,
            ),
            SimpleNamespace(
                id=6,
                customer_id=4,
                amount=999999,
                amount_minor=90000,
                currency="INR",
                provider="razorpay",
                provider_account="merchant-a",
                status="completed",
                created_at=started_at + timedelta(days=1),
                margin_percent=99,
            ),
        ]

        def payment(order_id, payment_id, status, event_at, currency="INR", amount_minor=0):
            return SimpleNamespace(
                order_id=order_id,
                provider="razorpay",
                provider_account="merchant-a",
                external_payment_id=payment_id,
                currency=currency,
                amount_minor=amount_minor,
                amount=999999,
                status=status,
                provider_event_at=event_at,
            )

        payments = [
            payment(1, "pay-control", "succeeded", started_at + timedelta(days=1), amount_minor=12345),
            payment(1, "pay-control", "succeeded", started_at + timedelta(days=1), amount_minor=12345),
            payment(2, "pay-treatment", "succeeded", started_at + timedelta(days=2), amount_minor=24690),
            payment(3, "pay-before-start", "succeeded", started_at - timedelta(seconds=1), amount_minor=90000),
            payment(4, "pay-unassigned", "succeeded", started_at + timedelta(days=1), amount_minor=90000),
            payment(5, "pay-usd", "succeeded", started_at + timedelta(days=1), currency="USD", amount_minor=90000),
            payment(6, "pay-failed", "failed", started_at + timedelta(days=1), amount_minor=90000),
        ]
        metrics = calculate_experiment_metrics(
            experiment,
            assignments,
            orders,
            payments=payments,
            measured_at=now,
        )
        self.assertEqual(metrics["metrics"]["sample_status"], "sufficient_sample")
        self.assertEqual(metrics["metrics"]["control_orders"], 1)
        self.assertEqual(metrics["metrics"]["treatment_orders"], 1)
        self.assertEqual(metrics["metrics"]["control_revenue"], 123.45)
        self.assertEqual(metrics["metrics"]["treatment_revenue"], 246.9)
        self.assertIsNone(metrics["metrics"]["control_margin_percent"])
        self.assertIsNone(metrics["metrics"]["treatment_margin_percent"])

        early = calculate_experiment_metrics(
            experiment,
            assignments,
            orders,
            payments=payments,
            measured_at=started_at + timedelta(days=3),
        )
        self.assertEqual(
            early["metrics"]["sample_status"],
            "insufficient_observation_window",
        )
        self.assertEqual(early["metrics"]["control_orders"], 1)
        self.assertEqual(early["metrics"]["treatment_orders"], 1)


if __name__ == "__main__":
    unittest.main()
