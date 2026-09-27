from typing import Any

from services.measurement_engine import (
    MIN_CUSTOMERS_PER_VARIANT,
    MIN_OBSERVATION_DAYS,
)


def build_learning_evidence(
    decision: str,
    reason: str,
    next_action: str,
    metrics: dict[str, Any],
) -> dict[str, Any] | None:
    if metrics.get("sample_status") != "sufficient_sample":
        return None

    control_customers = metrics.get("control_customers")
    treatment_customers = metrics.get("treatment_customers")
    observation_days = metrics.get("observation_days")
    if (
        not isinstance(control_customers, int)
        or not isinstance(treatment_customers, int)
        or not isinstance(observation_days, (int, float))
        or control_customers < MIN_CUSTOMERS_PER_VARIANT
        or treatment_customers < MIN_CUSTOMERS_PER_VARIANT
        or observation_days < MIN_OBSERVATION_DAYS
    ):
        return None

    normalized_decision = decision.upper()
    signal_by_decision = {
        "CONTINUE": "POSITIVE",
        "OPTIMIZE": "OPTIMIZE",
        "STOP": "NEGATIVE",
    }
    signal = signal_by_decision.get(normalized_decision)
    if signal is None:
        return None

    sample_coverage = min(1.0, min(control_customers, treatment_customers) / 30)
    observation_coverage = min(1.0, observation_days / 14)
    confidence = round(sample_coverage * observation_coverage, 4)

    normalized_action = next_action.lower()
    if normalized_decision == "CONTINUE":
        action_code = "CONTINUE_CURRENT_STRATEGY"
    elif normalized_decision == "STOP":
        action_code = "STOP_STRATEGY"
    elif "discount" in normalized_action and any(
        word in normalized_action for word in ("reduce", "lower", "decrease")
    ):
        action_code = "REDUCE_DISCOUNT"
    elif any(word in normalized_action for word in ("audience", "segment", "target")):
        action_code = "REFINE_TARGETING"
    else:
        action_code = "TRY_DIFFERENT_OFFER"

    return {
        "learning_signal": signal,
        "signal_reason": reason,
        "confidence": confidence,
        "confidence_basis": (
            f"Evidence coverage = min(control/treatment assigned customers, 30) "
            f"coverage ({sample_coverage:.2f}) × min(observation days, 14) "
            f"coverage ({observation_coverage:.2f}); this is not a statistical "
            "significance probability."
        ),
        "recommended_action_code": action_code,
    }
