from fastapi import FastAPI, HTTPException
from openai import OpenAI
import json
import os
from datetime import datetime, timedelta
from uuid import uuid4
from typing import Optional
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sqlalchemy import (
    create_engine,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    inspect,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.exc import IntegrityError
from services.opportunity_engine import (
    CommerceCartEvent,
    CommerceOrder,
    CommercePayment,
    detect_opportunities,
)
from services.experiment_engine import (
    assign_variants,
    build_experiment_fingerprint,
    build_hypothesis,
    canonical_strategy_code,
    get_eligible_customers,
    variant_definition,
)
from services.measurement_engine import calculate_experiment_metrics
from services.learning_engine import build_learning_evidence

DATABASE_URL = os.getenv("GROWTHPILOT_DATABASE_URL", "sqlite:///./growthpilot.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()

class MerchantGoal(Base):
    __tablename__ = "merchant_goals"

    id = Column(Integer, primary_key=True, index=True)
    goal = Column(Float, nullable=False)
    max_discount = Column(Float, nullable=False)
    budget = Column(Float, nullable=False)
    min_margin = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Opportunity(Base):
    __tablename__ = "opportunities"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    priority = Column(String, nullable=False)
    potential_revenue = Column(Float, nullable=False)
    recommended_action = Column(String, nullable=False)
    suggested_discount = Column(Float, nullable=False)
    risk = Column(String, nullable=False)
    score = Column(Float, nullable=False, default=0, server_default="0")
    affected_customers = Column(Integer, nullable=False, default=0, server_default="0")
    affected_orders = Column(Integer, nullable=False, default=0, server_default="0")
    evidence = Column(Text, nullable=False, default="", server_default="''")
    created_at = Column(DateTime, default=datetime.utcnow)


class Customer(Base):
    __tablename__ = "commerce_customers"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Order(Base):
    __tablename__ = "commerce_orders"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("commerce_customers.id"), nullable=False)
    amount = Column(Float, nullable=False)
    status = Column(String, nullable=False)
    margin_percent = Column(Float, nullable=True)
    external_order_id = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class CartEvent(Base):
    __tablename__ = "commerce_cart_events"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("commerce_customers.id"), nullable=False)
    cart_value = Column(Float, nullable=False)
    event_type = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Payment(Base):
    __tablename__ = "commerce_payments"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("commerce_orders.id"), nullable=False)
    status = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Experiment(Base):
    __tablename__ = "experiments"

    id = Column(Integer, primary_key=True, index=True)
    experiment_id = Column(String, nullable=False, unique=True, index=True)
    opportunity_id = Column(Integer, ForeignKey("opportunities.id"), nullable=False)
    goal_id = Column(Integer, ForeignKey("merchant_goals.id"), nullable=True)
    opportunity_title = Column(String, nullable=False)
    status = Column(String, nullable=False, default="RUNNING")
    risk = Column(String, nullable=False)
    recommended_action = Column(Text, nullable=False)
    hypothesis = Column(Text, nullable=False)
    control_variant = Column(Text, nullable=False)
    treatment_variant = Column(Text, nullable=False)
    traffic_percentage = Column(Float, nullable=False, default=90)
    holdout_percentage = Column(Float, nullable=False, default=10)
    budget = Column(Float, nullable=False)
    max_discount = Column(Float, nullable=False)
    suggested_discount = Column(Float, nullable=False)
    original_strategy_action = Column(Text, nullable=True)
    strategy_code = Column(String, nullable=True)
    strategy_fingerprint = Column(String, nullable=True, index=True)
    target_segment = Column(String, nullable=True)
    min_margin = Column(Float, nullable=False)
    target_growth = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ExperimentAssignment(Base):
    __tablename__ = "experiment_assignments"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "customer_id",
            name="uq_experiment_assignment_customer",
        ),
        Index("ix_experiment_assignments_experiment_id", "experiment_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    experiment_id = Column(
        Integer,
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id = Column(
        Integer,
        ForeignKey("commerce_customers.id"),
        nullable=False,
    )
    variant = Column(String, nullable=False)
    assigned_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ExperimentEvaluation(Base):
    __tablename__ = "experiment_evaluations"

    id = Column(Integer, primary_key=True, index=True)
    experiment_id = Column(
        Integer,
        ForeignKey("experiments.id"),
        nullable=False,
        index=True,
    )
    decision = Column(String, nullable=False)
    reason = Column(Text, nullable=False)
    next_action = Column(Text, nullable=False)
    confidence = Column(Float, nullable=True)
    metrics_json = Column(Text, nullable=False)
    evaluated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    original_strategy_action = Column(Text, nullable=True)


class LearningRecord(Base):
    __tablename__ = "learning_records"
    __table_args__ = (
        UniqueConstraint("experiment_id", name="uq_learning_record_experiment"),
        Index("ix_learning_records_opportunity", "opportunity_title"),
    )

    id = Column(Integer, primary_key=True, index=True)
    experiment_id = Column(Integer, ForeignKey("experiments.id"), nullable=False)
    opportunity_title = Column(String, nullable=False)
    experiment_status = Column(String, nullable=False)
    hypothesis = Column(Text, nullable=False)
    evaluation_decision = Column(String, nullable=False)
    evaluation_reason = Column(Text, nullable=False)
    next_action = Column(Text, nullable=False)
    recommended_action_code = Column(String, nullable=False)
    learning_signal = Column(String, nullable=False)
    signal_reason = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False)
    confidence_basis = Column(Text, nullable=False)
    metrics_json = Column(Text, nullable=False)
    evaluated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    original_strategy_action = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    experiment = relationship("Experiment")


Base.metadata.create_all(bind=engine)


def migrate_opportunity_columns():
    existing_columns = {
        column["name"] for column in inspect(engine).get_columns("opportunities")
    }
    migrations = {
        "score": "FLOAT NOT NULL DEFAULT 0",
        "affected_customers": "INTEGER NOT NULL DEFAULT 0",
        "affected_orders": "INTEGER NOT NULL DEFAULT 0",
        "evidence": "TEXT NOT NULL DEFAULT ''",
    }

    with engine.begin() as connection:
        for column_name, column_definition in migrations.items():
            if column_name not in existing_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE opportunities ADD COLUMN "
                    f"{column_name} {column_definition}"
                )
        order_columns = {
            column["name"]
            for column in inspect(connection).get_columns("commerce_orders")
        }
        if "margin_percent" not in order_columns:
            connection.exec_driver_sql(
                "ALTER TABLE commerce_orders ADD COLUMN margin_percent FLOAT"
            )
        experiment_columns = {
            column["name"]
            for column in inspect(connection).get_columns("experiments")
        }
        if "recommended_action" not in experiment_columns:
            connection.exec_driver_sql(
                "ALTER TABLE experiments ADD COLUMN "
                "recommended_action TEXT NOT NULL DEFAULT ''"
            )
            connection.exec_driver_sql(
                "UPDATE experiments SET recommended_action = treatment_variant "
                "WHERE recommended_action = ''"
            )


