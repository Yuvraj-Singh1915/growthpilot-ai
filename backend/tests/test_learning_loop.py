import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import HTTPException

os.environ["GROWTHPILOT_DATABASE_URL"] = "sqlite://"
os.environ["GEMINI_API_KEY"] = "isolated-test-key"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from services.experiment_engine import (
    CommerceCartEvent,
    CommerceOrder,
    CommercePayment,
    assign_variants,
)
from services.learning_engine import build_learning_evidence
from services.opportunity_engine import detect_opportunities
from services.measurement_engine import calculate_experiment_metrics


class LearningLoopTests(unittest.TestCase):
    def setUp(self):
        self.previous_session_factory = main.SessionLocal
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        main.Base.metadata.create_all(bind=self.engine)
        main.SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )
        self.client_db = main.SessionLocal()

    def tearDown(self):
        self.client_db.close()
        main.SessionLocal = self.previous_session_factory
        self.engine.dispose()

    def add_customer(self, created_at=None):
        customer = main.Customer(created_at=created_at or datetime.utcnow())
        self.client_db.add(customer)
        self.client_db.flush()
        return customer

    def add_opportunity(self, title, discount=0):
        opportunity = main.Opportunity(
            title=title,
            priority="MEDIUM",
            potential_revenue=20000,
            recommended_action="Local observed strategy",
            suggested_discount=discount,
            risk="LOW",
        )
        self.client_db.add(opportunity)
        self.client_db.flush()
        return opportunity

    def add_order(self, customer_id, amount, created_at, margin=25, status="completed"):
        order = main.Order(
            customer_id=customer_id,
            amount=amount,
            status=status,
            margin_percent=margin,
            created_at=created_at,
        )
        self.client_db.add(order)
        self.client_db.flush()
        return order

    def add_payment(self, order, status="succeeded", amount=None, created_at=None):
        payment = main.Payment(
            order_id=order.id,
            status=status,
            amount=order.amount if amount is None else amount,
            created_at=created_at or order.created_at,
        )
        self.client_db.add(payment)
        self.client_db.flush()
        return payment

    def add_experiment_with_assignments(
        self,
        opportunity,
        original_action,
        control_count=10,
        treatment_count=10,
        started_days_ago=2,
        treatment_margin=5,
    ):
        now = datetime.utcnow()
        started_at = now - timedelta(days=started_days_ago)
        experiment = main.Experiment(
            experiment_id=f"EXP-TEST-{len(self.client_db.query(main.Experiment).all()) + 1}",
            opportunity_id=opportunity.id,
            opportunity_title=opportunity.title,
            status="RUNNING",
            risk="LOW",
            recommended_action=original_action,
            original_strategy_action=original_action,
            strategy_code="test.strategy",
            hypothesis=f"Test hypothesis for {opportunity.title}",
            control_variant="Existing experience",
            treatment_variant=original_action,
            traffic_percentage=90,
            holdout_percentage=10,
            budget=50000,
            max_discount=10,
            suggested_discount=opportunity.suggested_discount,
            min_margin=20,
            target_growth=15,
            created_at=started_at,
            started_at=started_at,
            updated_at=started_at,
        )
        self.client_db.add(experiment)
        self.client_db.flush()

        assigned = []
        for index in range(control_count + treatment_count):
            customer = self.add_customer(created_at=started_at - timedelta(days=1))
            variant = "CONTROL" if index < control_count else "TREATMENT"
            assignment = main.ExperimentAssignment(
                experiment_id=experiment.id,
                customer_id=customer.id,
                variant=variant,
                assigned_at=started_at,
            )
            self.client_db.add(assignment)
            assigned.append((customer, variant))

        observed_at = now - timedelta(days=1)
        for customer, variant in assigned:
            margin = 25 if variant == "CONTROL" else treatment_margin
            order = self.add_order(customer.id, 200 if variant == "TREATMENT" else 100, observed_at, margin)
            self.add_payment(order)
        self.client_db.commit()
        return experiment

    def create_approved_experiment(self, opportunity_title, action, strategy_code=None):
        response = main.create_experiment(
            main.ExperimentRequest(
                opportunity=opportunity_title,
                decision="APPROVE",
                recommended_action=action,
                risk="LOW",
                budget=50000,
                max_discount=10,
                min_margin=20,
                target_growth=15,
                strategy_code=strategy_code,
            )
        )
        return response

    def test_deterministic_assignment_is_ninety_control_ten_treatment(self):
        first = assign_variants("EXP-STABLE", list(range(1, 101)))
        second = assign_variants("EXP-STABLE", list(reversed(range(1, 101))))
        self.assertEqual(first, second)
        self.assertEqual(sum(variant == "CONTROL" for _, variant in first), 90)
        self.assertEqual(sum(variant == "TREATMENT" for _, variant in first), 10)

    def test_experiment_identity_reuses_equivalent_strategy_and_distinguishes_parameters(self):
        now = datetime.utcnow()
        for _ in range(100):
            customer = self.add_customer()
            self.add_order(customer.id, 1200, now - timedelta(days=90))
        aov = self.add_opportunity("Increase Average Order Value", discount=0)
        carts = self.add_opportunity("Recover Abandoned Carts", discount=0)
        for customer in self.client_db.query(main.Customer).all():
            self.client_db.add(
                main.CartEvent(
                    customer_id=customer.id,
                    cart_value=1000,
                    event_type="checkout_started",
                    created_at=now - timedelta(days=1),
                )
            )
        self.client_db.commit()

        case_a = self.create_approved_experiment(
            aov.title, "Increase AOV using targeted offer"
        )
        repeated = self.create_approved_experiment(
            aov.title, "Increase AOV using targeted offer"
        )
        self.assertEqual(case_a["experiment_id"], repeated["experiment_id"])
        self.assertTrue(repeated["reused"])

        case_b = self.create_approved_experiment(
            aov.title, "Improve average order value with a targeted discount"
        )
        self.assertEqual(case_a["experiment_id"], case_b["experiment_id"])
        self.assertTrue(case_b["reused"])

        aov.suggested_discount = 5
        self.client_db.commit()
        case_c = self.create_approved_experiment(
            aov.title, "Improve average order value with a targeted discount"
        )
        self.assertNotEqual(case_a["experiment_id"], case_c["experiment_id"])

        case_d = self.create_approved_experiment(
            carts.title, "Send personalized recovery offer"
        )
        self.assertNotEqual(case_c["experiment_id"], case_d["experiment_id"])

    def test_legacy_active_experiment_with_old_split_is_preserved_not_reused(self):
        now = datetime.utcnow()
        customers = []
        for _ in range(100):
            customer = self.add_customer()
            customers.append(customer)
            self.add_order(customer.id, 1200, now - timedelta(days=90))
        opportunity = self.add_opportunity("Increase Average Order Value")
        legacy = main.Experiment(
            experiment_id="EXP-LEGACY-SPLIT",
            opportunity_id=opportunity.id,
            opportunity_title=opportunity.title,
            status="RUNNING",
            risk="LOW",
            recommended_action="Increase AOV using targeted offer",
            original_strategy_action="Increase AOV using targeted offer",
            hypothesis="Legacy persisted hypothesis",
            control_variant="Control",
            treatment_variant="Treatment",
            traffic_percentage=90,
            holdout_percentage=10,
            budget=50000,
            max_discount=10,
            suggested_discount=0,
            min_margin=20,
            target_growth=15,
            created_at=now,
            started_at=now,
            updated_at=now,
        )
        self.client_db.add(legacy)
        self.client_db.flush()
        for index, customer in enumerate(customers):
            self.client_db.add(
                main.ExperimentAssignment(
                    experiment_id=legacy.id,
                    customer_id=customer.id,
                    variant="CONTROL" if index < 10 else "TREATMENT",
                    assigned_at=now,
                )
            )
        self.client_db.commit()

        created = self.create_approved_experiment(
            opportunity.title,
            "Improve average order value with a targeted discount",
        )
        self.assertNotEqual(created["experiment_id"], legacy.experiment_id)
        self.assertFalse(created["reused"])
        self.assertEqual(
            self.client_db.query(main.Experiment)
            .filter_by(experiment_id=legacy.experiment_id)
            .one()
            .status,
            "RUNNING",
        )
        legacy_assignments = (
            self.client_db.query(main.ExperimentAssignment)
            .filter(main.ExperimentAssignment.experiment_id == legacy.id)
            .all()
        )
        old_counts = {
            variant: sum(item.variant == variant for item in legacy_assignments)
            for variant in {"CONTROL", "TREATMENT"}
        }
        self.assertEqual(old_counts, {"CONTROL": 10, "TREATMENT": 90})

    def test_observed_order_ingestion_is_validated_and_idempotent(self):
        customer = self.add_customer()
        self.client_db.commit()
        request = main.ObservedOrderRequest(
            external_order_id="LOCAL-ORDER-1",
            customer_id=customer.id,
            amount=123.45,
            status="completed",
            margin_percent=25,
            payment_status="succeeded",
        )
        created = main.ingest_observed_order(request)
        self.assertEqual(created["status"], "success")
        self.assertFalse(created["duplicate"])
        self.assertEqual(created["payment"]["amount"], 123.45)

        repeated = main.ingest_observed_order(request)
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(
            self.client_db.query(main.Order)
            .filter_by(external_order_id="LOCAL-ORDER-1")
            .count(),
            1,
        )
        self.assertEqual(
            self.client_db.query(main.Payment)
            .filter_by(order_id=created["order"]["id"])
            .count(),
            1,
        )

        with self.assertRaises(HTTPException) as invalid_customer:
            main.ingest_observed_order(
                main.ObservedOrderRequest(
                    external_order_id="LOCAL-ORDER-2",
                    customer_id=99999,
                    amount=50,
                )
            )
        self.assertEqual(invalid_customer.exception.status_code, 404)
        self.assertEqual(self.client_db.query(main.Order).count(), 1)

        with self.assertRaises(HTTPException) as conflicting_duplicate:
            main.ingest_observed_order(
                main.ObservedOrderRequest(
                    external_order_id="LOCAL-ORDER-1",
                    customer_id=customer.id,
                    amount=999,
                    status="completed",
                    margin_percent=25,
                    payment_status="succeeded",
                )
            )
        self.assertEqual(conflicting_duplicate.exception.status_code, 409)

    def test_measurement_excludes_pre_start_unassigned_and_failed_payment_orders(self):
        now = datetime.utcnow()
        started_at = now - timedelta(days=8)
        customers = [self.add_customer() for _ in range(21)]
        assignments = [
            SimpleNamespace(
                customer_id=customer.id,
                variant="CONTROL" if index < 10 else "TREATMENT",
            )
            for index, customer in enumerate(customers[:20])
        ]
        experiment = SimpleNamespace(
            experiment_id="EXP-MEASURE",
            opportunity_title="Increase Average Order Value",
            started_at=started_at,
        )
        orders = []
        payments = []
        before_start = self.add_order(
            customers[0].id, 9999, started_at - timedelta(days=1), margin=80
        )
        orders.append(before_start)
        payments.append(self.add_payment(before_start))
        control_order = self.add_order(
            customers[1].id, 100, now - timedelta(days=1), margin=20
        )
        orders.append(control_order)
        payments.append(self.add_payment(control_order))
        treatment_order = self.add_order(
            customers[10].id, 200, now - timedelta(days=1), margin=25
        )
        orders.append(treatment_order)
        payments.append(self.add_payment(treatment_order))
        failed_order = self.add_order(
            customers[11].id, 5000, now - timedelta(days=1), margin=90
        )
        orders.append(failed_order)
        payments.append(self.add_payment(failed_order, status="failed"))
        unassigned_order = self.add_order(
            customers[20].id, 8000, now - timedelta(days=1), margin=90
        )
        orders.append(unassigned_order)
        payments.append(self.add_payment(unassigned_order))

        self.client_db.commit()
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
        self.assertEqual(metrics["metrics"]["control_revenue"], 100)
        self.assertEqual(metrics["metrics"]["treatment_revenue"], 200)
        self.assertEqual(metrics["metrics"]["treatment_margin_percent"], 25)

        early_metrics = calculate_experiment_metrics(
            experiment,
            assignments,
            orders,
            payments=payments,
            measured_at=started_at + timedelta(days=3),
        )
        self.assertEqual(
            early_metrics["metrics"]["sample_status"],
            "insufficient_observation_window",
        )
        insufficient_assignments = assignments[:-1]
        small_sample = calculate_experiment_metrics(
            experiment,
            insufficient_assignments,
            orders,
            payments=payments,
            measured_at=now,
        )
        self.assertEqual(
            small_sample["metrics"]["sample_status"],
            "insufficient_sample",
        )

    def test_post_start_ingested_order_for_assigned_customer_is_measured(self):
        opportunity = self.add_opportunity("Increase Average Order Value")
        experiment = self.add_experiment_with_assignments(
            opportunity,
            "Recommend relevant product bundles",
            started_days_ago=8,
            treatment_margin=25,
        )
        assignment = (
            self.client_db.query(main.ExperimentAssignment)
            .filter(
                main.ExperimentAssignment.experiment_id == experiment.id,
                main.ExperimentAssignment.variant == "TREATMENT",
            )
            .first()
        )
        result = main.ingest_observed_order(
            main.ObservedOrderRequest(
                external_order_id="POST-START-ASSIGNED-1",
                customer_id=assignment.customer_id,
                amount=333,
                status="completed",
                margin_percent=24,
                payment_status="succeeded",
            )
        )
        self.assertFalse(result["duplicate"])

        metrics = main._get_experiment_measurement(
            self.client_db, experiment
        )
        self.assertEqual(metrics["metrics"]["sample_status"], "sufficient_sample")
        self.assertEqual(metrics["metrics"]["treatment_orders"], 11)
        self.assertEqual(metrics["metrics"]["treatment_revenue"], 2333)

    def test_razorpay_commerce_snapshot_preserves_normalized_provider_data(self):
        now = datetime.utcnow()
        customer = self.add_customer()
        order = main.Order(
            customer_id=customer.id,
            amount=123.45,
            amount_minor=12345,
            currency="INR",
            status="completed",
            external_order_id="internal-order",
            provider="razorpay",
            provider_account="merchant-test",
            provider_order_id="order_razorpay_test",
            created_at=now - timedelta(days=2),
        )
        self.client_db.add(order)
        self.client_db.flush()
        payment = main.Payment(
            order_id=order.id,
            status="succeeded",
            amount=123.45,
            amount_minor=12345,
            currency="INR",
            provider="razorpay",
            provider_account="merchant-test",
            external_payment_id="pay_razorpay_test",
            provider_event_at=now - timedelta(days=1),
            received_at=now,
            created_at=now,
        )
        self.client_db.add(payment)
        self.client_db.commit()

        customer_ids, orders, payments = main._commerce_intelligence_inputs(
            self.client_db
        )
        snapshot_order = next(item for item in orders if item.order_id == order.id)
        snapshot_payment = next(
            item for item in payments if item.external_payment_id == "pay_razorpay_test"
        )
        self.assertIn(customer.id, customer_ids)
        self.assertEqual(snapshot_order.provider, "razorpay")
        self.assertEqual(snapshot_order.provider_order_id, "order_razorpay_test")
        self.assertEqual(snapshot_order.currency, "INR")
        self.assertEqual(snapshot_order.amount_minor, 12345)
        self.assertEqual(snapshot_order.created_at, payment.provider_event_at)
        self.assertEqual(snapshot_payment.provider_account, "merchant-test")
        self.assertEqual(snapshot_payment.amount_minor, 12345)

    def test_evaluation_does_not_finalize_before_window_or_sample(self):
        opportunity = self.add_opportunity("Increase Average Order Value")
        early = self.add_experiment_with_assignments(
            opportunity,
            "Increase AOV with a measured bundle",
            started_days_ago=2,
            treatment_margin=1,
        )
        early_result = main.evaluate_result(
            main.ResultEvaluationRequest(experiment_id=early.experiment_id)
        )
        self.assertEqual(early_result["experiment_status"], "RUNNING")
        self.assertEqual(self.client_db.query(main.LearningRecord).count(), 0)
        self.assertEqual(
            self.client_db.query(main.ExperimentEvaluation).count(),
            1,
        )

        underpowered_opportunity = self.add_opportunity("Increase Repeat Purchases")
        underpowered = self.add_experiment_with_assignments(
            underpowered_opportunity,
            "Recommend a reorder reminder",
            control_count=9,
            treatment_count=10,
            started_days_ago=8,
        )
        result = main.evaluate_result(
            main.ResultEvaluationRequest(experiment_id=underpowered.experiment_id)
        )
        self.assertEqual(result["experiment_status"], "RUNNING")
        self.assertEqual(self.client_db.query(main.LearningRecord).count(), 0)

    def test_final_learning_retains_strategy_is_idempotent_and_scoped(self):
        aov = self.add_opportunity("Increase Average Order Value")
        repeat = self.add_opportunity("Increase Repeat Purchases")
        first = self.add_experiment_with_assignments(
            aov, "Recommend relevant product bundles", started_days_ago=8,
            treatment_margin=25,
        )
        second = self.add_experiment_with_assignments(
            repeat, "Send repeat-purchase reminder", started_days_ago=8,
            treatment_margin=25,
        )
        ai_evaluation = {
            "decision": "STOP",
            "reason": "Observed evidence did not support the tested strategy.",
            "next_action": "Stop this strategy and test a different offer.",
        }
        with patch.object(
            main,
            "_request_ai_experiment_evaluation",
            return_value=(ai_evaluation, True),
        ) as evaluator:
            finalized = main.evaluate_result(
                main.ResultEvaluationRequest(experiment_id=first.experiment_id)
            )
            repeated = main.evaluate_result(
                main.ResultEvaluationRequest(experiment_id=first.experiment_id)
            )
            main.evaluate_result(
                main.ResultEvaluationRequest(experiment_id=second.experiment_id)
            )
        self.assertEqual(finalized["experiment_status"], "STOPPED")
        self.assertEqual(repeated["experiment_status"], "STOPPED")
        self.assertEqual(evaluator.call_count, 2)
        self.assertEqual(self.client_db.query(main.LearningRecord).count(), 2)

        first_record = (
            self.client_db.query(main.LearningRecord)
            .filter_by(experiment_id=first.id)
            .one()
        )
        self.assertEqual(
            first_record.original_strategy_action,
            "Recommend relevant product bundles",
        )
        self.assertEqual(first_record.learning_signal, "NEGATIVE")
        evaluation = (
            self.client_db.query(main.ExperimentEvaluation)
            .filter_by(experiment_id=first.id)
            .one()
        )
        self.assertEqual(
            evaluation.original_strategy_action,
            first_record.original_strategy_action,
        )
        _, same_opportunity_history = main._get_opportunity_learning(
            self.client_db, aov.title
        )
        _, other_opportunity_history = main._get_opportunity_learning(
            self.client_db, repeat.title
        )
        self.assertEqual(len(same_opportunity_history), 1)
        self.assertEqual(
            same_opportunity_history[0]["original_strategy_action"],
            "Recommend relevant product bundles",
        )
        self.assertEqual(len(other_opportunity_history), 1)
        self.assertEqual(
            other_opportunity_history[0]["original_strategy_action"],
            "Send repeat-purchase reminder",
        )

    def test_learning_evidence_requires_sample_and_seven_days(self):
        base_metrics = {
            "sample_status": "sufficient_sample",
            "control_customers": 10,
            "treatment_customers": 10,
            "observation_days": 7,
        }
        self.assertIsNotNone(
            build_learning_evidence("OPTIMIZE", "reason", "next", base_metrics)
        )
        for invalid in (
            {**base_metrics, "sample_status": "insufficient_sample"},
            {**base_metrics, "observation_days": 6.99},
            {**base_metrics, "control_customers": 9},
            {**base_metrics, "treatment_customers": 9},
        ):
            self.assertIsNone(
                build_learning_evidence("OPTIMIZE", "reason", "next", invalid)
            )

    def test_next_action_is_recommendation_only_and_guardrails_precede_ai(self):
        opportunity = self.add_opportunity("Increase Average Order Value", discount=5)
        self.client_db.commit()
        before = {
            model: self.client_db.query(model).count()
            for model in (
                main.Experiment,
                main.ExperimentAssignment,
                main.Order,
                main.Customer,
                main.Payment,
            )
        }
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"decision":"APPROVE","risk":"LOW","recommended_action":"Recommend relevant product bundles","reason":"Observed opportunity evidence supports review."}'
                    )
                )
            ]
        )
        with patch.object(main.client.chat.completions, "create", return_value=response):
            result = main.recommend_autopilot_next_action(
                main.AutopilotNextActionRequest(
                    opportunity=opportunity.title,
                    goal=15,
                    max_discount=10,
                    budget=50000,
                    min_margin=20,
                )
            )
        after = {
            model: self.client_db.query(model).count()
            for model in before
        }
        self.assertEqual(before, after)
        self.assertTrue(result["advisory_only"])
        self.assertEqual(result["campaign_execution"], "not_performed")
        self.assertEqual(
            result["recommended_experiment_configuration"]["control"]["traffic_percent"],
            90,
        )
        self.assertEqual(
            result["recommended_experiment_configuration"]["treatment"]["traffic_percent"],
            10,
        )

        with patch.object(main.client.chat.completions, "create") as model_call:
            unsafe = main.run_strategy(
                main.StrategyRequest(
                    opportunity=opportunity.title,
                    potential_revenue=20000,
                    suggested_discount=11,
                    max_discount=10,
                    min_margin=20,
                )
            )
        self.assertEqual(
            __import__("json").loads(unsafe["strategy"])["decision"],
            "REJECT",
        )
        model_call.assert_not_called()

    def test_data_derived_opportunity_engine_can_return_all_five_categories(self):
        now = datetime.utcnow()
        customer_ids = list(range(1, 51))
        orders = []
        for customer_id in range(1, 11):
            orders.extend(
                [
                    CommerceOrder(customer_id, 1500, "completed", now - timedelta(days=120)),
                    CommerceOrder(customer_id, 1500, "completed", now - timedelta(days=100)),
                ]
            )
        for customer_id in range(11, 21):
            orders.extend(
                CommerceOrder(
                    customer_id, 3000, "completed", now - timedelta(days=120 + index)
                )
                for index in range(5)
            )
        for customer_id in range(21, 31):
            orders.append(
                CommerceOrder(customer_id, 1000, "completed", now - timedelta(days=120))
            )
        orders.extend(
            CommerceOrder(
                customer_id,
                1000,
                "payment_failed",
                now - timedelta(days=1),
                order_id=100 + index,
            )
            for index, customer_id in enumerate(range(41, 51), start=1)
        )
        carts = [
            CommerceCartEvent(
                customer_id, 1000, "checkout_started", now - timedelta(days=1)
            )
            for customer_id in range(31, 41)
        ]
        payments = [
            CommercePayment(100 + index, customer_id, 1000, "failed")
            for index, customer_id in enumerate(range(41, 51), start=1)
        ]
        opportunities = detect_opportunities(
            customer_ids,
            orders,
            carts,
            payments,
            max_discount=10,
            as_of=now,
        )
        self.assertEqual(len(opportunities), 5)
        self.assertEqual(
            {item["title"] for item in opportunities},
            {
                "Recover Abandoned Carts",
                "Increase Repeat Purchases",
                "Increase Average Order Value",
                "Recover Failed Payments",
                "Reactivate High-Value Customers",
            },
        )

    def test_demo_seed_is_isolated_and_idempotent(self):
        first = main.seed_demo_data()
        counts_after_first = (
            self.client_db.query(main.Customer).count(),
            self.client_db.query(main.Order).count(),
            self.client_db.query(main.CartEvent).count(),
            self.client_db.query(main.Payment).count(),
        )
        second = main.seed_demo_data()
        counts_after_second = (
            self.client_db.query(main.Customer).count(),
            self.client_db.query(main.Order).count(),
            self.client_db.query(main.CartEvent).count(),
            self.client_db.query(main.Payment).count(),
        )
        self.assertGreater(first["customers_created"], 0)
        self.assertEqual(second["customers_created"], 0)
        self.assertEqual(counts_after_first, counts_after_second)
        detected = main.get_opportunities(
            main.OpportunityRequest(
                goal=15,
                max_discount=10,
                budget=50000,
                min_margin=20,
            )
        )
        self.assertEqual(len(detected["opportunities"]), 5)

    def test_required_routes_are_registered(self):
        routes = {
            (method, route.path)
            for route in main.app.routes
            for method in (getattr(route, "methods", None) or set())
        }
        expected = {
            ("POST", "/api/demo/seed"),
            ("POST", "/api/opportunities"),
            ("GET", "/api/opportunities"),
            ("POST", "/api/strategy"),
            ("POST", "/api/experiment"),
            ("GET", "/api/experiment/{experiment_id}"),
            ("GET", "/api/customers"),
            ("POST", "/api/orders"),
            ("GET", "/api/orders"),
            ("POST", "/api/measurement"),
            ("POST", "/api/evaluate-result"),
            ("GET", "/api/learning"),
            ("GET", "/api/learning/{opportunity:path}"),
            ("POST", "/api/autopilot/next-action"),
        }
        self.assertTrue(expected <= routes)


if __name__ == "__main__":
    unittest.main()
