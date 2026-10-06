import hashlib
import hmac
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

os.environ["GROWTHPILOT_DATABASE_URL"] = "sqlite://"
os.environ["GEMINI_API_KEY"] = "isolated-test-key"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class RazorpayEndToEndTests(unittest.IsolatedAsyncioTestCase):
    webhook_secret = "local-deterministic-webhook-test-secret"
    provider_account = "e2e-test-account"

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
        self.db = main.SessionLocal()
        self.environment = patch.dict(
            os.environ,
            {
                "RAZORPAY_MODE": "test",
                "RAZORPAY_WEBHOOK_SECRET": self.webhook_secret,
                "RAZORPAY_ACCOUNT_REFERENCE": self.provider_account,
            },
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.db.close()
        main.SessionLocal = self.previous_session_factory
        self.engine.dispose()

    def add_aov_cohort(self, count=96):
        now = datetime.utcnow()
        customers = [
            main.Customer(created_at=now - timedelta(days=120))
            for _ in range(count)
        ]
        self.db.add_all(customers)
        self.db.flush()
        self.db.add_all(
            main.Order(
                customer_id=customer.id,
                amount=1200,
                status="completed",
                margin_percent=25,
                created_at=now - timedelta(days=30),
            )
            for customer in customers
        )
        self.db.add(
            main.MerchantGoal(
                goal=15,
                max_discount=10,
                budget=50000,
                min_margin=20,
            )
        )
        self.db.commit()
        return customers

    def payment_payload(
        self,
        customer_id,
        order_id,
        payment_id,
        amount_minor,
        created_at,
    ):
        return {
            "entity": "event",
            "account_id": self.provider_account,
            "event": "payment.captured",
            "created_at": created_at,
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "entity": "payment",
                        "amount": amount_minor,
                        "currency": "INR",
                        "status": "captured",
                        "order_id": order_id,
                        "created_at": created_at,
                        "notes": {
                            "growthpilot_customer_id": str(customer_id)
                        },
                    }
                }
            },
        }

    async def deliver_signed_webhook(self, payload, event_id):
        raw_body = json.dumps(payload, separators=(",", ":")).encode()
        signature = hmac.new(
            self.webhook_secret.encode(),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        headers = [
            (b"x-razorpay-event-id", event_id.encode()),
            (b"x-razorpay-signature", signature.encode()),
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/webhooks/razorpay",
            "raw_path": b"/api/webhooks/razorpay",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
        }
        body_sent = False

        async def receive():
            nonlocal body_sent
            if body_sent:
                return {"type": "http.disconnect"}
            body_sent = True
            return {
                "type": "http.request",
                "body": raw_body,
                "more_body": False,
            }

        request = Request(scope, receive)
        return await main.ingest_razorpay_webhook(request)

    def gemini_response(self, content):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=content)
                )
            ]
        )

    async def test_signed_razorpay_payment_flows_through_growth_system(self):
        customers = self.add_aov_cohort()
        mapped_customer = customers[0]
        event_time = int(
            (datetime.utcnow() - timedelta(days=10)).timestamp()
        )

        mapped_order = main.ingest_observed_order(
            main.ObservedOrderRequest(
                external_order_id="e2e-internal-order-1",
                customer_id=mapped_customer.id,
                amount=123.45,
                status="pending",
                margin_percent=25,
                currency="INR",
                provider="razorpay",
                provider_account=self.provider_account,
                provider_order_id="order_e2e_initial",
            )
        )
        order_id = mapped_order["order"]["id"]
        self.db.expire_all()
        order = self.db.query(main.Order).filter_by(id=order_id).one()
        self.assertEqual(order.status, "pending")
        self.assertEqual(order.provider_order_id, "order_e2e_initial")
        self.assertEqual(self.db.query(main.Payment).count(), 0)

        webhook_result = await self.deliver_signed_webhook(
            self.payment_payload(
                mapped_customer.id,
                "order_e2e_initial",
                "pay_e2e_initial",
                12345,
                event_time,
            ),
            event_id="evt_e2e_initial",
        )
        self.assertEqual(webhook_result["processing_status"], "processed")
        self.db.expire_all()
        order = self.db.query(main.Order).filter_by(id=order_id).one()
        payment = self.db.query(main.Payment).filter_by(order_id=order_id).one()
        self.assertEqual(order.status, "completed")
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(payment.external_payment_id, "pay_e2e_initial")
        self.assertEqual(
            self.db.query(main.WebhookEvent)
            .filter_by(event_id="evt_e2e_initial")
            .one()
            .processing_status,
            "processed",
        )

        opportunity_result = main.get_opportunities(
            main.OpportunityRequest(
                goal=15,
                max_discount=10,
                budget=50000,
                min_margin=20,
            )
        )
        aov_opportunity = next(
            item
            for item in opportunity_result["opportunities"]
            if item["title"] == "Increase Average Order Value"
        )
        self.assertGreater(aov_opportunity["affected_customers"], 0)
        self.assertEqual(
            self.db.query(main.Opportunity)
            .filter_by(title="Increase Average Order Value")
            .count(),
            1,
        )
        _, commerce_orders, commerce_payments = (
            main._commerce_intelligence_inputs(self.db)
        )
        self.assertTrue(
            any(
                item.order_id == order_id
                and item.provider == "razorpay"
                and item.status == "completed"
                for item in commerce_orders
            )
        )
        self.assertTrue(
            any(
                item.external_payment_id == "pay_e2e_initial"
                and item.status == "succeeded"
                for item in commerce_payments
            )
        )

        strategy_api_call = main.client.chat.completions.create
        approved_strategy = (
            '{"decision":"APPROVE","risk":"LOW",'
            '"recommended_action":"Recommend relevant product bundles",'
            '"reason":"The observed AOV opportunity is suitable for a controlled test."}'
        )
        final_evaluation = (
            '{"decision":"CONTINUE",'
            '"reason":"Observed treatment margin meets the merchant guardrail.",'
            '"next_action":"Continue the successful controlled strategy."}'
        )
        with patch.object(
            main.client.chat.completions,
            "create",
            side_effect=[
                self.gemini_response(approved_strategy),
                self.gemini_response(final_evaluation),
            ],
        ) as gemini_call:
            unsafe_strategy = main.run_strategy(
                main.StrategyRequest(
                    opportunity=aov_opportunity["title"],
                    potential_revenue=aov_opportunity["potential_revenue"],
                    suggested_discount=11,
                    max_discount=10,
                    min_margin=20,
                )
            )
            self.assertEqual(
                json.loads(unsafe_strategy["strategy"])["decision"],
                "REJECT",
            )
            gemini_call.assert_not_called()

            strategy_result = main.run_strategy(
                main.StrategyRequest(
                    opportunity=aov_opportunity["title"],
                    potential_revenue=aov_opportunity["potential_revenue"],
                    suggested_discount=aov_opportunity["suggested_discount"],
                    max_discount=10,
                    min_margin=20,
                    budget=50000,
                    target_growth=15,
                )
            )
            strategy = json.loads(strategy_result["strategy"])
            self.assertEqual(strategy["decision"], "APPROVE")
            gemini_call.assert_called_once()

            experiment_result = main.create_experiment(
                main.ExperimentRequest(
                    opportunity=aov_opportunity["title"],
                    decision=strategy["decision"],
                    recommended_action=strategy["recommended_action"],
                    risk=strategy["risk"],
                    budget=50000,
                    max_discount=10,
                    min_margin=20,
                    target_growth=15,
                )
            )
            self.assertEqual(experiment_result["status"], "launched")
            self.assertEqual(
                experiment_result["assigned_customers"]["control"],
                86,
            )
            self.assertEqual(
                experiment_result["assigned_customers"]["treatment"],
                10,
            )
            experiment = (
                self.db.query(main.Experiment)
                .filter_by(experiment_id=experiment_result["experiment_id"])
                .one()
            )
            assignments = (
                self.db.query(main.ExperimentAssignment)
                .filter_by(experiment_id=experiment.id)
                .all()
            )
            self.assertEqual(len(assignments), 96)
            self.assertEqual(
                sum(item.variant == "CONTROL" for item in assignments),
                86,
            )
            self.assertEqual(
                sum(item.variant == "TREATMENT" for item in assignments),
                10,
            )

            experiment.started_at = datetime.utcnow() - timedelta(days=8)
            self.db.commit()

            control_customer_id = next(
                item.customer_id
                for item in assignments
                if item.variant == "CONTROL"
            )
            treatment_customer_id = next(
                item.customer_id
                for item in assignments
                if item.variant == "TREATMENT"
            )
            control_order_result = main.ingest_observed_order(
                main.ObservedOrderRequest(
                    external_order_id="e2e-control-observation",
                    customer_id=control_customer_id,
                    amount=1000,
                    status="completed",
                    margin_percent=25,
                    payment_status="succeeded",
                )
            )
            self.assertFalse(control_order_result["duplicate"])

            treatment_order_result = main.ingest_observed_order(
                main.ObservedOrderRequest(
                    external_order_id="e2e-internal-order-treatment",
                    customer_id=treatment_customer_id,
                    amount=2000,
                    status="pending",
                    margin_percent=25,
                    currency="INR",
                    provider="razorpay",
                    provider_account=self.provider_account,
                    provider_order_id="order_e2e_treatment",
                )
            )
            treatment_order_id = treatment_order_result["order"]["id"]
            treatment_webhook_result = await self.deliver_signed_webhook(
                self.payment_payload(
                    treatment_customer_id,
                    "order_e2e_treatment",
                    "pay_e2e_treatment",
                    200000,
                    int(datetime.utcnow().timestamp()),
                ),
                event_id="evt_e2e_treatment",
            )
            self.assertEqual(
                treatment_webhook_result["processing_status"],
                "processed",
            )
            self.db.expire_all()
            treatment_order = (
                self.db.query(main.Order)
                .filter_by(id=treatment_order_id)
                .one()
            )
            self.assertEqual(treatment_order.status, "completed")
            self.assertEqual(
                self.db.query(main.Payment)
                .filter_by(order_id=treatment_order_id)
                .one()
                .status,
                "succeeded",
            )

            measurement = main.calculate_measurement(
                main.MeasurementRequest(
                    experiment_id=experiment_result["experiment_id"]
                )
            )
            self.assertEqual(
                measurement["metrics"]["sample_status"],
                "sufficient_sample",
            )
            self.assertEqual(measurement["metrics"]["control_customers"], 86)
            self.assertEqual(measurement["metrics"]["treatment_customers"], 10)
            self.assertEqual(measurement["metrics"]["control_orders"], 1)
            self.assertEqual(measurement["metrics"]["treatment_orders"], 1)
            self.assertEqual(measurement["metrics"]["control_revenue"], 1000)
            self.assertEqual(measurement["metrics"]["treatment_revenue"], 2000)
            self.assertEqual(
                measurement["metrics"]["treatment_margin_percent"],
                25,
            )
            self.assertEqual(
                self.db.query(main.ExperimentEvaluation)
                .filter_by(experiment_id=experiment.id)
                .count(),
                0,
            )

            with patch.object(
                main,
                "_request_ai_experiment_evaluation",
                return_value=(
                    json.loads(final_evaluation),
                    True,
                ),
            ) as evaluation_call:
                evaluation_result = main.evaluate_result(
                    main.ResultEvaluationRequest(
                        experiment_id=experiment_result["experiment_id"]
                    )
                )
            evaluation_call.assert_called_once()

        self.assertEqual(gemini_call.call_count, 1)
        self.assertEqual(evaluation_result["experiment_status"], "COMPLETED")
        self.assertEqual(
            json.loads(evaluation_result["evaluation"])["decision"],
            "CONTINUE",
        )
        self.db.expire_all()
        persisted_experiment = (
            self.db.query(main.Experiment)
            .filter_by(id=experiment.id)
            .one()
        )
        persisted_evaluation = (
            self.db.query(main.ExperimentEvaluation)
            .filter_by(experiment_id=experiment.id)
            .one()
        )
        learning_record = (
            self.db.query(main.LearningRecord)
            .filter_by(experiment_id=experiment.id)
            .one()
        )
        self.assertEqual(persisted_experiment.status, "COMPLETED")
        self.assertEqual(persisted_evaluation.decision, "CONTINUE")
        self.assertEqual(
            json.loads(persisted_evaluation.metrics_json)["treatment_revenue"],
            2000,
        )
        self.assertEqual(learning_record.learning_signal, "POSITIVE")
        self.assertEqual(
            self.db.query(main.ExperimentAssignment)
            .filter_by(experiment_id=experiment.id)
            .count(),
            96,
        )


if __name__ == "__main__":
    unittest.main()