migrate_opportunity_columns()


def migrate_learning_loop_columns():
    additive_columns = {
        "commerce_orders": {"external_order_id": "TEXT"},
        "experiments": {
            "original_strategy_action": "TEXT",
            "strategy_code": "VARCHAR",
            "strategy_fingerprint": "VARCHAR",
            "target_segment": "VARCHAR",
        },
        "experiment_evaluations": {"original_strategy_action": "TEXT"},
        "learning_records": {
            "original_strategy_action": "TEXT",
            "created_at": "DATETIME",
        },
    }
    with engine.begin() as connection:
        for table_name, columns in additive_columns.items():
            existing = {
                column["name"]
                for column in inspect(connection).get_columns(table_name)
            }
            for column_name, column_definition in columns.items():
                if column_name not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} ADD COLUMN "
                        f"{column_name} {column_definition}"
                    )
        connection.exec_driver_sql(
            "UPDATE experiments SET original_strategy_action = recommended_action "
            "WHERE original_strategy_action IS NULL AND recommended_action != ''"
        )
        connection.exec_driver_sql(
            "UPDATE learning_records SET created_at = evaluated_at "
            "WHERE created_at IS NULL"
        )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "uq_commerce_orders_external_order_id "
            "ON commerce_orders (external_order_id) "
            "WHERE external_order_id IS NOT NULL"
        )


migrate_learning_loop_columns()

