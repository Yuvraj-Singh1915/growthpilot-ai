import hashlib
import hmac
import json
import os
import sys
import unittest
from datetime import datetime
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

    def payment_payload(self, event="payment.captured"):
        status = "captured" if event == "payment.captured" else "failed"
        return {
            "entity": "event",
            "account_id": "acc_test_local",
            "event": event,
            "created_at": 1790500000,
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test_local_1",
                        "entity": "payment",
                        "amount": 12345,
                        "currency": "INR",
                        "status": status,
                        "order_id": "order_test_local_1",
                        "created_at": 1790500000,
                        "notes": {
                            "growthpilot_customer_id": str(self.customer_id)
                        },
                    }
                }
            },
        }

    async def deliver(self, payload, event_id="evt_test_local_1", signature=True):
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
        result = await self.deliver(self.payment_payload())
        self.assertEqual(result["processing_status"], "processed")
        order = self.db.query(main.Order).one()
        payment = self.db.query(main.Payment).one()
        self.assertEqual(order.provider, "razorpay")
        self.assertEqual(order.external_order_id, "order_test_local_1")
        self.assertEqual(payment.external_payment_id, "pay_test_local_1")
        self.assertEqual(payment.amount, 123.45)
        self.assertEqual(payment.status, "succeeded")
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
        payload = self.payment_payload()
        first = await self.deliver(payload)
        second = await self.deliver(payload)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.counts(), (1, 1, 1))

    async def test_duplicate_payment_identifier_does_not_duplicate_payment(self):
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

    async def test_unmapped_payment_is_recorded_without_business_records(self):
        payload = self.payment_payload()
        payload["payload"]["payment"]["entity"]["notes"] = {}
        result = await self.deliver(payload)
        self.assertEqual(result["processing_status"], "unmapped")
        self.assertEqual(self.counts(), (0, 0, 1))

    async def test_invalid_customer_reference_is_not_coerced(self):
        payload = self.payment_payload()
        payload["payload"]["payment"]["entity"]["notes"] = {
            "growthpilot_customer_id": self.customer_id + 0.5
        }
        result = await self.deliver(payload)
        self.assertEqual(result["processing_status"], "unmapped")
        self.assertEqual(self.counts(), (0, 0, 1))


if __name__ == "__main__":
    unittest.main()
