import hashlib
import hmac
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
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


class RazorpayWebhookTests(unittest.IsolatedAsyncioTestCase):
    secret = "local-deterministic-webhook-test-secret"

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
        customer = main.Customer(created_at=datetime(2026, 9, 1))
        self.db.add(customer)
        self.db.commit()
        self.customer_id = customer.id

    def tearDown(self):
        self.db.close()
        main.SessionLocal = self.previous_session_factory
        self.engine.dispose()

    def payment_payload(
        self,
        event="payment.captured",
        account_id="test-account",
        order_id="order_test_local_1",
        payment_id="pay_test_local_1",
        customer_id=None,
        amount_minor=12345,
        created_at=1790500000,
    ):
        status = "captured" if event == "payment.captured" else "failed"
        mapped_customer_id = (
            self.customer_id if customer_id is None else customer_id
        )
        return {
            "entity": "event",
            "account_id": account_id,
            "event": event,
            "created_at": created_at,
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "entity": "payment",
                        "amount": amount_minor,
                        "currency": "INR",
                        "status": status,
                        "order_id": order_id,
                        "created_at": created_at,
                        "notes": {
                            "growthpilot_customer_id": str(mapped_customer_id)
                        },
                    }
                }
            },
        }

    def create_mapped_order(
        self,
        account_id="test-account",
        order_id="order_test_local_1",
        customer_id=None,
        external_order_id=None,
    ):
        with patch.dict(os.environ, {"RAZORPAY_MODE": "test"}):
            result = main.ingest_observed_order(
                main.ObservedOrderRequest(
                    external_order_id=external_order_id
                    or f"internal-{account_id}-{order_id}",
                    customer_id=self.customer_id if customer_id is None else customer_id,
                    amount=123.45,
                    status="pending",
                    currency="INR",
                    provider="razorpay",
                    provider_account=account_id,
                    provider_order_id=order_id,
                )
            )
        return (
            self.db.query(main.Order)
            .filter(main.Order.id == result["order"]["id"])
            .one()
        )

    async def deliver(
        self,
        payload,
        event_id="evt_test_local_1",
        signature=True,
        mode="test",
    ):
        raw_body = json.dumps(payload, separators=(",", ":")).encode()
        headers = [(b"x-razorpay-event-id", event_id.encode())]
        if signature is True:
            digest = hmac.new(
                self.secret.encode(), raw_body, hashlib.sha256
            ).hexdigest()
            headers.append((b"x-razorpay-signature", digest.encode()))
        elif isinstance(signature, str):
            headers.append((b"x-razorpay-signature", signature.encode()))

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
            return {"type": "http.request", "body": raw_body, "more_body": False}

        request = Request(scope, receive)
        with patch.dict(
            os.environ,
            {
                "RAZORPAY_MODE": mode,
                "RAZORPAY_WEBHOOK_SECRET": self.secret,
                "RAZORPAY_ACCOUNT_REFERENCE": "test-account",
            },
        ):
            return await main.ingest_razorpay_webhook(request)

    def counts(self):
        self.db.expire_all()
        return (
            self.db.query(main.Order).count(),
            self.db.query(main.Payment).count(),
            self.db.query(main.WebhookEvent).count(),
        )

    async def test_valid_signature_persists_normalized_payment(self):
        order = self.create_mapped_order()
        result = await self.deliver(self.payment_payload())
        self.assertEqual(result["processing_status"], "processed")
        self.db.refresh(order)
        payment = self.db.query(main.Payment).one()
        self.assertEqual(order.provider, "razorpay")
        self.assertEqual(order.provider_order_id, "order_test_local_1")
        self.assertEqual(order.currency, "INR")
        self.assertEqual(order.amount_minor, 12345)
        self.assertEqual(payment.external_payment_id, "pay_test_local_1")
        self.assertEqual(payment.provider, "razorpay")
        self.assertEqual(payment.provider_account, "test-account")
        self.assertEqual(payment.order_id, order.id)
        self.assertEqual(payment.amount, 123.45)
        self.assertEqual(payment.amount_minor, 12345)
        self.assertEqual(payment.currency, "INR")
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(
            payment.provider_event_at,
            datetime.fromtimestamp(1790500000, timezone.utc).replace(tzinfo=None),
        )
        self.assertIsNotNone(payment.received_at)
        self.assertEqual(order.status, "completed")
        self.assertEqual(self.db.query(main.WebhookEvent).one().payload_hash,
                         hashlib.sha256(
                             json.dumps(
                                 self.payment_payload(),
                                 separators=(",", ":"),
                             ).encode()
                         ).hexdigest())

    async def test_invalid_signature_is_rejected_without_writes(self):
        with self.assertRaises(HTTPException) as raised:
            await self.deliver(self.payment_payload(), signature="0" * 64)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.counts(), (0, 0, 0))

    async def test_missing_signature_is_rejected_without_writes(self):
        with self.assertRaises(HTTPException) as raised:
            await self.deliver(self.payment_payload(), signature=False)
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(self.counts(), (0, 0, 0))

    async def test_duplicate_event_has_no_duplicate_side_effects(self):
        self.create_mapped_order()
        payload = self.payment_payload()
        first = await self.deliver(payload)
        second = await self.deliver(payload)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.counts(), (1, 1, 1))

    async def test_duplicate_payment_identifier_does_not_duplicate_payment(self):
        self.create_mapped_order()
        payload = self.payment_payload()
        first = await self.deliver(payload, event_id="evt_capture_1")
        second = await self.deliver(payload, event_id="evt_capture_2")
        self.assertEqual(first["processing_status"], "processed")
        self.assertEqual(second["processing_status"], "processed")
        self.assertEqual(self.counts(), (1, 1, 2))

    async def test_unknown_event_is_recorded_without_business_records(self):
        payload = {
            "entity": "event",
            "event": "refund.created",
            "created_at": 1790500000,
            "payload": {},
        }
        result = await self.deliver(payload)
        self.assertEqual(result["processing_status"], "ignored")
        self.assertEqual(self.counts(), (0, 0, 1))

    async def test_unsupported_currency_is_recorded_without_business_records(self):
        payload = self.payment_payload()
        payload["payload"]["payment"]["entity"]["currency"] = "USD"
        result = await self.deliver(payload)
        self.assertEqual(
            result["processing_status"],
            "ignored_unsupported_currency",
        )
        self.assertEqual(self.counts(), (0, 0, 1))

    async def test_non_test_mode_is_rejected_without_writes(self):
        with self.assertRaises(HTTPException) as raised:
            await self.deliver(self.payment_payload(), mode="live")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.counts(), (0, 0, 0))

    async def test_provider_order_id_cannot_map_to_two_orders_in_one_account(self):
        self.create_mapped_order()
        with patch.dict(os.environ, {"RAZORPAY_MODE": "test"}):
            with self.assertRaises(HTTPException) as raised:
                main.ingest_observed_order(
                    main.ObservedOrderRequest(
                        external_order_id="another-internal-order",
                        customer_id=self.customer_id,
                        amount=123.45,
                        status="pending",
                        currency="INR",
                        provider="razorpay",
                        provider_account="test-account",
                        provider_order_id="order_test_local_1",
                    )
                )
        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.db.query(main.Order).count(), 1)

    async def test_razorpay_order_mapping_is_rejected_outside_test_mode(self):
        with patch.dict(os.environ, {"RAZORPAY_MODE": "live"}):
            with self.assertRaises(HTTPException) as raised:
                main.ingest_observed_order(
                    main.ObservedOrderRequest(
                        external_order_id="live-mode-order",
                        customer_id=self.customer_id,
                        amount=123.45,
                        status="pending",
                        currency="INR",
                        provider="razorpay",
                        provider_account="test-account",
                        provider_order_id="order_live_mode",
                    )
                )
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.db.query(main.Order).count(), 0)

    async def test_unmapped_payment_is_recorded_without_business_records(self):
        payload = self.payment_payload()
        payload["payload"]["payment"]["entity"]["notes"] = {}
        result = await self.deliver(payload)
        self.assertEqual(result["processing_status"], "unmapped")
        self.assertEqual(self.counts(), (0, 0, 1))

    async def test_invalid_customer_reference_is_not_coerced(self):
        self.create_mapped_order()
        payload = self.payment_payload()
        payload["payload"]["payment"]["entity"]["notes"] = {
            "growthpilot_customer_id": self.customer_id + 0.5
        }
        result = await self.deliver(payload)
        self.assertEqual(result["processing_status"], "unmapped")
        self.assertEqual(self.counts(), (1, 0, 1))

    async def test_failed_payment_maps_without_creating_an_order(self):
        order = self.create_mapped_order()
        result = await self.deliver(
            self.payment_payload(
                event="payment.failed",
                payment_id="pay_test_failed",
            )
        )
        self.db.refresh(order)
        payment = self.db.query(main.Payment).one()
        self.assertEqual(result["processing_status"], "processed")
        self.assertEqual(payment.status, "failed")
        self.assertEqual(payment.amount_minor, 12345)
        self.assertEqual(order.status, "payment_failed")
        self.assertEqual(self.counts(), (1, 1, 1))

    async def test_missing_order_mapping_does_not_create_business_records(self):
        payload = self.payment_payload()
        result = await self.deliver(payload)
        self.assertEqual(result["processing_status"], "unmapped")
        self.assertEqual(self.counts(), (0, 0, 1))

    async def test_payment_without_provider_event_time_is_not_persisted(self):
        self.create_mapped_order()
        payload = self.payment_payload()
        payload.pop("created_at")
        payload["payload"]["payment"]["entity"].pop("created_at")
        result = await self.deliver(payload)
        self.assertEqual(result["processing_status"], "unmapped")
        self.assertEqual(self.counts(), (1, 0, 1))

    async def test_customer_mismatch_does_not_create_a_customer_or_payment(self):
        self.create_mapped_order()
        another_customer = main.Customer(created_at=datetime(2026, 9, 1))
        self.db.add(another_customer)
        self.db.commit()
        result = await self.deliver(
            self.payment_payload(customer_id=another_customer.id)
        )
        self.assertEqual(result["processing_status"], "conflict")
        self.assertEqual(self.db.query(main.Customer).count(), 2)
        self.assertEqual(self.counts(), (1, 0, 1))

    async def test_conflicting_payment_id_does_not_overwrite_financial_data(self):
        self.create_mapped_order()
        await self.deliver(self.payment_payload(), event_id="evt_capture_original")
        result = await self.deliver(
            self.payment_payload(amount_minor=13000),
            event_id="evt_capture_conflicting",
        )
        payment = self.db.query(main.Payment).one()
        self.assertEqual(result["processing_status"], "conflict")
        self.assertEqual(payment.amount_minor, 12345)
        self.assertEqual(payment.amount, 123.45)
        self.assertEqual(self.counts(), (1, 1, 2))

    async def test_failed_to_captured_transition_requires_newer_event(self):
        order = self.create_mapped_order()
        failed = self.payment_payload(
            event="payment.failed",
            created_at=1790500000,
        )
        captured = self.payment_payload(
            event="payment.captured",
            created_at=1790500060,
        )
        first = await self.deliver(failed, event_id="evt_payment_failed")
        second = await self.deliver(captured, event_id="evt_payment_captured")
        self.assertEqual(first["processing_status"], "processed")
        self.assertEqual(second["processing_status"], "processed")

        stale = self.payment_payload(
            event="payment.failed",
            created_at=1790499900,
        )
        third = await self.deliver(stale, event_id="evt_payment_stale")
        duplicate_capture = self.payment_payload(
            event="payment.captured",
            created_at=1790500100,
        )
        duplicate = await self.deliver(
            duplicate_capture,
            event_id="evt_payment_duplicate_capture",
        )
        newer_failure = self.payment_payload(
            event="payment.failed",
            created_at=1790500120,
        )
        fourth = await self.deliver(
            newer_failure,
            event_id="evt_payment_newer_failure",
        )
        payment = self.db.query(main.Payment).one()
        self.db.refresh(order)
        self.assertEqual(third["processing_status"], "stale")
        self.assertEqual(duplicate["processing_status"], "processed")
        self.assertEqual(fourth["processing_status"], "conflict")
        self.assertEqual(payment.status, "succeeded")
        self.assertEqual(
            payment.provider_event_at,
            datetime.fromtimestamp(1790500060, timezone.utc).replace(tzinfo=None),
        )
        self.assertEqual(order.status, "completed")
        self.assertEqual(self.counts(), (1, 1, 5))

    async def test_partial_capture_does_not_mark_order_completed(self):
        order = self.create_mapped_order()
        result = await self.deliver(self.payment_payload(amount_minor=5000))
        self.db.refresh(order)
        self.assertEqual(result["processing_status"], "processed")
        self.assertEqual(order.status, "pending")
        self.assertEqual(self.db.query(main.Payment).one().amount_minor, 5000)

    async def test_identical_provider_ids_are_scoped_by_account(self):
        self.create_mapped_order(account_id="account-one")
        self.create_mapped_order(
            account_id="account-two",
            external_order_id="internal-account-two-order",
        )
        first = await self.deliver(
            self.payment_payload(account_id="account-one"),
            event_id="evt_account_one",
        )
        second = await self.deliver(
            self.payment_payload(account_id="account-two"),
            event_id="evt_account_two",
        )
        self.assertEqual(first["processing_status"], "processed")
        self.assertEqual(second["processing_status"], "processed")
        self.assertEqual(self.counts(), (2, 2, 2))


if __name__ == "__main__":
    unittest.main()