load_dotenv()
client = OpenAI(
    api_key=os.getenv("GEMINI_API_KEY"),
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)
app = FastAPI(title="GrowthPilot AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class GrowthGoal(BaseModel):
    goal: float
    max_discount: float
    budget: float
    min_margin: float


@app.get("/")
def root():
    return {
        "message": "GrowthPilot AI backend is running 🚀"
    }


@app.post("/api/goal")
def create_goal(data: GrowthGoal):
    db = SessionLocal()

    new_goal = MerchantGoal(
        goal=data.goal,
        max_discount=data.max_discount,
        budget=data.budget,
        min_margin=data.min_margin,
    )

    db.add(new_goal)
    db.commit()
    db.refresh(new_goal)
    db.close()

    return {
        "status": "success",
        "message": "Growth goal saved to database",
        "goal_id": new_goal.id,
        "goal": new_goal.goal,
        "max_discount": new_goal.max_discount,
        "budget": new_goal.budget,
        "min_margin": new_goal.min_margin,
    }


class OpportunityRequest(BaseModel):
    goal: float
    max_discount: float
    budget: float
    min_margin: float


def seed_demo_commerce_data(db):
    existing_counts = {
        "customers": db.query(Customer).count(),
        "orders": db.query(Order).count(),
        "carts": db.query(CartEvent).count(),
        "payments": db.query(Payment).count(),
    }
    if any(existing_counts.values()):
        return {
            "status": "success",
            "customers_created": 0,
            "orders_created": 0,
            "carts_created": 0,
            "payments_created": 0,
            "message": "Commerce data already exists; no additional demo records were created.",
        }

    now = datetime.utcnow()
    customers = [
        Customer(created_at=now - timedelta(days=365 - index))
        for index in range(60)
    ]
    db.add_all(customers)
    db.flush()

    completed_orders = []
    for index, customer in enumerate(customers):
        if index < 20:
            completed_orders.append(
                Order(
                    customer_id=customer.id,
                    amount=1000 + (index % 5) * 250,
                    status="completed",
                    margin_percent=22 + index % 9,
                    created_at=now - timedelta(days=120 + index % 20),
                )
            )
        elif index < 35:
            completed_orders.extend(
                [
                    Order(
                        customer_id=customer.id,
                        amount=1200 + (index % 6) * 180,
                        status="completed",
                        margin_percent=22 + index % 9,
                        created_at=now - timedelta(days=150),
                    ),
                    Order(
                        customer_id=customer.id,
                        amount=1400 + (index % 6) * 200,
                        status="completed",
                        margin_percent=22 + index % 9,
                        created_at=now - timedelta(days=15),
                    ),
                ]
            )
        elif index < 40:
            completed_orders.extend(
                [
                    Order(
                        customer_id=customer.id,
                        amount=2200 + (index % 3) * 300,
                        status="completed",
                        margin_percent=22 + index % 9,
                        created_at=now - timedelta(days=220),
                    ),
                    Order(
                        customer_id=customer.id,
                        amount=2600 + (index % 3) * 300,
                        status="completed",
                        margin_percent=22 + index % 9,
                        created_at=now - timedelta(days=140),
                    ),
                    Order(
                        customer_id=customer.id,
                        amount=3500 + (index % 3) * 300,
                        status="completed",
                        margin_percent=22 + index % 9,
                        created_at=now - timedelta(days=120),
                    ),
                    Order(
                        customer_id=customer.id,
                        amount=3200 + (index % 2) * 500,
                        status="completed",
                        margin_percent=22 + index % 9,
                        created_at=now - timedelta(days=110),
                    ),
                ]
            )
        elif index < 48:
            completed_orders.extend(
                [
                    Order(
                        customer_id=customer.id,
                        amount=4000 + (index % 3) * 300,
                        status="completed",
                        margin_percent=22 + index % 9,
                        created_at=now - timedelta(days=300),
                    ),
                    Order(
                        customer_id=customer.id,
                        amount=4200 + (index % 3) * 300,
                        status="completed",
                        margin_percent=22 + index % 9,
                        created_at=now - timedelta(days=220),
                    ),
                    Order(
                        customer_id=customer.id,
                        amount=4500 + (index % 3) * 300,
                        status="completed",
                        margin_percent=22 + index % 9,
                        created_at=now - timedelta(days=120),
                    ),
                ]
            )

    db.add_all(completed_orders)
    db.flush()

    cart_events = []
    for index, customer in enumerate(customers):
        if index < 20 or index >= 48:
            for cart_number, days_ago in enumerate((10, 20)):
                cart_events.append(
                    CartEvent(
                        customer_id=customer.id,
                        cart_value=900 + ((index * 3 + cart_number * 2) % 9) * 350,
                        event_type="checkout_started",
                        created_at=now - timedelta(days=days_ago),
                    )
                )
        elif index < 35:
            cart_events.append(
                CartEvent(
                    customer_id=customer.id,
                    cart_value=1200 + (index % 5) * 400,
                    event_type="checkout_started",
                    created_at=now - timedelta(days=45),
                )
            )
    db.add_all(cart_events)

    payments = [
        Payment(
            order_id=order.id,
            status="succeeded",
            amount=order.amount,
            created_at=order.created_at,
        )
        for order in completed_orders
    ]
    failed_orders = []
    for index in range(42, 60):
        customer = customers[index]
        order = Order(
            customer_id=customer.id,
            amount=850 + (index % 7) * 275,
            status="payment_failed",
            created_at=now - timedelta(days=2 + index % 5),
        )
        failed_orders.append(order)
    db.add_all(failed_orders)
    db.flush()
    payments.extend(
        Payment(
            order_id=order.id,
            status="failed",
            amount=order.amount,
            created_at=order.created_at,
        )
        for order in failed_orders
    )
    db.add_all(payments)
    db.commit()

    return {
        "status": "success",
        "customers_created": len(customers),
        "orders_created": len(completed_orders) + len(failed_orders),
        "carts_created": len(cart_events),
        "payments_created": len(payments),
        "message": "Deterministic local commerce demo data was created.",
    }


@app.post("/api/demo/seed")
def seed_demo_data():
    db = SessionLocal()
    try:
        return seed_demo_commerce_data(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class ObservedOrderRequest(BaseModel):
    external_order_id: str
    customer_id: int
    amount: float
    status: str = "completed"
    margin_percent: Optional[float] = None
    payment_status: Optional[str] = None
    payment_amount: Optional[float] = None


def _serialize_observed_order(order, payment=None, duplicate=False):
    return {
        "status": "success",
        "duplicate": duplicate,
        "order": {
            "id": order.id,
            "external_order_id": order.external_order_id,
            "customer_id": order.customer_id,
            "amount": order.amount,
            "status": order.status,
            "margin_percent": order.margin_percent,
            "created_at": order.created_at,
        },
        "payment": (
            {
                "id": payment.id,
                "status": payment.status,
                "amount": payment.amount,
                "created_at": payment.created_at,
            }
            if payment is not None
            else None
        ),
    }


def _observed_order_matches_request(order, payment, data, order_status, payment_status):
    payment_amount = (
        data.payment_amount
        if data.payment_amount is not None
        else data.amount
        if payment_status is not None
        else None
    )
    return (
        order.customer_id == data.customer_id
        and order.amount == data.amount
        and order.status == order_status
        and order.margin_percent == data.margin_percent
        and (
            payment is None
            and payment_status is None
            or payment is not None
            and payment_status == payment.status
            and payment_amount == payment.amount
        )
    )


@app.get("/api/customers")
def list_customers():
    db = SessionLocal()
    try:
        customers = db.query(Customer).order_by(Customer.id).all()
        return {
            "status": "success",
            "count": len(customers),
            "customers": [
                {"id": customer.id, "created_at": customer.created_at}
                for customer in customers
            ],
        }
    finally:
        db.close()


@app.post("/api/orders")
def ingest_observed_order(data: ObservedOrderRequest):
    external_order_id = data.external_order_id.strip()
    order_status = data.status.strip().lower()
    payment_status = data.payment_status.strip().lower() if data.payment_status else None
    if not external_order_id:
        raise HTTPException(status_code=422, detail="external_order_id is required.")
    if data.amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be greater than zero.")
    if order_status not in {"completed", "pending", "payment_failed", "cancelled"}:
        raise HTTPException(status_code=422, detail="Unsupported order status.")
    if data.margin_percent is not None and not -100 <= data.margin_percent <= 100:
        raise HTTPException(status_code=422, detail="margin_percent must be between -100 and 100.")
    if payment_status is not None and payment_status not in {
        "succeeded", "successful", "paid", "captured", "failed", "declined", "pending", "refunded"
    }:
        raise HTTPException(status_code=422, detail="Unsupported payment status.")
    if data.payment_amount is not None and payment_status is None:
        raise HTTPException(status_code=422, detail="payment_status is required with payment_amount.")
    if data.payment_amount is not None and data.payment_amount <= 0:
        raise HTTPException(status_code=422, detail="payment_amount must be greater than zero.")

    db = SessionLocal()
    try:
        customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
        if customer is None:
            raise HTTPException(status_code=404, detail="Customer not found.")

        existing = (
            db.query(Order)
            .filter(Order.external_order_id == external_order_id)
            .first()
        )
        if existing is not None:
            payment = (
                db.query(Payment)
                .filter(Payment.order_id == existing.id)
                .order_by(Payment.id)
                .first()
            )
            if not _observed_order_matches_request(
                existing, payment, data, order_status, payment_status
            ):
                raise HTTPException(
                    status_code=409,
                    detail="external_order_id already exists with different order or payment data.",
                )
            return _serialize_observed_order(existing, payment, duplicate=True)

        observed_at = datetime.utcnow()
        order = Order(
            customer_id=data.customer_id,
            amount=data.amount,
            status=order_status,
            margin_percent=data.margin_percent,
            external_order_id=external_order_id,
            created_at=observed_at,
        )
        db.add(order)
        db.flush()
        payment = None
        if payment_status is not None:
            payment = Payment(
                order_id=order.id,
                status=payment_status,
                amount=(
                    data.payment_amount
                    if data.payment_amount is not None
                    else data.amount
                ),
                created_at=observed_at,
            )
            db.add(payment)
        db.commit()
        db.refresh(order)
        if payment is not None:
            db.refresh(payment)
        return _serialize_observed_order(order, payment)
    except IntegrityError as error:
        db.rollback()
        existing = (
            db.query(Order)
            .filter(Order.external_order_id == external_order_id)
            .first()
        )
        if existing is None:
            raise error
        payment = (
            db.query(Payment)
            .filter(Payment.order_id == existing.id)
            .order_by(Payment.id)
            .first()
        )
        if not _observed_order_matches_request(
            existing, payment, data, order_status, payment_status
        ):
            raise HTTPException(
                status_code=409,
                detail="external_order_id already exists with different order or payment data.",
            ) from error
        return _serialize_observed_order(existing, payment, duplicate=True)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/api/orders")
def list_orders():
    db = SessionLocal()
    try:
        orders = db.query(Order).order_by(Order.id).all()
        return {
            "status": "success",
            "count": len(orders),
            "orders": [
                {
                    "id": order.id,
                    "external_order_id": order.external_order_id,
                    "customer_id": order.customer_id,
                    "amount": order.amount,
                    "status": order.status,
                    "margin_percent": order.margin_percent,
                    "created_at": order.created_at,
                }
                for order in orders
            ],
        }
    finally:
        db.close()


class ExperimentRequest(BaseModel):
    opportunity: str
    decision: str
    recommended_action: str
    risk: str
    budget: Optional[float] = None
    max_discount: Optional[float] = None
    min_margin: Optional[float] = None
    target_growth: Optional[float] = None
    strategy_code: Optional[str] = None
    target_segment: Optional[str] = None


@app.post("/api/experiment")
def create_experiment(data: ExperimentRequest):

    # Safety guardrail:
    # Only approved AI decisions can be executed.
    if data.decision != "APPROVE":
        return {
            "status": "blocked",
            "message": "Experiment cannot be launched without AI approval.",
            "decision": data.decision,
            "approval_required": True,
        }

    db = SessionLocal()
    try:
        goal = db.query(MerchantGoal).order_by(MerchantGoal.id.desc()).first()
        opportunity = (
            db.query(Opportunity)
            .filter(Opportunity.title == data.opportunity)
            .order_by(Opportunity.id.desc())
            .first()
        )
        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail="Opportunity was not found. Refresh detected opportunities before creating an experiment.",
            )

        budget = goal.budget if goal is not None else (
            data.budget if data.budget is not None else 50000
        )
        max_discount = goal.max_discount if goal is not None else (
            data.max_discount if data.max_discount is not None else 10
        )
        min_margin = goal.min_margin if goal is not None else (
            data.min_margin if data.min_margin is not None else 20
        )
        target_growth = goal.goal if goal is not None else (
            data.target_growth if data.target_growth is not None else 15
        )
        if goal is not None:
            if data.budget is not None:
                budget = min(budget, data.budget)
            if data.max_discount is not None:
                max_discount = min(max_discount, data.max_discount)
            if data.min_margin is not None:
                min_margin = max(min_margin, data.min_margin)
            if data.target_growth is not None:
                target_growth = data.target_growth

        if min_margin < 15:
            return {
                "status": "blocked",
                "message": "Experiment cannot be launched below the minimum safe margin threshold.",
                "decision": "REJECT",
                "approval_required": False,
            }
        if opportunity.suggested_discount > max_discount:
            return {
                "status": "blocked",
                "message": "Experiment discount exceeds the merchant's configured maximum.",
                "decision": "REJECT",
                "approval_required": False,
            }

        strategy_code = canonical_strategy_code(
            opportunity.title,
            data.recommended_action,
            data.strategy_code,
        )
        strategy_fingerprint = build_experiment_fingerprint(
            opportunity_id=opportunity.id,
            opportunity_title=opportunity.title,
            strategy_code=strategy_code,
            risk=data.risk,
            budget=budget,
            max_discount=max_discount,
            suggested_discount=opportunity.suggested_discount,
            min_margin=min_margin,
            target_growth=target_growth,
            target_segment=data.target_segment,
        )

        active_experiments = (
            db.query(Experiment)
            .filter(
                Experiment.opportunity_id == opportunity.id,
                Experiment.status == "RUNNING",
            )
            .order_by(Experiment.id.desc())
            .all()
        )
        active_experiment = None
        for candidate in active_experiments:
            candidate_action = (
                candidate.original_strategy_action or candidate.recommended_action
            )
            candidate_code = candidate.strategy_code or canonical_strategy_code(
                candidate.opportunity_title,
                candidate_action,
            )
            candidate_assignments = (
                db.query(ExperimentAssignment)
                .filter(ExperimentAssignment.experiment_id == candidate.id)
                .all()
            )
            candidate_assignment_count = len(candidate_assignments)
            candidate_control_percentage = (
                sum(item.variant == "CONTROL" for item in candidate_assignments)
                / candidate_assignment_count
                * 100
                if candidate_assignment_count
                else 0
            )
            candidate_treatment_percentage = (
                sum(item.variant == "TREATMENT" for item in candidate_assignments)
                / candidate_assignment_count
                * 100
                if candidate_assignment_count
                else 0
            )
            candidate_fingerprint = candidate.strategy_fingerprint or (
                build_experiment_fingerprint(
                    opportunity_id=candidate.opportunity_id,
                    opportunity_title=candidate.opportunity_title,
                    strategy_code=candidate_code,
                    risk=candidate.risk,
                    budget=candidate.budget,
                    max_discount=candidate.max_discount,
                    suggested_discount=candidate.suggested_discount,
                    min_margin=candidate.min_margin,
                    target_growth=candidate.target_growth,
                    target_segment=candidate.target_segment,
                    control_percentage=candidate_control_percentage,
                    treatment_percentage=candidate_treatment_percentage,
                )
            )
            if candidate_fingerprint == strategy_fingerprint:
                active_experiment = candidate
                if candidate.strategy_fingerprint is None:
                    candidate.strategy_fingerprint = candidate_fingerprint
                    candidate.strategy_code = candidate_code
                    candidate.original_strategy_action = candidate_action
                    db.commit()
                break

        control_variant, treatment_variant = variant_definition(
            opportunity.title,
            data.recommended_action,
            opportunity.suggested_discount,
        )
        if active_experiment is not None:
            assignments = (
                db.query(ExperimentAssignment)
                .filter(ExperimentAssignment.experiment_id == active_experiment.id)
                .all()
            )
            result = _serialize_experiment(
                active_experiment,
                assignments,
                reused=True,
            )
            result["measurement_status"] = _get_experiment_measurement(
                db,
                active_experiment,
            )["metrics"]["sample_status"]
            return result

        customers = db.query(Customer).all()
        orders = db.query(Order).all()
        carts = db.query(CartEvent).all()
        payment_rows = (
            db.query(Payment, Order.customer_id)
            .join(Order, Payment.order_id == Order.id)
            .all()
        )
        eligible_customer_ids = get_eligible_customers(
            opportunity_title=opportunity.title,
            customer_ids=[customer.id for customer in customers],
            orders=[
                CommerceOrder(
                    customer_id=order.customer_id,
                    amount=order.amount,
                    status=order.status,
                    created_at=order.created_at,
                )
                for order in orders
            ],
            cart_events=[
                CommerceCartEvent(
                    customer_id=cart.customer_id,
                    cart_value=cart.cart_value,
                    event_type=cart.event_type,
                    created_at=cart.created_at,
                )
                for cart in carts
            ],
            payments=[
                CommercePayment(
                    order_id=payment.order_id,
                    customer_id=customer_id,
                    amount=payment.amount,
                    status=payment.status,
                )
                for payment, customer_id in payment_rows
            ],
        )
        if len(eligible_customer_ids) < 10:
            raise HTTPException(
                status_code=409,
                detail="At least 10 eligible customers are required to create a 90/10 experiment.",
            )

        now = datetime.utcnow()
        experiment_public_id = (
            f"EXP-{now.strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8].upper()}"
        )
        hypothesis = build_hypothesis(
            opportunity.title,
            opportunity.suggested_discount,
        )
        experiment = Experiment(
            experiment_id=experiment_public_id,
            opportunity_id=opportunity.id,
            goal_id=goal.id if goal is not None else None,
            opportunity_title=opportunity.title,
            status="RUNNING",
            risk=data.risk,
            recommended_action=data.recommended_action,
            original_strategy_action=data.recommended_action,
            strategy_code=strategy_code,
            strategy_fingerprint=strategy_fingerprint,
            target_segment=data.target_segment,
            hypothesis=hypothesis,
            control_variant=control_variant,
            treatment_variant=treatment_variant,
            traffic_percentage=90,
            holdout_percentage=10,
            budget=budget,
            max_discount=max_discount,
            suggested_discount=opportunity.suggested_discount,
            min_margin=min_margin,
            target_growth=target_growth,
            created_at=now,
            started_at=now,
            updated_at=now,
        )
        db.add(experiment)
        db.flush()

        assignments = [
            ExperimentAssignment(
                experiment_id=experiment.id,
                customer_id=customer_id,
                variant=variant,
                assigned_at=now,
            )
            for customer_id, variant in assign_variants(
                experiment_public_id,
                eligible_customer_ids,
            )
        ]
        db.add_all(assignments)
        db.commit()
        db.refresh(experiment)
        result = _serialize_experiment(experiment, assignments)
        result["measurement_status"] = _get_experiment_measurement(
            db,
            experiment,
        )["metrics"]["sample_status"]
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _serialize_experiment(experiment, assignments, reused=False):
    control_count = sum(item.variant == "CONTROL" for item in assignments)
    treatment_count = sum(item.variant == "TREATMENT" for item in assignments)
    assigned_count = control_count + treatment_count
    control_share = (
        round(control_count / assigned_count * 100, 2) if assigned_count else None
    )
    treatment_share = (
        round(treatment_count / assigned_count * 100, 2) if assigned_count else None
    )
    return {
        "status": "launched",
        "experiment_id": experiment.experiment_id,
        "experiment_status": experiment.status,
        "opportunity": experiment.opportunity_title,
        "action": experiment.recommended_action,
        "original_strategy_action": (
            experiment.original_strategy_action or experiment.recommended_action
        ),
        "strategy_code": experiment.strategy_code,
        "risk": experiment.risk,
        "test_group": f"{treatment_share:g}%" if treatment_share is not None else None,
        "holdout_group": f"{control_share:g}%" if control_share is not None else None,
        "measurement": [
            "Incremental revenue",
            "Conversion rate",
            "Profit margin",
        ],
        "hypothesis": experiment.hypothesis,
        "variants": {
            "control": {
                "traffic": control_share,
                "definition": experiment.control_variant,
            },
            "treatment": {
                "traffic": treatment_share,
                "definition": experiment.treatment_variant,
            },
        },
        "assigned_customers": {
            "control": control_count,
            "treatment": treatment_count,
            "total": control_count + treatment_count,
        },
        "budget": experiment.budget,
        "max_discount": experiment.max_discount,
        "suggested_discount": experiment.suggested_discount,
        "min_margin": experiment.min_margin,
        "goal_id": experiment.goal_id,
        "created_at": experiment.created_at.isoformat(),
        "started_at": experiment.started_at.isoformat(),
        "updated_at": experiment.updated_at.isoformat(),
        "message": (
            "Existing running experiment reused."
            if reused
            else "Experiment launched successfully."
        ),
        "reused": reused,
    }


def _find_experiment_or_404(db, experiment_public_id: str):
    experiment = (
        db.query(Experiment)
        .filter(Experiment.experiment_id == experiment_public_id)
        .first()
    )
    if experiment is None:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    return experiment


def _get_experiment_measurement(db, experiment):
    assignments = (
        db.query(ExperimentAssignment)
        .filter(ExperimentAssignment.experiment_id == experiment.id)
        .all()
    )
    orders = db.query(Order).all()
    payments = db.query(Payment).all()
    return calculate_experiment_metrics(
        experiment,
        assignments,
        orders,
        payments=payments,
    )


@app.get("/api/experiment/{experiment_id}")
def get_experiment(experiment_id: str):
    db = SessionLocal()
    try:
        experiment = _find_experiment_or_404(db, experiment_id)
        assignments = (
            db.query(ExperimentAssignment)
            .filter(ExperimentAssignment.experiment_id == experiment.id)
            .all()
        )
        result = _serialize_experiment(experiment, assignments)
        result["status"] = experiment.status
        result["experiment_status"] = experiment.status
        result["experiment_id"] = experiment.experiment_id
        result["measurement_status"] = _get_experiment_measurement(
            db,
            experiment,
        )["metrics"]["sample_status"]
        result["completed_at"] = (
            experiment.completed_at.isoformat()
            if experiment.completed_at
            else None
        )
        return result
    finally:
        db.close()


def _set_experiment_status(experiment_id: str, status: str):
    db = SessionLocal()
    try:
        experiment = _find_experiment_or_404(db, experiment_id)
        if experiment.status != "RUNNING":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot change experiment from {experiment.status} to {status}.",
            )
        experiment.status = status
        experiment.updated_at = datetime.utcnow()
        if status in {"COMPLETED", "STOPPED"}:
            experiment.completed_at = experiment.updated_at
        db.commit()
        assignments = (
            db.query(ExperimentAssignment)
            .filter(ExperimentAssignment.experiment_id == experiment.id)
            .all()
        )
        result = _serialize_experiment(experiment, assignments)
        result["status"] = experiment.status
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/api/experiment/{experiment_id}/pause")
def pause_experiment(experiment_id: str):
    return _set_experiment_status(experiment_id, "PAUSED")


