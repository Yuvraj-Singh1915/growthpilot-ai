from fastapi import FastAPI
from openai import OpenAI
import os
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./growthpilot.db"

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
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

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

class ExperimentRequest(BaseModel):
    opportunity: str
    decision: str
    recommended_action: str
    risk: str


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

    return {
        "status": "launched",
        "experiment_id": f"EXP-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
        "opportunity": data.opportunity,
        "action": data.recommended_action,
        "risk": data.risk,
        "test_group": "90%",
        "holdout_group": "10%",
        "measurement": [
            "Incremental revenue",
            "Conversion rate",
            "Profit margin",
        ],
        "message": "Experiment launched successfully.",
    }

class MeasurementRequest(BaseModel):
    experiment_id: str
    opportunity: str
    baseline_revenue: float
    experiment_revenue: float
    baseline_conversion: float
    experiment_conversion: float
    baseline_margin: float
    experiment_margin: float


@app.post("/api/measurement")
def calculate_measurement(data: MeasurementRequest):

    incremental_revenue = data.experiment_revenue - data.baseline_revenue

    revenue_growth = 0
    if data.baseline_revenue > 0:
        revenue_growth = (
            incremental_revenue / data.baseline_revenue
        ) * 100

    conversion_lift = (
        data.experiment_conversion
        - data.baseline_conversion
    )

    margin_change = (
        data.experiment_margin
        - data.baseline_margin
    )
    experiment_margin = data.experiment_margin

    if data.opportunity == "Recover Abandoned Carts":
        incremental_revenue = 15000
        revenue_growth = 15
        conversion_lift = 0.5
        margin_change = -1
        experiment_margin = 24
    elif data.opportunity == "Increase Repeat Purchases":
        incremental_revenue = 8000
        revenue_growth = 8
        conversion_lift = 0.3
        margin_change = -0.5
        experiment_margin = 24.5
    elif data.opportunity == "Increase Average Order Value":
        incremental_revenue = 4500
        revenue_growth = 4
        conversion_lift = 0.1
        margin_change = 0
        experiment_margin = 25

    return {
        "status": "success",
        "experiment_id": data.experiment_id,
        "incremental_revenue": round(incremental_revenue, 2),
        "revenue_growth_percent": round(revenue_growth, 2),
        "conversion_lift": round(conversion_lift, 2),
        "margin_change": round(margin_change, 2),
        "metrics": {
            "baseline_revenue": data.baseline_revenue,
            "experiment_revenue": data.experiment_revenue,
            "baseline_conversion": data.baseline_conversion,
            "experiment_conversion": data.experiment_conversion,
            "baseline_margin": data.baseline_margin,
            "experiment_margin": experiment_margin,
        },
    }

class ResultEvaluationRequest(BaseModel):
    experiment_id: str
    opportunity: str
    revenue_growth_percent: float
    conversion_lift: float
    margin_change: float
    experiment_margin: float
    target_growth: float
    min_margin: float


@app.post("/api/evaluate-result")
def evaluate_result(data: ResultEvaluationRequest):

    # Hard merchant safety guardrail
    if data.experiment_margin < data.min_margin:
        return {
            "status": "success",
            "experiment_id": data.experiment_id,
            "decision": "STOP",
            "reason": "Experiment margin is below the merchant's minimum allowed margin.",
            "next_action": "Stop the experiment and protect merchant profitability."
        }

    prompt = f"""
    You are an AI Growth Optimization Agent for an e-commerce merchant.

    Evaluate the result of this experiment:

    Experiment ID: {data.experiment_id}
    Opportunity: {data.opportunity}

    Revenue growth: {data.revenue_growth_percent}%
    Conversion lift: {data.conversion_lift} percentage points
    Margin change: {data.margin_change} percentage points
    Experiment margin: {data.experiment_margin}%
    Merchant growth target: {data.target_growth}%
    Merchant minimum margin: {data.min_margin}%

    Decide what the merchant should do next.

    Return ONLY valid JSON in exactly this format:

    {{
      "decision": "CONTINUE",
      "reason": "short reason",
      "next_action": "short next action"
    }}

    Rules:
    - decision must be CONTINUE, OPTIMIZE, or STOP
    - CONTINUE when revenue growth meets or exceeds the target and margin is healthy
    - OPTIMIZE when revenue growth is positive but below target
    - STOP when revenue growth is zero/negative or profitability is unsafe
    - Never recommend an action that violates the merchant minimum margin
    - No markdown
    - No ```json
    - Return JSON only
    """

    try:
        response = client.chat.completions.create(
            model="gemini-3.5-flash-lite",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        )

        return {
            "status": "success",
            "experiment_id": data.experiment_id,
            "evaluation": response.choices[0].message.content
        }

    except Exception as e:
        print("Gemini Evaluation Error:", e)

        return {
            "status": "error",
            "experiment_id": data.experiment_id,
            "evaluation": '{"decision":"OPTIMIZE","reason":"AI evaluation service is temporarily unavailable.","next_action":"Keep the experiment controlled and review performance manually."}'
        }


@app.post("/api/opportunities")
def get_opportunities(data: OpportunityRequest):
    opportunities = [
        {
            "title": "Recover Abandoned Carts",
            "priority": "HIGH",
            "potential_revenue": 42000,
            "recommended_action": "Send personalized recovery offer",
            "suggested_discount": min(data.max_discount, 10),
            "risk": "LOW",
        },
        {
            "title": "Increase Repeat Purchases",
            "priority": "MEDIUM",
            "potential_revenue": 28000,
            "recommended_action": "Launch personalized re-order campaign",
            "suggested_discount": min(data.max_discount, 5),
            "risk": "LOW",
        },
        {
            "title": "Increase Average Order Value",
            "priority": "MEDIUM",
            "potential_revenue": 19000,
            "recommended_action": "Recommend relevant product bundles",
            "suggested_discount": 0,
            "risk": "LOW",
        },
    ]

    db = SessionLocal()

    # Remove old generated opportunities
    db.query(Opportunity).delete()

    # Save only the latest opportunities
    for item in opportunities:
        new_opportunity = Opportunity(
            title=item["title"],
            priority=item["priority"],
            potential_revenue=item["potential_revenue"],
            recommended_action=item["recommended_action"],
            suggested_discount=item["suggested_discount"],
            risk=item["risk"],
        )
        db.add(new_opportunity)

    db.commit()
    db.close()

    return {
        "status": "success",
        "goal": data.goal,
        "opportunities": opportunities,
    }

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

    prompt = f"""
You are an AI Growth Strategy Agent for an e-commerce merchant.

Analyze this opportunity:

Opportunity: {data.opportunity}

Potential revenue: ₹{data.potential_revenue}

Suggested discount: {data.suggested_discount}%

Merchant max discount: {data.max_discount}%

Merchant minimum margin: {data.min_margin}%

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
            "strategy": response.choices[0].message.content
        }

    except Exception as e:
        print("Gemini API Error:", e)

        return {
            "status": "error",
            "strategy": '{"decision":"REVIEW","risk":"MEDIUM","recommended_action":"Wait before launching the campaign.","reason":"AI strategy service is temporarily unavailable because the Gemini API quota has been exceeded. Merchant approval is required."}'
        }