@app.post("/api/experiment/{experiment_id}/complete")
def complete_experiment(experiment_id: str):
    return _set_experiment_status(experiment_id, "COMPLETED")


@app.post("/api/experiment/{experiment_id}/stop")
def stop_experiment(experiment_id: str):
    return _set_experiment_status(experiment_id, "STOPPED")

class MeasurementRequest(BaseModel):
    experiment_id: str


@app.post("/api/measurement")
def calculate_measurement(data: MeasurementRequest):
    db = SessionLocal()
    try:
        experiment = (
            db.query(Experiment)
            .filter(Experiment.experiment_id == data.experiment_id)
            .first()
        )
        if experiment is not None:
            return _get_experiment_measurement(db, experiment)
        raise HTTPException(status_code=404, detail="Experiment not found.")
    finally:
        db.close()

class ResultEvaluationRequest(BaseModel):
    experiment_id: str


def _serialize_learning_record(record):
    return {
        "experiment_id": record.experiment.experiment_id,
        "opportunity": record.opportunity_title,
        "original_strategy_action": record.original_strategy_action,
        "experiment_status": record.experiment_status,
        "hypothesis": record.hypothesis,
        "decision": record.evaluation_decision,
        "reason": record.evaluation_reason,
        "next_action": record.next_action,
        "recommended_action_code": record.recommended_action_code,
        "learning_signal": record.learning_signal,
        "signal_reason": record.signal_reason,
        "confidence": record.confidence,
        "confidence_basis": record.confidence_basis,
        "metrics": json.loads(record.metrics_json),
        "evaluated_at": record.evaluated_at.isoformat(),
        "created_at": record.created_at.isoformat(),
    }


def _get_opportunity_learning(db, opportunity_title: str, limit: int = 5):
    query = (
        db.query(LearningRecord)
        .filter(
            func.lower(LearningRecord.opportunity_title)
            == opportunity_title.strip().lower()
        )
        .order_by(LearningRecord.evaluated_at.desc(), LearningRecord.id.desc())
    )
    total = query.count()
    records = query.limit(limit).all()
    return total, [_serialize_learning_record(record) for record in records]


def _learning_evaluation_response(db, record):
    return {
        "status": "success",
        "experiment_id": record.experiment.experiment_id,
        "experiment_status": record.experiment.status,
        "evaluation": json.dumps(
            {
                "decision": record.evaluation_decision,
                "reason": record.evaluation_reason,
                "next_action": record.next_action,
            },
            separators=(",", ":"),
        ),
    }


@app.post("/api/evaluate-result")
def evaluate_result(data: ResultEvaluationRequest):
    db = SessionLocal()
    try:
        experiment = (
            db.query(Experiment)
            .filter(Experiment.experiment_id == data.experiment_id)
            .first()
        )
        if experiment is None:
            raise HTTPException(status_code=404, detail="Experiment not found.")

        measurement = _get_experiment_measurement(db, experiment)
        metrics = measurement["metrics"]
        metric_snapshot = {
            **metrics,
            "incremental_revenue": measurement["incremental_revenue"],
            "revenue_growth_percent": measurement["revenue_growth_percent"],
            "conversion_lift": measurement["conversion_lift"],
            "margin_change": measurement["margin_change"],
        }
        existing_learning = (
            db.query(LearningRecord)
            .filter(LearningRecord.experiment_id == experiment.id)
            .first()
        )
        if existing_learning is not None:
            return _learning_evaluation_response(db, existing_learning)

        observed_margin = metrics["treatment_margin_percent"]
        evaluation_is_final = False
        if metrics["sample_status"] == "insufficient_observation_window":
            evaluation = {
                "decision": "CONTINUE",
                "reason": (
                    "The minimum 7-day observation window is not complete; "
                    "no final experiment evaluation or learning will be recorded."
                ),
                "next_action": "Continue observing assigned customers until the 7-day window is complete.",
            }
        elif metrics["sample_status"] == "insufficient_sample":
            evaluation = {
                "decision": "CONTINUE",
                "reason": (
                    f"Insufficient assigned sample for reliable evaluation: "
                    f"{metrics['control_customers']} control and "
                    f"{metrics['treatment_customers']} treatment customers; "
                    f"{'; '.join(metrics['sample_issues'])}."
                ),
                "next_action": "Keep the experiment controlled and collect more observed commerce activity before deciding.",
            }
        elif observed_margin is not None and observed_margin < experiment.min_margin:
            evaluation = {
                "decision": "STOP",
                "reason": "Measured treatment margin is below the merchant's minimum allowed margin.",
                "next_action": "Stop the experiment and protect merchant profitability.",
            }
            evaluation_is_final = True
            if experiment.status != "PAUSED":
                experiment.status = "STOPPED"
                experiment.completed_at = datetime.utcnow()
                experiment.updated_at = experiment.completed_at
        elif observed_margin is None:
            evaluation = {
                "decision": "CONTINUE",
                "reason": "Treatment margin could not be calculated from the available commerce records.",
                "next_action": "Keep the experiment controlled and collect order margin data before evaluating profitability.",
            }
        elif experiment.status == "PAUSED":
            evaluation = {
                "decision": "CONTINUE",
                "reason": "The experiment is paused; paused experiments are not finalized into learning.",
                "next_action": "Resume or complete the experiment before recording a learning outcome.",
            }
        else:
            evaluation, evaluation_is_final = _request_ai_experiment_evaluation(
                experiment, metrics
            )

        if evaluation_is_final and experiment.status != "PAUSED":
            if evaluation["decision"] == "STOP":
                experiment.status = "STOPPED"
            elif experiment.status == "RUNNING":
                experiment.status = "COMPLETED"
            experiment.completed_at = experiment.completed_at or datetime.utcnow()
            experiment.updated_at = datetime.utcnow()

        previous_evaluation = (
            db.query(ExperimentEvaluation)
            .filter(ExperimentEvaluation.experiment_id == experiment.id)
            .order_by(ExperimentEvaluation.id.desc())
            .first()
        )
        if previous_evaluation is None:
            previous_evaluation = ExperimentEvaluation(experiment_id=experiment.id)
            db.add(previous_evaluation)
        previous_evaluation.decision = evaluation["decision"]
        previous_evaluation.reason = evaluation["reason"]
        previous_evaluation.next_action = evaluation["next_action"]
        previous_evaluation.original_strategy_action = (
            experiment.original_strategy_action or experiment.recommended_action
        )
        previous_evaluation.metrics_json = json.dumps(
            metric_snapshot, separators=(",", ":")
        )
        previous_evaluation.evaluated_at = datetime.utcnow()

        learning_evidence = (
            build_learning_evidence(
                evaluation["decision"],
                evaluation["reason"],
                evaluation["next_action"],
                metrics,
            )
            if evaluation_is_final and experiment.status in {"COMPLETED", "STOPPED"}
            else None
        )
        if learning_evidence is not None:
            db.add(
                LearningRecord(
                    experiment_id=experiment.id,
                    opportunity_title=experiment.opportunity_title,
                    experiment_status=experiment.status,
                    hypothesis=experiment.hypothesis,
                    original_strategy_action=(
                        experiment.original_strategy_action
                        or experiment.recommended_action
                    ),
                    evaluation_decision=evaluation["decision"],
                    evaluation_reason=evaluation["reason"],
                    next_action=evaluation["next_action"],
                    recommended_action_code=learning_evidence[
                        "recommended_action_code"
                    ],
                    learning_signal=learning_evidence["learning_signal"],
                    signal_reason=learning_evidence["signal_reason"],
                    confidence=learning_evidence["confidence"],
                    confidence_basis=learning_evidence["confidence_basis"],
                    metrics_json=json.dumps(metric_snapshot, separators=(",", ":")),
                )
            )
        db.commit()
        return {
            "status": "success",
            "experiment_id": experiment.experiment_id,
            "experiment_status": experiment.status,
            "evaluation": json.dumps(evaluation, separators=(",", ":")),
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/api/learning")
def list_learning():
    db = SessionLocal()
    try:
        all_records = (
            db.query(LearningRecord)
            .order_by(LearningRecord.evaluated_at.desc(), LearningRecord.id.desc())
            .limit(100)
            .all()
        )
        return {
            "status": "success",
            "total": db.query(LearningRecord).count(),
            "records": [_serialize_learning_record(record) for record in all_records],
        }
    finally:
        db.close()


@app.get("/api/learning/{opportunity:path}")
def get_opportunity_learning(opportunity: str):
    db = SessionLocal()
    try:
        total, records = _get_opportunity_learning(db, opportunity, limit=100)
        return {
            "status": "success",
            "opportunity": opportunity,
            "total": total,
            "records": records,
        }
    finally:
        db.close()


def _request_ai_experiment_evaluation(experiment, metrics):
    prompt = f"""
    You are an AI Growth Optimization Agent for an e-commerce merchant.

    Evaluate observed results for experiment {experiment.experiment_id}.
    Opportunity: {experiment.opportunity_title}
    Hypothesis: {experiment.hypothesis}
    Merchant target growth: {experiment.target_growth}%
    Merchant minimum margin: {experiment.min_margin}%
    Observed revenue lift vs control: {metrics['revenue_growth_percent']}%
    Observed conversion lift in percentage points: {metrics['conversion_lift']}
    Observed margin impact vs control: {metrics['margin_change']}
    Treatment margin: {metrics['treatment_margin_percent']}
    Assigned sample: {metrics['eligible_customers']} customers
    Control: {metrics['control_customers']} assigned, {metrics['control_conversions']} converted,
    {metrics['control_orders']} orders, revenue {metrics['control_revenue']},
    conversion rate {metrics['control_conversion_rate']}%,
    average order value {metrics['control_average_order_value']},
    margin {metrics['control_margin_percent']}.
    Treatment: {metrics['treatment_customers']} assigned, {metrics['treatment_conversions']} converted,
    {metrics['treatment_orders']} orders, revenue {metrics['treatment_revenue']},
    conversion rate {metrics['treatment_conversion_rate']}%,
    average order value {metrics['treatment_average_order_value']},
    margin {metrics['treatment_margin_percent']}.

    Return ONLY valid JSON with decision CONTINUE, OPTIMIZE, or STOP,
    plus a short reason and next_action. Do not override the hard merchant margin guardrail.
    """
    try:
        response = client.chat.completions.create(
            model="gemini-3.5-flash-lite",
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        parsed = json.loads(content)
        decision = parsed.get("decision")
        if decision not in {"CONTINUE", "OPTIMIZE", "STOP"}:
            raise ValueError("AI evaluation returned an unsupported decision")
        return (
            {
                "decision": decision,
                "reason": str(parsed.get("reason", "")),
                "next_action": str(parsed.get("next_action", "")),
            },
            True,
        )
    except Exception as error:
        print("Gemini Evaluation Error:", error)
        return (
            {
                "decision": "OPTIMIZE",
                "reason": "AI evaluation service is temporarily unavailable.",
                "next_action": "Keep the experiment controlled and review observed performance manually.",
            },
            False,
        )


@app.post("/api/opportunities")
def get_opportunities(data: OpportunityRequest):
    db = SessionLocal()
    try:
        latest_goal = (
            db.query(MerchantGoal)
            .order_by(MerchantGoal.id.desc())
            .first()
        )
        settings = latest_goal or data
        customer_rows = db.query(Customer).all()
        order_rows = db.query(Order).all()
        cart_rows = db.query(CartEvent).all()
        payment_rows = (
            db.query(Payment, Order.customer_id)
            .join(Order, Payment.order_id == Order.id)
            .all()
        )

        opportunities = detect_opportunities(
            customer_ids=[customer.id for customer in customer_rows],
            orders=[
                CommerceOrder(
                    customer_id=order.customer_id,
                    amount=order.amount,
                    status=order.status,
                    created_at=order.created_at,
                )
                for order in order_rows
            ],
            cart_events=[
                CommerceCartEvent(
                    customer_id=cart.customer_id,
                    cart_value=cart.cart_value,
                    event_type=cart.event_type,
                    created_at=cart.created_at,
                )
                for cart in cart_rows
            ],
            payments=[
                CommercePayment(
                    order_id=payment.order_id,
                    customer_id=customer_id,
                    amount=payment.amount,
                    status=payment.status,
                )
                for payment, customer_id in payment_rows
            ],
            max_discount=settings.max_discount,
        )

        existing_opportunities = db.query(Opportunity).all()
        existing_by_title = {
            opportunity.title: opportunity
            for opportunity in existing_opportunities
        }
        persisted_opportunities = []
        for item in opportunities:
            opportunity = existing_by_title.pop(item["title"], None)
            if opportunity is None:
                opportunity = Opportunity(**item)
                db.add(opportunity)
            else:
                for field, value in item.items():
                    setattr(opportunity, field, value)
            persisted_opportunities.append(opportunity)

        db.commit()
        for item, opportunity in zip(opportunities, persisted_opportunities):
            item["id"] = opportunity.id
            item["created_at"] = opportunity.created_at

        return {
            "status": "success",
            "goal": settings.goal,
            "opportunities": opportunities,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

@app.get("/api/opportunities")
def list_opportunities():
    db = SessionLocal()

    opportunities = db.query(Opportunity).all()

    result = []

    for item in opportunities:
        result.append({
            "id": item.id,
            "title": item.title,
            "priority": item.priority,
            "potential_revenue": item.potential_revenue,
            "recommended_action": item.recommended_action,
            "suggested_discount": item.suggested_discount,
            "risk": item.risk,
            "score": item.score,
            "affected_customers": item.affected_customers,
            "affected_orders": item.affected_orders,
            "evidence": item.evidence,
            "created_at": item.created_at,
        })

    db.close()

    return {
        "status": "success",
        "count": len(result),
        "opportunities": result,
    }
class DecisionRequest(BaseModel):
    opportunity: str
    potential_revenue: float
    suggested_discount: float
    max_discount: float
    min_margin: float


@app.post("/api/decision")
def make_decision(data: DecisionRequest):

    # GUARDRAIL 1: Discount policy
    if data.suggested_discount > data.max_discount:
        return {
            "status": "blocked",
            "decision": "REJECT",
            "reason": "Discount exceeds merchant policy",
            "policy_check": "FAILED",
            "approval_required": False,
        }

    # GUARDRAIL 2: Minimum margin policy
    if data.min_margin < 15:
        return {
            "status": "blocked",
            "decision": "REJECT",
            "reason": "Minimum margin is below safe threshold",
            "policy_check": "FAILED",
            "approval_required": False,
        }

    # DECISION ENGINE
    if data.potential_revenue >= 30000:
        decision = "APPROVE"
        risk = "LOW"
        approval_required = False
    elif data.potential_revenue >= 15000:
        decision = "REVIEW"
        risk = "MEDIUM"
        approval_required = True
    else:
        decision = "REVIEW"
        risk = "HIGH"
        approval_required = True

    return {
        "status": "success",
        "decision": decision,
        "opportunity": data.opportunity,
        "risk": risk,
        "approval_required": approval_required,
        "policy_check": "PASSED",
        "recommended_action": "Launch controlled experiment",
        "experiment": {
            "test_group": "90%",
            "holdout_group": "10%",
        },
        "measurement": [
            "Incremental revenue",
            "Conversion rate",
            "Profit margin",
        ],
    }

class StrategyRequest(BaseModel):
    opportunity: str
    potential_revenue: float
    suggested_discount: float
    max_discount: float
    min_margin: float
    budget: Optional[float] = None
    target_growth: Optional[float] = None


@app.post("/api/strategy")
def run_strategy(data: StrategyRequest):

    if data.suggested_discount > data.max_discount:
        return {
            "status": "success",
            "strategy": '{"decision":"REJECT","risk":"HIGH","recommended_action":"Do not launch this offer.","reason":"Merchant guardrail blocked the discount because it exceeds the maximum allowed discount."}'
        }

    if data.min_margin < 15:
        return {
            "status": "success",
            "strategy": '{"decision":"REJECT","risk":"HIGH","recommended_action":"Do not launch this offer.","reason":"Merchant guardrail blocked the action because minimum margin is below the safety threshold."}'
        }

    db = SessionLocal()
    try:
        learning_total, learning_context = _get_opportunity_learning(
            db, data.opportunity, limit=5
        )
        merchant_goal = (
            db.query(MerchantGoal)
            .order_by(MerchantGoal.id.desc())
            .first()
        )
        effective_budget = (
            min(merchant_goal.budget, data.budget)
            if merchant_goal is not None and data.budget is not None
            else merchant_goal.budget
            if merchant_goal is not None
            else data.budget
        )
        effective_target_growth = (
            data.target_growth
            if data.target_growth is not None
            else merchant_goal.goal
            if merchant_goal is not None
            else None
        )
    finally:
        db.close()

    learning_prompt = (
        "\nRelevant learning from prior completed experiments for this exact "
        "opportunity (evidence only; merchant guardrails always take precedence):\n"
        + json.dumps(learning_context, separators=(",", ":"))
        if learning_context
        else "\nNo completed experiment learning is available for this exact opportunity.\n"
    )
    prompt = f"""
You are an AI Growth Strategy Agent for an e-commerce merchant.

Analyze this opportunity:

Opportunity: {data.opportunity}

Potential revenue: ₹{data.potential_revenue}

Suggested discount: {data.suggested_discount}%

Merchant max discount: {data.max_discount}%

Merchant minimum margin: {data.min_margin}%

Merchant experiment budget: {effective_budget if effective_budget is not None else "not provided"}

Merchant target growth: {effective_target_growth if effective_target_growth is not None else "not provided"}%

{learning_prompt}
Recommend the safest growth strategy.

Return ONLY valid JSON in this exact format:

{{
  "decision": "APPROVE",
  "risk": "LOW",
  "recommended_action": "short action",
  "reason": "short reason"
}}

Rules:

- decision must be APPROVE, REVIEW, or REJECT
- risk must be LOW, MEDIUM, or HIGH
- Do not include markdown
- Do not include ```json
- Return JSON only
"""


    try:
        response = client.chat.completions.create(
            model="gemini-3.5-flash-lite",
            messages=[
                {"role": "user", "content": prompt}
            ]
        )

        return {
            "status": "success",
            "strategy": response.choices[0].message.content,
            "learning_context": {
                "opportunity": data.opportunity,
                "total_completed_experiments": learning_total,
                "records": learning_context,
            },
        }

    except Exception as e:
        print("Gemini API Error:", e)

        return {
            "status": "error",
            "strategy": '{"decision":"REVIEW","risk":"MEDIUM","recommended_action":"Wait before launching the campaign.","reason":"AI strategy service is temporarily unavailable because the Gemini API quota has been exceeded. Merchant approval is required."}',
            "learning_context": {
                "opportunity": data.opportunity,
                "total_completed_experiments": learning_total,
                "records": learning_context,
            },
        }


class AutopilotNextActionRequest(BaseModel):
    opportunity: str
    goal: float
    max_discount: float
    budget: float
    min_margin: float


@app.post("/api/autopilot/next-action")
def recommend_autopilot_next_action(data: AutopilotNextActionRequest):
    db = SessionLocal()
    try:
        opportunity = (
            db.query(Opportunity)
            .filter(Opportunity.title == data.opportunity)
            .order_by(Opportunity.id.desc())
            .first()
        )
        if opportunity is None:
            raise HTTPException(
                status_code=404,
                detail="Opportunity was not found. Refresh detected opportunities before requesting a next action.",
            )

        merchant_goal = (
            db.query(MerchantGoal)
            .order_by(MerchantGoal.id.desc())
            .first()
        )
        max_discount = min(
            data.max_discount,
            merchant_goal.max_discount if merchant_goal is not None else data.max_discount,
        )
        min_margin = max(
            data.min_margin,
            merchant_goal.min_margin if merchant_goal is not None else data.min_margin,
        )
        budget = min(
            data.budget,
            merchant_goal.budget if merchant_goal is not None else data.budget,
        )
        learning_total, history = _get_opportunity_learning(
            db, opportunity.title, limit=5
        )
        opportunity_data = {
            "title": opportunity.title,
            "potential_revenue": opportunity.potential_revenue,
            "recommended_action": opportunity.recommended_action,
            "suggested_discount": opportunity.suggested_discount,
        }
    finally:
        db.close()

    strategy_result = run_strategy(
        StrategyRequest(
            opportunity=opportunity_data["title"],
            potential_revenue=opportunity_data["potential_revenue"],
            suggested_discount=opportunity_data["suggested_discount"],
            max_discount=max_discount,
            min_margin=min_margin,
            budget=budget,
            target_growth=data.goal,
        )
    )
    try:
        strategy = json.loads(strategy_result["strategy"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=502,
            detail="The strategy service returned an unreadable recommendation.",
        ) from error

    decision = strategy.get("decision")
    action_by_decision = {
        "APPROVE": (
            "READY_FOR_CONTROLLED_EXPERIMENT",
            "Recommend a controlled experiment",
        ),
        "REVIEW": ("REQUEST_MERCHANT_REVIEW", "Request merchant review"),
        "REJECT": ("HOLD_FOR_GUARDRAIL", "Hold this action"),
    }
    action_code, action_label = action_by_decision.get(
        decision, ("MANUAL_REVIEW", "Review recommendation manually")
    )
    latest_learning = history[0] if history else None
    recommended_strategy = str(
        strategy.get("recommended_action", opportunity_data["recommended_action"])
    )
    control_variant, treatment_variant = variant_definition(
        opportunity_data["title"],
        recommended_strategy,
        opportunity_data["suggested_discount"],
    )
    expected_learning_objective = (
        f"Compare observed treatment and control {opportunity_data['title'].lower()} outcomes "
        "after the minimum sample and observation requirements are met."
    )
    return {
        "status": strategy_result["status"],
        "opportunity": opportunity_data["title"],
        "strategy_decision": decision,
        "recommended_action": {
            "code": action_code,
            "label": action_label,
            "rationale": strategy.get("reason", ""),
        },
        "hypothesis": build_hypothesis(
            opportunity_data["title"],
            opportunity_data["suggested_discount"],
        ),
        "expected_learning_objective": expected_learning_objective,
        "recommended_experiment_configuration": {
            "control": {"traffic_percent": 90, "definition": control_variant},
            "treatment": {"traffic_percent": 10, "definition": treatment_variant},
            "suggested_discount": opportunity_data["suggested_discount"],
            "max_discount": max_discount,
            "budget": budget,
            "min_margin": min_margin,
            "guardrail_approved": decision == "APPROVE",
        },
        "learning_signal": (
            latest_learning["learning_signal"]
            if latest_learning
            else "NO_PRIOR_LEARNING"
        ),
        "confidence": (
            latest_learning["confidence"] if latest_learning else None
        ),
        "confidence_basis": (
            latest_learning["confidence_basis"]
            if latest_learning
            else "No completed experiment history exists for this opportunity."
        ),
        "total_historical_experiments": learning_total,
        "historical_experiments": history,
        "campaign_execution": "not_performed",
        "advisory_only": True,
    }
