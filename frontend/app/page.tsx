"use client";

import { useCallback, useEffect, useState } from "react";
import { apiUrl } from "@/lib/api";

type Opportunity = {
  title: string;
  priority: string;
  potential_revenue: number;
  suggested_discount: number;
  recommended_action: string;
  score?: number;
  affected_customers?: number;
  affected_orders?: number;
  evidence?: string;
};

type DecisionResult = {
  decision: string;
  risk: string;
  recommended_action: string;
  reason: string;
  approval_required: boolean;
};

type ExperimentEvaluation = {
  decision?: string;
  reason?: string;
  next_action?: string;
};

type EvaluationResult = {
  experiment_status?: string;
  evaluation?: string | ExperimentEvaluation;
};

type ExperimentResult = {
  status: string;
  experiment_status?: string;
  experiment_id?: string;
  test_group?: string;
  holdout_group?: string;
  measurement?: string[];
  message?: string;
  hypothesis?: string;
  assigned_customers?: {
    control: number;
    treatment: number;
    total: number;
  };
  started_at?: string;
  measurement_status?: string;
  completed_at?: string | null;
};

type MeasurementResult = {
  incremental_revenue: number | null;
  revenue_growth_percent: number | null;
  conversion_lift: number | null;
  margin_change: number | null;
  metrics?: {
    sample_status?: string;
    eligible_customers?: number;
    control_customers?: number;
    treatment_customers?: number;
    control_conversions?: number;
    treatment_conversions?: number;
    control_orders?: number;
    treatment_orders?: number;
    control_margin_percent?: number | null;
    treatment_margin_percent?: number | null;
    control_average_order_value?: number | null;
    treatment_average_order_value?: number | null;
    sample_issues?: string[];
    observation_days?: number;
    minimum_observation_days?: number;
  };
};

type ObservationProgress = {
  observationDays: number;
  minimumDays: number;
  percentComplete: number | null;
};

function getObservationProgress(
  metrics?: MeasurementResult["metrics"]
): ObservationProgress | null {
  const observationDays = metrics?.observation_days;
  const minimumDays = metrics?.minimum_observation_days;
  if (
    typeof observationDays !== "number" ||
    !Number.isFinite(observationDays) ||
    observationDays < 0 ||
    typeof minimumDays !== "number" ||
    !Number.isFinite(minimumDays) ||
    minimumDays < 0
  ) {
    return null;
  }

  return {
    observationDays,
    minimumDays,
    percentComplete:
      minimumDays > 0
        ? Math.min(100, (observationDays / minimumDays) * 100)
        : null,
  };
}

type LearningRecord = {
  experiment_id: string;
  opportunity: string;
  original_strategy_action: string | null;
  experiment_status: string;
  hypothesis: string;
  decision: string;
  reason: string;
  next_action: string;
  recommended_action_code: string;
  learning_signal: string;
  signal_reason: string;
  confidence: number;
  confidence_basis: string;
  metrics: {
    control_customers?: number;
    treatment_customers?: number;
    observation_days?: number;
    revenue_growth_percent?: number | null;
    conversion_lift?: number | null;
    margin_change?: number | null;
  };
  evaluated_at: string;
};

type NextActionResult = {
  status: string;
  strategy_decision: string;
  recommended_action: {
    code: string;
    label: string;
    rationale: string;
  };
  learning_signal: string;
  confidence: number | null;
  confidence_basis: string;
  total_historical_experiments: number;
  campaign_execution: string;
  hypothesis: string;
  expected_learning_objective: string;
  recommended_experiment_configuration: {
    control: { traffic_percent: number; definition: string };
    treatment: { traffic_percent: number; definition: string };
    suggested_discount: number;
    max_discount: number;
    budget: number;
    min_margin: number;
    guardrail_approved: boolean;
  };
  advisory_only: boolean;
};

async function getApiErrorMessage(response: Response, fallback: string) {
  try {
    const body: unknown = await response.json();
    if (
      typeof body === "object" &&
      body !== null &&
      "detail" in body &&
      typeof body.detail === "string"
    ) {
      return body.detail;
    }
  } catch {
    // Use the status-based message when the response is not JSON.
  }

  return `${fallback} (HTTP ${response.status}).`;
}

function parseExperimentEvaluation(
  evaluation: EvaluationResult["evaluation"]
): ExperimentEvaluation | undefined {
  if (typeof evaluation !== "string") {
    return evaluation;
  }

  try {
    const parsed: unknown = JSON.parse(evaluation);
    if (typeof parsed !== "object" || parsed === null) {
      return undefined;
    }

    const result = parsed as Record<string, unknown>;
    if (
      ["decision", "reason", "next_action"].every(
        (field) => result[field] === undefined || typeof result[field] === "string"
      )
    ) {
      return result as ExperimentEvaluation;
    }
  } catch {
    return undefined;
  }

  return undefined;
}

function formatMeasurementStatus(status?: string) {
  switch (status) {
    case "insufficient_observation_window":
      return "Collecting experiment evidence";
    case "insufficient_sample":
      return "Insufficient sample";
    case "sufficient_sample":
      return "Observation complete";
    default:
      return status?.replaceAll("_", " ") || "Waiting for measurement";
  }
}

function formatObservedMargin(margin?: number | null) {
  return margin == null ? "Unavailable" : `${margin}%`;
}

export default function Home() {
  const [goal, setGoal] = useState(15);
  const [maxDiscount, setMaxDiscount] = useState(10);
  const [budget, setBudget] = useState(50000);
  const [minMargin, setMinMargin] = useState(20);

  const [editingGoal, setEditingGoal] = useState(false);
  const [savingGoal, setSavingGoal] = useState(false);
  const [apiStatus, setApiStatus] = useState("");
  const [apiStatusIsError, setApiStatusIsError] = useState(false);

  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [loadingOpportunities, setLoadingOpportunities] = useState(false);
  const [opportunityError, setOpportunityError] = useState("");
  const [seedingDemoData, setSeedingDemoData] = useState(false);
  const [demoSeedMessage, setDemoSeedMessage] = useState("");

  const [decisionResults, setDecisionResults] = useState<
    Record<number, DecisionResult>
  >({});
  const [decisionErrors, setDecisionErrors] = useState<Record<number, string>>({});
  const [experimentLaunchErrors, setExperimentLaunchErrors] = useState<
    Record<number, string>
  >({});

  const [experimentResults, setExperimentResults] = useState<
    Record<number, ExperimentResult>
  >({});

  const [measurementResults, setMeasurementResults] = useState<
    Record<number, MeasurementResult>
  >({});
  const [refreshingExperiment, setRefreshingExperiment] = useState<
    Record<number, boolean>
  >({});
  const [experimentRefreshErrors, setExperimentRefreshErrors] = useState<
    Record<number, string>
  >({});
  const [staleMeasurementResults, setStaleMeasurementResults] = useState<
    Record<number, boolean>
  >({});

  const [loadingDecision, setLoadingDecision] = useState<number | null>(null);
  const [evaluationResults, setEvaluationResults] = useState<
    Record<number, EvaluationResult>
  >({});
  const [loadingEvaluation, setLoadingEvaluation] = useState<number | null>(null);
  const [learningRecords, setLearningRecords] = useState<LearningRecord[]>([]);
  const [learningTotal, setLearningTotal] = useState(0);
  const [loadingLearning, setLoadingLearning] = useState(false);
  const [learningError, setLearningError] = useState("");
  const [nextActionResults, setNextActionResults] = useState<
    Record<number, NextActionResult>
  >({});
  const [nextActionErrors, setNextActionErrors] = useState<Record<number, string>>({});
  const [loadingNextAction, setLoadingNextAction] = useState<number | null>(null);

  // ---------------------------------------
  // LOAD OPPORTUNITIES
  // ---------------------------------------

  const loadOpportunities = useCallback(async () => {
    setLoadingOpportunities(true);
    setOpportunityError("");

    try {
      const response = await fetch(
        apiUrl("/api/opportunities"),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            goal,
            max_discount: maxDiscount,
            budget,
            min_margin: minMargin,
          }),
        }
      );

      if (!response.ok) {
        throw new Error(
          await getApiErrorMessage(response, "Failed to load opportunities.")
        );
      }

      const data = await response.json();

      setOpportunities(data.opportunities);
    } catch (error) {
      console.error("Opportunity Engine error:", error);
      setOpportunityError(
        error instanceof Error
          ? error.message
          : "Could not load opportunities. Check that the backend is running."
      );
    } finally {
      setLoadingOpportunities(false);
    }
  }, [goal, maxDiscount, budget, minMargin]);

  const loadLearningMemory = async () => {
    setLoadingLearning(true);
    setLearningError("");
    try {
      const response = await fetch(apiUrl("/api/learning"));
      if (!response.ok) {
        throw new Error("Failed to load experiment learning");
      }
      const data = await response.json();
      setLearningRecords(data.records);
      setLearningTotal(data.total);
    } catch (error) {
      console.error("Learning memory error:", error);
      setLearningError("Could not load learning memory. Check that the backend is running.");
    } finally {
      setLoadingLearning(false);
    }
  };

  const requestNextAction = async (index: number, opportunity: Opportunity) => {
    setLoadingNextAction(index);
    setNextActionErrors((previous) => ({
      ...previous,
      [index]: "",
    }));
    try {
      const response = await fetch(
        apiUrl("/api/autopilot/next-action"),
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            opportunity: opportunity.title,
            goal,
            max_discount: maxDiscount,
            budget,
            min_margin: minMargin,
          }),
        }
      );
      if (!response.ok) {
        throw new Error(
          await getApiErrorMessage(response, "Failed to get the next best action.")
        );
      }
      const data = await response.json();
      setNextActionResults((previous) => ({
        ...previous,
        [index]: data,
      }));
    } catch (error) {
      console.error("Autopilot next-action error:", error);
      setNextActionErrors((previous) => ({
        ...previous,
        [index]:
          error instanceof Error
            ? error.message
            : "Could not get a next-action recommendation. Check that the backend is running.",
      }));
    } finally {
      setLoadingNextAction(null);
    }
  };

  const refreshExperiment = async (index: number) => {
    const experimentId = experimentResults[index]?.experiment_id;
    if (!experimentId) {
      return;
    }

    setRefreshingExperiment((previous) => ({ ...previous, [index]: true }));
    setExperimentRefreshErrors((previous) => ({ ...previous, [index]: "" }));
    setStaleMeasurementResults((previous) => ({
      ...previous,
      [index]: !!measurementResults[index],
    }));

    try {
      const experimentResponse = await fetch(
        apiUrl(`/api/experiment/${encodeURIComponent(experimentId)}`)
      );
      if (!experimentResponse.ok) {
        throw new Error(
          await getApiErrorMessage(
            experimentResponse,
            "Failed to refresh experiment status."
          )
        );
      }
      const experimentData: ExperimentResult = await experimentResponse.json();
      setExperimentResults((previous) => ({
        ...previous,
        [index]: {
          ...previous[index],
          ...experimentData,
          experiment_status:
            experimentData.experiment_status ?? experimentData.status,
        },
      }));

      const measurementResponse = await fetch(apiUrl("/api/measurement"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ experiment_id: experimentId }),
      });
      if (!measurementResponse.ok) {
        throw new Error(
          await getApiErrorMessage(
            measurementResponse,
            "Failed to refresh experiment measurement."
          )
        );
      }
      const measurementData: MeasurementResult =
        await measurementResponse.json();

      setMeasurementResults((previous) => ({
        ...previous,
        [index]: measurementData,
      }));
      setStaleMeasurementResults((previous) => ({
        ...previous,
        [index]: false,
      }));
    } catch (error) {
      console.error("Experiment refresh error:", error);
      setExperimentRefreshErrors((previous) => ({
        ...previous,
        [index]:
          error instanceof Error
            ? error.message
            : "Could not refresh experiment data.",
      }));
    } finally {
      setRefreshingExperiment((previous) => ({ ...previous, [index]: false }));
    }
  };

  useEffect(() => {
    loadOpportunities();
    void loadLearningMemory();
  }, [loadOpportunities, goal, maxDiscount, budget, minMargin]);

  // ---------------------------------------
  // AI DECISION → EXPERIMENT → MEASUREMENT
  // ---------------------------------------

  const runDecision = async (index: number, opportunity: Opportunity) => {
  setDecisionErrors((previous) => ({
    ...previous,
    [index]: "",
  }));
  setExperimentLaunchErrors((previous) => ({
    ...previous,
    [index]: "",
  }));
  setLoadingDecision(index);

  try {
    // 1. AI STRATEGY
    const response = await fetch(apiUrl("/api/strategy"), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        opportunity: opportunity.title,
        potential_revenue: opportunity.potential_revenue,
        suggested_discount: opportunity.suggested_discount,
        max_discount: maxDiscount,
        min_margin: minMargin,
        budget,
        target_growth: goal,
      }),
    });

    if (!response.ok) {
      throw new Error(
        await getApiErrorMessage(response, "Strategy request failed.")
      );
    }

    const data = await response.json();
    const aiStrategy = JSON.parse(data.strategy);

    setDecisionResults((prev) => ({
      ...prev,
      [index]: {
        decision: aiStrategy.decision,
        risk: aiStrategy.risk,
        recommended_action: aiStrategy.recommended_action,
        reason: aiStrategy.reason,
        approval_required: aiStrategy.decision !== "APPROVE",
      },
    }));

    // Only APPROVED strategies continue to experiment
    if (aiStrategy.decision !== "APPROVE") {
      return;
    }

    // 2. CREATE EXPERIMENT
    const experimentResponse = await fetch(
      apiUrl("/api/experiment"),
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          opportunity: opportunity.title,
          decision: aiStrategy.decision,
          recommended_action: aiStrategy.recommended_action,
          risk: aiStrategy.risk,
          budget,
          max_discount: maxDiscount,
          min_margin: minMargin,
          target_growth: goal,
        }),
      }
    );

    if (!experimentResponse.ok) {
      const message = await getApiErrorMessage(
        experimentResponse,
        "Experiment launch failed."
      );
      setExperimentLaunchErrors((previous) => ({
        ...previous,
        [index]: message,
      }));
      throw new Error(message);
    }

    const experimentData = await experimentResponse.json();

    if (experimentData.status !== "launched") {
      setExperimentLaunchErrors((previous) => ({
        ...previous,
        [index]: experimentData.message || "Experiment was not launched.",
      }));
      return;
    }

    setExperimentResults((prev) => ({
      ...prev,
      [index]: {
        status: experimentData.status,
        experiment_status: experimentData.experiment_status,
        experiment_id: experimentData.experiment_id,
        test_group: experimentData.test_group,
        holdout_group: experimentData.holdout_group,
        measurement: experimentData.measurement,
        message: experimentData.message,
        hypothesis: experimentData.hypothesis,
        assigned_customers: experimentData.assigned_customers,
        started_at: experimentData.started_at,
        measurement_status: experimentData.measurement_status,
      },
    }));

    // 3. MEASUREMENT
    // Measurement is derived from persisted assignments and commerce records.
    const measurementResponse = await fetch(
      apiUrl("/api/measurement"),
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          experiment_id: experimentData.experiment_id,
        }),
      }
    );

    if (!measurementResponse.ok) {
      throw new Error(
        await getApiErrorMessage(measurementResponse, "Measurement request failed.")
      );
    }

    const measurementData = await measurementResponse.json();

    setMeasurementResults((prev) => ({
      ...prev,
      [index]: {
        incremental_revenue: measurementData.incremental_revenue,
        revenue_growth_percent: measurementData.revenue_growth_percent,
        conversion_lift: measurementData.conversion_lift,
        margin_change: measurementData.margin_change,
        metrics: measurementData.metrics,
      },
    }));

    // 4. AI RESULT EVALUATION
    setLoadingEvaluation(index);

    const evaluationResponse = await fetch(
      apiUrl("/api/evaluate-result"),
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          experiment_id: experimentData.experiment_id,
        }),
      }
    );

    if (!evaluationResponse.ok) {
      throw new Error(
        await getApiErrorMessage(evaluationResponse, "Evaluation request failed.")
      );
    }

    const evaluationData: EvaluationResult = await evaluationResponse.json();

    setEvaluationResults((prev) => ({
      ...prev,
      [index]: evaluationData,
    }));
    if (evaluationData.experiment_status) {
      setExperimentResults((previous) => ({
        ...previous,
        [index]: {
          ...previous[index],
          experiment_status: evaluationData.experiment_status,
        },
      }));
    }
    await loadLearningMemory();
  } catch (error) {
    console.error("GrowthPilot AI flow error:", error);
    setDecisionErrors((previous) => ({
      ...previous,
      [index]:
        error instanceof Error
          ? error.message
          : "The growth flow could not be completed. Please try again.",
    }));
  } finally {
    setLoadingDecision(null);
    setLoadingEvaluation(null);
  }
};
  // ---------------------------------------
  // LAST MEASUREMENT RESULT
  // ---------------------------------------

  const measurementKeys = Object.keys(
    measurementResults
  );

  const lastMeasurementIndex =
    measurementKeys.length > 0
      ? Number(
          measurementKeys[
            measurementKeys.length - 1
          ]
        )
      : null;

  const lastMeasurement =
    lastMeasurementIndex !== null
      ? measurementResults[lastMeasurementIndex]
      : null;

  const lastMeasurementIsStale =
    lastMeasurementIndex !== null &&
    !!staleMeasurementResults[lastMeasurementIndex];
  const isWaitingForObservation =
    !lastMeasurement ||
    lastMeasurementIsStale ||
    lastMeasurement.metrics?.sample_status ===
      "insufficient_observation_window";
  const waitingForObservationCopy =
    lastMeasurementIsStale
      ? "The latest measurement refresh failed. Previously observed values are marked stale until refreshed."
      : "No observed results yet. GrowthPilot evaluates the experiment only after the observation window is complete.";
  const observedRevenueValue =
    !isWaitingForObservation && lastMeasurement?.incremental_revenue != null
      ? `₹${lastMeasurement.incremental_revenue.toLocaleString("en-IN")}`
      : isWaitingForObservation
        ? "Awaiting observed experiment data"
        : "Not enough data";
  const observedConversionValue =
    !isWaitingForObservation && lastMeasurement?.conversion_lift != null
      ? `${lastMeasurement.conversion_lift > 0 ? "+" : ""}${lastMeasurement.conversion_lift} pp`
      : isWaitingForObservation
        ? "Awaiting observed experiment data"
        : "Not enough data";
  const observedAovValue =
    !isWaitingForObservation &&
    lastMeasurement?.metrics?.treatment_average_order_value != null
      ? `₹${lastMeasurement.metrics.treatment_average_order_value.toLocaleString("en-IN")}`
      : isWaitingForObservation
        ? "Awaiting observed experiment data"
        : "Not enough data";

  const goalProgress =
    !isWaitingForObservation &&
    lastMeasurement?.revenue_growth_percent !== null &&
    lastMeasurement?.revenue_growth_percent !== undefined
    ? Math.min(
        100,
        (lastMeasurement.revenue_growth_percent /
          Math.max(goal, 1)) *
          100
      )
    : 0;
  const autopilotLearningRecord = experimentResults[0]?.experiment_id
    ? learningRecords.find(
        (record) => record.experiment_id === experimentResults[0].experiment_id
      )
    : undefined;
  const autopilotEvaluation = parseExperimentEvaluation(
    evaluationResults[0]?.evaluation
  );

  return (
    <main className="min-h-screen bg-slate-950 text-white">
      {/* ---------------------------------------
          HEADER
      --------------------------------------- */}

      <header className="border-b border-slate-800 bg-slate-950/90 px-4 py-4 sm:px-6">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="whitespace-nowrap text-lg font-bold tracking-tight sm:text-2xl">
              GrowthPilot{" "}
              <span className="text-blue-400">
                AI
              </span>
            </h1>

            <p className="text-xs text-slate-400 sm:text-sm">
              Autonomous Commerce Growth Engine
            </p>
          </div>

          <div className="shrink-0 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-2 text-right sm:px-3">
            <p className="whitespace-nowrap text-[10px] font-semibold text-emerald-300 sm:text-sm">
              AUTOPILOT READY
            </p>
            <p className="whitespace-nowrap text-[9px] text-slate-400 sm:text-xs">
              Manual launch
            </p>
          </div>
        </div>
      </header>

      {/* ---------------------------------------
          MAIN CONTENT
      --------------------------------------- */}

      <section className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 sm:py-8">

        {/* WELCOME */}

        <div className="mb-8">
          <p className="text-sm font-medium text-blue-400">
            AI GROWTH COMMAND CENTER
          </p>

          <h2 className="mt-2 text-3xl font-bold sm:text-4xl">
            Grow your revenue intelligently.
          </h2>

          <p className="mt-3 max-w-2xl text-slate-400">
            GrowthPilot analyzes your commerce data,
            finds opportunities, chooses strategies,
            and measures results automatically.
          </p>
        </div>

        {/* ---------------------------------------
            GROWTH GOAL
        --------------------------------------- */}

        <div className="mb-8 rounded-2xl border border-blue-500/30 bg-blue-500/10 p-6">

          <p className="text-sm text-blue-300">
            CURRENT GROWTH GOAL
          </p>

          {editingGoal ? (
            <div className="mt-4 grid gap-4 md:grid-cols-2">

              <div>
                <label className="text-sm text-slate-300">
                  Revenue Growth Target (%)
                </label>

                <input
                  type="number"
                  value={goal}
                  onChange={(e) =>
                    setGoal(
                      Number(e.target.value)
                    )
                  }
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-white"
                />
              </div>

              <div>
                <label className="text-sm text-slate-300">
                  Maximum Discount (%)
                </label>

                <input
                  type="number"
                  value={maxDiscount}
                  onChange={(e) =>
                    setMaxDiscount(
                      Number(e.target.value)
                    )
                  }
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-white"
                />
              </div>

              <div>
                <label className="text-sm text-slate-300">
                  Campaign Budget (₹)
                </label>

                <input
                  type="number"
                  value={budget}
                  onChange={(e) =>
                    setBudget(
                      Number(e.target.value)
                    )
                  }
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-white"
                />
              </div>

              <div>
                <label className="text-sm text-slate-300">
                  Minimum Margin (%)
                </label>

                <input
                  type="number"
                  value={minMargin}
                  onChange={(e) =>
                    setMinMargin(
                      Number(e.target.value)
                    )
                  }
                  className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 p-3 text-white"
                />
              </div>
            </div>
          ) : (
            <div className="mt-2">

              <h3 className="text-2xl font-bold">
                Increase Revenue by {goal}%
              </h3>

              <p className="mt-1 text-slate-400">
                Max discount {maxDiscount}% • Budget ₹
                {budget.toLocaleString("en-IN")} •
                Minimum margin {minMargin}%
              </p>

            </div>
          )}

          <button
            onClick={async () => {
              if (!editingGoal) {
                setEditingGoal(true);
                setApiStatus("");
                setApiStatusIsError(false);
                return;
              }

              setSavingGoal(true);
              setApiStatus("");
              setApiStatusIsError(false);

              try {
                const response = await fetch(
                  apiUrl("/api/goal"),
                  {
                    method: "POST",
                    headers: {
                      "Content-Type":
                        "application/json",
                    },
                    body: JSON.stringify({
                      goal,
                      max_discount:
                        maxDiscount,
                      budget,
                      min_margin:
                        minMargin,
                    }),
                  }
                );

                if (!response.ok) {
                  throw new Error(
                    await getApiErrorMessage(response, "Failed to save goal.")
                  );
                }

                setEditingGoal(false);
                await loadOpportunities();
                setApiStatus("Goal saved successfully");
              } catch (error) {
                setApiStatusIsError(true);
                setApiStatus(
                  error instanceof Error
                    ? error.message
                    : "Could not save the growth goal. Check that the backend is running."
                );
              } finally {
                setSavingGoal(false);
              }
            }}
            className="mt-5 rounded-lg bg-blue-500 px-5 py-3 font-semibold text-white hover:bg-blue-400"
          >
            {savingGoal
              ? "Saving..."
              : editingGoal
              ? "Save Goal"
              : "Update Goal"}
          </button>

          {apiStatus && (
            <p
              className={`mt-3 text-sm ${
                apiStatusIsError ? "text-red-300" : "text-green-400"
              }`}
              role={apiStatusIsError ? "alert" : undefined}
            >
              {apiStatus}
            </p>
          )}

        </div>

        {/* ---------------------------------------
            METRICS
        --------------------------------------- */}

        <div className="grid min-w-0 gap-4 sm:grid-cols-2 xl:grid-cols-4">

          <MetricCard
            title="Observed Revenue"
            value={observedRevenueValue}
            change={
              isWaitingForObservation
                ? waitingForObservationCopy
                : "Measured incremental revenue"
            }
            waiting={isWaitingForObservation}
          />

          <MetricCard
            title="Observed Conversion"
            value={observedConversionValue}
            change={
              isWaitingForObservation
                ? waitingForObservationCopy
                : "Treatment conversion lift"
            }
            waiting={isWaitingForObservation}
          />

          <MetricCard
            title="Observed Average Order Value"
            value={observedAovValue}
            change={
              isWaitingForObservation
                ? waitingForObservationCopy
                : "Treatment average order value"
            }
            waiting={isWaitingForObservation}
          />

          <MetricCard
            title="Observed Cart Recovery"
            value="Not measured"
            change="No cart-recovery experiment measurement is available."
          />

        </div>

        {/* ---------------------------------------
            AUTONOMOUS GROWTH LOOP
        --------------------------------------- */}

        <div className="mt-8 rounded-2xl border border-purple-500/20 bg-gradient-to-r from-slate-900 via-slate-900 to-purple-950/40 p-4 sm:p-6">
          <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
            <div>
              <p className="text-sm font-medium text-purple-400">
                AUTONOMOUS GROWTH LOOP
              </p>
              <h3 className="mt-1 text-xl font-bold">
                From opportunity to next best action
              </h3>
            </div>
            <p className="max-w-xl text-sm text-slate-400 md:text-right">
              GrowthPilot analyzes persisted commerce data and recommends controlled learning steps. No campaign is executed.
            </p>
          </div>

          <div className="mt-6 grid min-w-0 grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-9">
            {[
              ["01", "Commerce", "Data Analysis", "text-slate-300"],
              ["02", "Opportunity", "Detection", "text-blue-400"],
              ["03", "AI", "Strategy", "text-purple-400"],
              ["04", "Safety", "Guardrails", "text-orange-400"],
              ["05", "Persistent", "Experiment", "text-green-400"],
              ["06", "Observed", "Measurement", "text-cyan-400"],
              ["07", "AI", "Evaluation", "text-pink-400"],
              ["08", "Learning", "Memory", "text-amber-400"],
              ["09", "Next Best", "Recommendation", "text-emerald-400"],
            ].map(([step, label, detail, color], index) => (
              <div key={step} className="relative min-w-0 rounded-xl border border-slate-800 bg-slate-950/70 p-3 sm:p-4">
                <p className={`text-xs font-semibold ${color}`}>{step}</p>
                <p className="mt-3 break-words text-sm font-semibold text-white">{label}</p>
                <p className="break-words text-xs text-slate-400">{detail}</p>
                {index < 8 && (
                  <span className="absolute -right-3 top-1/2 z-10 hidden -translate-y-1/2 text-slate-600 xl:block">
                    →
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>

        <section className="mt-6 rounded-2xl border border-amber-500/20 bg-slate-900 p-6">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-sm font-medium text-amber-400">LEARNING MEMORY</p>
              <h3 className="mt-1 text-xl font-bold">Evidence from completed experiments</h3>
            </div>
            <p className="text-sm text-slate-400">
              {learningTotal} stored learning record{learningTotal === 1 ? "" : "s"}
            </p>
          </div>

          {loadingLearning ? (
            <p className="mt-5 text-sm text-slate-400">Loading persisted experiment learning...</p>
          ) : learningError ? (
            <p className="mt-5 text-sm text-red-300">{learningError}</p>
          ) : learningRecords.length === 0 ? (
            <p className="mt-5 text-sm text-slate-400">
              <span className="block font-medium text-slate-200">
                No learning recorded yet
              </span>
              <span className="mt-1 block">
                Learning is stored only after an experiment has been evaluated.
              </span>
            </p>
          ) : (
            <div className="mt-5 grid gap-3 lg:grid-cols-2">
              {learningRecords.slice(0, 6).map((record) => (
                <div
                  key={record.experiment_id}
                  className="rounded-xl border border-slate-800 bg-slate-950/70 p-4"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-semibold text-white">{record.opportunity}</p>
                    <span className="rounded-full bg-amber-500/10 px-2.5 py-1 text-xs font-semibold text-amber-300">
                      {record.learning_signal}
                    </span>
                  </div>
                  <p className="mt-2 text-sm text-slate-300">
                    {record.decision}: {record.next_action}
                  </p>
                  <p className="mt-2 text-xs text-slate-400">
                    Original strategy: {record.original_strategy_action || "Unavailable"}
                  </p>
                  <p className="mt-1 text-xs text-slate-500">{record.hypothesis}</p>
                  <div className="mt-3 grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
                    <p className="text-slate-400">
                      Revenue growth{" "}
                      <span className="block font-semibold text-white">
                        {record.metrics.revenue_growth_percent == null
                          ? "Unavailable"
                          : `${record.metrics.revenue_growth_percent > 0 ? "+" : ""}${record.metrics.revenue_growth_percent}%`}
                      </span>
                    </p>
                    <p className="text-slate-400">
                      Conversion lift{" "}
                      <span className="block font-semibold text-white">
                        {record.metrics.conversion_lift == null
                          ? "Unavailable"
                          : `${record.metrics.conversion_lift > 0 ? "+" : ""}${record.metrics.conversion_lift} pp`}
                      </span>
                    </p>
                    <p className="text-slate-400">
                      Margin change{" "}
                      <span className="block font-semibold text-white">
                        {record.metrics.margin_change == null
                          ? "Unavailable"
                          : `${record.metrics.margin_change > 0 ? "+" : ""}${record.metrics.margin_change} pp`}
                      </span>
                    </p>
                  </div>
                  <p className="mt-2 text-xs text-slate-500">
                    {record.metrics.control_customers ?? "—"} control /{" "}
                    {record.metrics.treatment_customers ?? "—"} treatment customers
                    {" · "}
                    {record.metrics.observation_days ?? "—"} observed days
                  </p>
                  <p className="mt-2 text-xs text-slate-400">
                    Evidence confidence: {(record.confidence * 100).toFixed(0)}%
                  </p>
                  <p className="mt-1 text-xs text-slate-500">{record.confidence_basis}</p>
                  <p className="mt-2 text-xs text-slate-400">{record.signal_reason}</p>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* ---------------------------------------
            MAIN GRID
        --------------------------------------- */}

        <div className="mt-8 grid gap-6 lg:grid-cols-3">

          {/* ---------------------------------------
              OPPORTUNITIES
          --------------------------------------- */}

          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-6 lg:col-span-2">

            <div className="flex items-center justify-between">

              <div>
                <p className="text-sm text-blue-400">
                  AI OPPORTUNITY MAP
                </p>

                <h3 className="mt-1 text-xl font-bold">
                  Highest Impact Opportunities
                </h3>
              </div>

              <span className="rounded-full border border-slate-700 bg-slate-800/70 px-3 py-1 text-center text-xs font-medium text-slate-300">
                Commerce data analysis
              </span>

            </div>

            {loadingOpportunities ? (
              <p className="mt-6 text-slate-400">
                AI is analyzing opportunities...
              </p>
            ) : opportunityError ? (
              <p className="mt-6 text-sm text-red-300" role="alert">
                {opportunityError}
              </p>
            ) : opportunities.length > 0 ? (
              <div className="mt-6 space-y-4">

                {opportunities.map(
                  (item, index) => (

                    <div
                      key={index}
                      className="min-w-0 rounded-xl border border-slate-800 bg-gradient-to-br from-slate-950 to-slate-900 p-4 shadow-lg shadow-black/10 sm:p-5"
                    >

                      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">

                        <div className="min-w-0 flex-1">

                          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">

                            <div className="min-w-0">
                              <p className="break-words text-lg font-bold tracking-tight text-white">
                                {item.title}
                              </p>

                              <p className="mt-2 max-w-xl break-words text-sm leading-6 text-slate-300">
                                {item.recommended_action}
                              </p>
                              {item.evidence && (
                                <p className="mt-2 max-w-xl break-words text-xs leading-5 text-slate-500">
                                  {item.evidence}
                                </p>
                              )}
                            </div>

                            <span className="w-fit shrink-0 rounded-full border border-blue-400/20 bg-blue-500/10 px-3 py-1 text-xs font-semibold text-blue-400">
                              {item.priority}
                            </span>

                          </div>

                          <div className="mt-5 rounded-lg border border-green-500/20 bg-green-500/5 px-4 py-3">

                            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                              Estimated Potential Revenue
                            </p>

                            <p className="mt-1 break-words text-2xl font-bold tracking-tight text-green-400">
                              ₹
                              {item.potential_revenue.toLocaleString(
                                "en-IN"
                              )}
                            </p>

                          </div>

                          {(item.score !== undefined ||
                            item.affected_customers !== undefined ||
                            item.affected_orders !== undefined) && (
                            <p className="mt-3 text-xs text-slate-500">
                              {item.score !== undefined &&
                                `Score ${item.score}`}
                              {item.affected_customers !== undefined &&
                                ` · ${item.affected_customers} customers`}
                              {item.affected_orders !== undefined &&
                                ` · ${item.affected_orders} orders`}
                            </p>
                          )}

                          <button
                            onClick={() =>
                              void runDecision(
                                index,
                                item
                              )
                            }
                            disabled={
                              loadingDecision ===
                              index
                            }
                            className="mt-4 w-full whitespace-normal break-words rounded-lg bg-blue-600 px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-blue-950/30 transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {loadingDecision ===
                            index
                              ? "Analyzing..."
                              : "Run AI Decision"}
                          </button>
                          {decisionErrors[index] && (
                            <p className="mt-2 text-sm text-red-300" role="alert">
                              {decisionErrors[index]}
                            </p>
                          )}

                          <button
                            onClick={() => void requestNextAction(index, item)}
                            disabled={loadingNextAction === index}
                            className="mt-2 w-full whitespace-normal break-words rounded-lg border border-purple-500/30 bg-purple-500/10 px-4 py-2.5 text-sm font-semibold text-purple-200 transition hover:bg-purple-500/20 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {loadingNextAction === index
                              ? "Reviewing learning..."
                              : "Get Learning-Aware Next Action"}
                          </button>

                          {nextActionErrors[index] && (
                            <p className="mt-2 text-xs text-red-300">
                              {nextActionErrors[index]}
                            </p>
                          )}

                          {nextActionResults[index] && (
                            <div className="mt-4 rounded-lg border border-purple-500/20 bg-purple-500/5 p-4">
                              <p className="text-sm font-semibold text-purple-300">
                                Next Best Action · Recommendation only
                              </p>
                              <p className="mt-2 font-semibold text-white">
                                {nextActionResults[index].recommended_action.label}
                              </p>
                              <p className="mt-1 text-sm text-slate-300">
                                {nextActionResults[index].recommended_action.rationale}
                              </p>
                              <p className="mt-2 text-xs text-slate-300">
                                Hypothesis: {nextActionResults[index].hypothesis}
                              </p>
                              <p className="mt-1 text-xs text-slate-400">
                                Learning objective: {nextActionResults[index].expected_learning_objective}
                              </p>
                              <p className="mt-1 text-xs text-slate-400">
                                Suggested split: {nextActionResults[index].recommended_experiment_configuration.control.traffic_percent}% control /{" "}
                                {nextActionResults[index].recommended_experiment_configuration.treatment.traffic_percent}% treatment ·{" "}
                                {nextActionResults[index].recommended_experiment_configuration.guardrail_approved
                                  ? "Guardrails passed"
                                  : "Guardrail approval required"}
                              </p>
                              <p className="mt-2 text-xs text-slate-400">
                                AI decision: {nextActionResults[index].strategy_decision}
                                {" · "}
                                Prior signal: {nextActionResults[index].learning_signal}
                                {" · "}
                                {nextActionResults[index].total_historical_experiments} matching experiment(s)
                              </p>
                              {nextActionResults[index].confidence !== null && (
                                <p className="mt-1 text-xs text-slate-500">
                                  Historical evidence confidence:{" "}
                                  {(nextActionResults[index].confidence * 100).toFixed(0)}%
                                  {" · "}
                                  {nextActionResults[index].confidence_basis}
                                </p>
                              )}
                              <p className="mt-2 text-xs text-slate-500">
                                No campaign or experiment was executed by this recommendation.
                              </p>
                            </div>
                          )}

                          {/* ---------------------------------------
                            AI DECISION RESULT
                          --------------------------------------- */}

                          {decisionResults[
                            index
                          ] && (
                            <div className="mt-4 min-w-0 rounded-lg border border-slate-700 bg-slate-900 p-4">

                              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                                AI Decision
                              </p>

                              <span className={`mt-2 inline-flex rounded-full px-3 py-1 text-sm font-bold ${
                                decisionResults[index].decision === "APPROVE"
                                  ? "bg-emerald-500/10 text-emerald-300"
                                  : "bg-amber-500/10 text-amber-300"
                              }`}>
                                {
                                  decisionResults[
                                    index
                                  ].decision
                                }
                              </span>

                              <p className="mt-2 text-sm text-slate-400">
                                Risk:{" "}
                                {
                                  decisionResults[
                                    index
                                  ].risk
                                }
                              </p>

                              <p className="mt-1 text-sm text-slate-400">
                                Action:{" "}
                                {
                                  decisionResults[
                                    index
                                  ]
                                    .recommended_action
                                }
                              </p>

                              {decisionResults[
                                index
                              ].reason && (
                                <p className="mt-2 text-sm text-slate-400">
                                  Reason:{" "}
                                  {
                                    decisionResults[
                                      index
                                    ].reason
                                  }
                                </p>
                              )}

                              {decisionResults[
                                index
                              ].decision ===
                                "APPROVE" && (
                                <p className="mt-2 text-sm font-semibold text-green-400">
                                  ✓ Approved for
                                  controlled
                                  experiment
                                </p>
                              )}

                              {decisionResults[
                                index
                              ].decision ===
                                "REJECT" && (
                                <p className="mt-2 text-sm font-semibold text-red-400">
                                  🛑 Blocked by
                                  Merchant
                                  Guardrail
                                </p>
                              )}

                              {decisionResults[
                                index
                              ].decision ===
                                "REVIEW" && (
                                <p className="mt-2 text-sm font-semibold text-yellow-400">
                                  ⚠️ Merchant
                                  approval required
                                </p>
                              )}

                              {decisionResults[
                                index
                              ]
                                .approval_required && (
                                <p className="mt-2 text-sm text-yellow-400">
                                  Merchant approval
                                  required
                                </p>
                              )}

                              {evaluationResults[index] &&
                                (() => {
                                  const evaluation = parseExperimentEvaluation(
                                    evaluationResults[index]?.evaluation
                                  );

                                  return (
                                    <div className="mt-4 border-t border-slate-800 pt-4">
                                      <p className="text-sm font-semibold text-purple-400">
                                        AI Result Evaluation
                                      </p>

                                      <div className="mt-3 space-y-3 text-sm">
                                        <div>
                                          <p className="text-slate-500">
                                            Decision
                                          </p>
                                          <p className={`mt-1 inline-flex rounded-full px-3 py-1 font-semibold ${
                                            evaluation?.decision === "CONTINUE"
                                              ? "bg-amber-500/10 text-amber-300"
                                              : evaluation?.decision === "STOP"
                                                ? "bg-rose-500/10 text-rose-300"
                                                : "bg-emerald-500/10 text-emerald-300"
                                          }`}>
                                            {evaluation?.decision}
                                          </p>
                                          {evaluation?.decision === "CONTINUE" && (
                                            <p className="mt-2 break-words text-slate-400">
                                              {evaluation.reason?.includes("7-day observation window")
                                                ? "The observation window is not complete, so GrowthPilot is continuing to observe rather than declaring a winner."
                                                : "The experiment is continuing; no final winner has been declared."}
                                            </p>
                                          )}
                                        </div>

                                        <div>
                                          <p className="text-slate-500">
                                            Reason
                                          </p>
                                          <p className="break-words font-semibold">
                                            {evaluation?.reason}
                                          </p>
                                        </div>

                                        <div>
                                          <p className="text-slate-500">
                                            Next Action
                                          </p>
                                          <p className="break-words font-semibold">
                                            {evaluation?.next_action}
                                          </p>
                                        </div>
                                      </div>
                                    </div>
                                  );
                                })()}

                            </div>
                          )}

                          {/* ---------------------------------------
                              EXPERIMENT RESULT
                          --------------------------------------- */}

                          {experimentResults[
                            index
                          ] && (
                            <div className="mt-4 min-w-0 rounded-lg border border-green-500/20 bg-green-500/5 p-4">

                              <div className="flex flex-wrap items-center justify-between gap-3">
                                <p className="text-sm font-semibold text-green-400">
                                  Experiment
                                </p>
                                <button
                                  type="button"
                                  onClick={() => void refreshExperiment(index)}
                                  disabled={refreshingExperiment[index]}
                                  className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  {refreshingExperiment[index]
                                    ? "Refreshing..."
                                    : "Refresh status & measurement"}
                                </button>
                              </div>
                              {experimentRefreshErrors[index] && (
                                <p className="mt-2 text-xs text-amber-300" role="alert">
                                  {experimentRefreshErrors[index]}
                                </p>
                              )}

                              <div className="mt-2 flex flex-wrap gap-2">
                                <span
                                  className={`rounded-full px-3 py-1 text-xs font-semibold ${
                                    experimentResults[index].experiment_status ===
                                    "RUNNING"
                                      ? "bg-emerald-500/10 text-emerald-300"
                                      : "bg-slate-500/10 text-slate-300"
                                  }`}
                                >
                                  {experimentResults[index].experiment_status ||
                                    "Status unavailable"}
                                </span>
                                {measurementResults[index] && (
                                  <span className="rounded-full bg-cyan-500/10 px-3 py-1 text-xs font-semibold text-cyan-200">
                                    {formatMeasurementStatus(
                                      measurementResults[index].metrics?.sample_status
                                    )}
                                  </span>
                                )}
                              </div>

                              <div className="mt-3 grid min-w-0 grid-cols-1 gap-3 text-sm sm:grid-cols-2">

                                <div className="min-w-0">
                                  <p className="text-slate-500">
                                    Treatment
                                  </p>
                                  <p className="break-words font-semibold">
                                    {
                                      experimentResults[
                                        index
                                      ]
                                        .test_group
                                    }
                                  </p>
                                </div>

                                <div className="min-w-0">
                                  <p className="text-slate-500">
                                    Control / Holdout
                                  </p>
                                  <p className="break-words font-semibold">
                                    {
                                      experimentResults[
                                        index
                                      ]
                                        .holdout_group
                                    }
                                  </p>
                                </div>

                                <div className="min-w-0">
                                  <p className="text-slate-500">
                                    Status
                                  </p>
                                  <p
                                    className={`break-words font-semibold ${
                                      experimentResults[index]
                                        .experiment_status === "RUNNING"
                                        ? "text-green-400"
                                        : "text-slate-300"
                                    }`}
                                  >
                                    {
                                      experimentResults[index]
                                        .experiment_status ||
                                      "Unavailable"
                                    }
                                  </p>
                                </div>

                                <div className="min-w-0">
                                  <p className="text-slate-500">
                                    Experiment ID
                                  </p>
                                  <p className="break-all font-semibold">
                                    {
                                      experimentResults[
                                        index
                                      ]
                                        .experiment_id
                                    }
                                  </p>
                                </div>

                                <div className="min-w-0">
                                  <p className="text-slate-500">
                                    Assigned Customers
                                  </p>
                                  <p className="break-words font-semibold">
                                    {experimentResults[index]
                                      .assigned_customers?.treatment ?? "Unavailable"}{" "}
                                    treatment /{" "}
                                    {experimentResults[index]
                                      .assigned_customers?.control ?? "Unavailable"}{" "}
                                    control
                                  </p>
                                </div>

                                <div className="min-w-0">
                                  <p className="text-slate-500">
                                    Measurement Status
                                  </p>
                                  <p className="mt-1 break-words font-semibold text-cyan-200">
                                    {formatMeasurementStatus(
                                      measurementResults[index]?.metrics?.sample_status ||
                                        experimentResults[index].measurement_status
                                    )}
                                  </p>
                                </div>

                                {experimentResults[index].started_at && (
                                  <div className="min-w-0">
                                    <p className="text-slate-500">
                                      Started
                                    </p>
                                    <p className="break-words font-semibold">
                                      {new Date(
                                        experimentResults[index].started_at!
                                      ).toLocaleString()}
                                    </p>
                                  </div>
                                )}
                                {experimentResults[index].completed_at && (
                                  <div className="min-w-0">
                                    <p className="text-slate-500">
                                      Completed
                                    </p>
                                    <p className="break-words font-semibold">
                                      {new Date(
                                        experimentResults[index].completed_at
                                      ).toLocaleString()}
                                    </p>
                                  </div>
                                )}

                              </div>

                              {experimentResults[index].hypothesis && (
                                <p className="mt-3 text-sm text-slate-300">
                                  <span className="text-slate-500">
                                    Hypothesis:{" "}
                                  </span>
                                  {experimentResults[index].hypothesis}
                                </p>
                              )}

                              {/* ---------------------------------------
                                  MEASUREMENT RESULT
                              --------------------------------------- */}

                              {measurementResults[
                                index
                              ] && (
                                <div className="mt-4 border-t border-slate-800 pt-4">

                                  <div className="flex flex-wrap items-center justify-between gap-2">
                                    <p className="text-sm font-semibold text-blue-300">
                                    Measurement
                                    </p>
                                    {staleMeasurementResults[index] && (
                                      <span className="rounded-full bg-amber-500/10 px-3 py-1 text-xs font-semibold text-amber-300">
                                        Previous measurement · refresh failed
                                      </span>
                                    )}
                                    {measurementResults[index].metrics?.sample_status ===
                                      "insufficient_observation_window" && (
                                      <span className="rounded-full bg-amber-500/10 px-3 py-1 text-xs font-semibold text-amber-300">
                                        Collecting experiment evidence
                                      </span>
                                    )}
                                  </div>
                                  {(() => {
                                    const progress = getObservationProgress(
                                      measurementResults[index].metrics
                                    );
                                    if (!progress) {
                                      return (
                                        <p className="mt-3 text-xs text-amber-300">
                                          Observation progress unavailable.
                                        </p>
                                      );
                                    }

                                    return (
                                      <div className="mt-3">
                                        <div className="flex flex-wrap justify-between gap-2 text-xs text-slate-400">
                                          <span>Observation window elapsed</span>
                                          <span>
                                            {progress.observationDays.toFixed(2)}{" "}
                                            days elapsed
                                            {progress.minimumDays > 0
                                              ? ` / ${progress.minimumDays} days`
                                              : " · no minimum duration configured"}
                                          </span>
                                        </div>
                                        {progress.percentComplete !== null && (
                                          <div
                                            className="mt-2 h-2 overflow-hidden rounded-full bg-slate-800"
                                            role="progressbar"
                                            aria-label="Observation window progress"
                                            aria-valuemin={0}
                                            aria-valuemax={100}
                                            aria-valuenow={progress.percentComplete}
                                          >
                                            <div
                                              className="h-full rounded-full bg-cyan-400"
                                              style={{
                                                width: `${progress.percentComplete}%`,
                                              }}
                                            />
                                          </div>
                                        )}
                                      </div>
                                    );
                                  })()}
                                  {measurementResults[index].metrics && (
                                    <div className="mt-3 space-y-1 text-xs text-slate-400">
                                      <p>
                                        Observed orders:{" "}
                                        {measurementResults[index].metrics
                                          ?.control_orders ?? "Unavailable"}{" "}
                                        control /{" "}
                                        {measurementResults[index].metrics
                                          ?.treatment_orders ?? "Unavailable"}{" "}
                                        treatment
                                      </p>
                                      <p>
                                        Observed margin: control{" "}
                                        {formatObservedMargin(
                                          measurementResults[index].metrics
                                            ?.control_margin_percent
                                        )}{" "}
                                        / treatment{" "}
                                        {formatObservedMargin(
                                          measurementResults[index].metrics
                                            ?.treatment_margin_percent
                                        )}
                                      </p>
                                      {measurementResults[index].metrics
                                        ?.sample_issues &&
                                        measurementResults[index].metrics
                                          .sample_issues!.length > 0 && (
                                          <p className="text-amber-300">
                                            Measurement evidence needed:{" "}
                                            {measurementResults[
                                              index
                                            ].metrics!.sample_issues!.join("; ")}.
                                          </p>
                                        )}
                                      {measurementResults[index].metrics
                                        ?.control_orders === 0 &&
                                        measurementResults[index].metrics
                                          ?.treatment_orders === 0 && (
                                          <p className="text-amber-300">
                                            No qualifying completed orders have
                                            been observed in either arm.
                                          </p>
                                        )}
                                      {measurementResults[index].metrics
                                        ?.treatment_margin_percent == null && (
                                        <p className="text-amber-300">
                                          Treatment margin evidence is
                                          unavailable from observed orders.
                                        </p>
                                      )}
                                    </div>
                                  )}
                                  {measurementResults[index].metrics?.sample_status ===
                                    "insufficient_observation_window" && (
                                    <p className="mt-2 text-sm text-slate-400">
                                      Waiting for the observation window to complete.
                                    </p>
                                  )}

                                  <div className="mt-3 grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">

                                    <div className="min-w-0">
                                      <p className="text-xs text-slate-500">
                                        Incremental Revenue
                                      </p>

                                      <p className="mt-1 break-words font-bold text-green-400">
                                        {measurementResults[index]
                                          .incremental_revenue === null
                                          ? "Insufficient data"
                                          : `₹${measurementResults[
                                              index
                                            ].incremental_revenue?.toLocaleString(
                                              "en-IN"
                                            )}`}
                                      </p>
                                    </div>

                                    <div className="min-w-0">
                                      <p className="text-xs text-slate-500">
                                        Revenue Growth
                                      </p>

                                      <p className="mt-1 break-words font-bold">
                                        {measurementResults[index]
                                          .revenue_growth_percent === null
                                          ? "Insufficient data"
                                          : `${
                                              measurementResults[index]
                                                .revenue_growth_percent! > 0
                                                ? "+"
                                                : ""
                                            }${
                                              measurementResults[index]
                                                .revenue_growth_percent
                                            }%`}
                                      </p>
                                    </div>

                                    <div className="min-w-0">
                                      <p className="text-xs text-slate-500">
                                        Conversion Lift
                                      </p>

                                      <p className="mt-1 break-words font-bold">
                                        {measurementResults[index]
                                          .conversion_lift === null
                                          ? "Insufficient data"
                                          : `${
                                              measurementResults[index]
                                                .conversion_lift! > 0
                                                ? "+"
                                                : ""
                                            }${
                                              measurementResults[index]
                                                .conversion_lift
                                            } pp`}
                                      </p>
                                    </div>

                                    <div className="min-w-0">
                                      <p className="text-xs text-slate-500">
                                        Margin Change
                                      </p>

                                      <p className="mt-1 break-words font-bold">
                                        {measurementResults[index]
                                          .margin_change === null
                                          ? "Insufficient data"
                                          : `${
                                              measurementResults[index]
                                                .margin_change! > 0
                                                ? "+"
                                                : ""
                                            }${
                                              measurementResults[index]
                                                .margin_change
                                            } pp`}
                                      </p>
                                    </div>

                                    {measurementResults[index].metrics
                                      ?.sample_status ===
                                      "insufficient_sample" && (
                                      <p className="mt-3 break-words text-xs text-amber-300">
                                        More assigned customers are needed:{" "}
                                        {measurementResults[index].metrics
                                          ?.sample_issues?.join("; ") ||
                                          "collect additional experiment data"}
                                        .
                                      </p>
                                    )}

                                  </div>

                                </div>
                              )}

                            </div>
                          )}

                        </div>

                      </div>

                    </div>
                  )
                )}

              </div>
            ) : (
              <div className="mt-6 rounded-xl border border-slate-800 bg-slate-950 p-4">
                <p className="text-sm text-slate-400">
                  No opportunities yet. Seed local commerce data to discover demo opportunities.
                </p>
                <button
                  onClick={async () => {
                    setSeedingDemoData(true);
                    setDemoSeedMessage("");

                    try {
                      const response = await fetch(
                        apiUrl("/api/demo/seed"),
                        { method: "POST" }
                      );

                      if (!response.ok) {
                        throw new Error("Failed to seed demo data");
                      }

                      const seedData = await response.json();
                      await loadOpportunities();
                      setDemoSeedMessage(
                        seedData.customers_created > 0
                          ? `Created ${seedData.customers_created} demo customers and ${seedData.orders_created} orders.`
                          : seedData.message
                      );
                    } catch (error) {
                      console.error("Demo data seeding error:", error);
                      setDemoSeedMessage(
                        "Could not seed demo data. Check that the backend is running."
                      );
                    } finally {
                      setSeedingDemoData(false);
                    }
                  }}
                  disabled={seedingDemoData}
                  className="mt-4 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {seedingDemoData
                    ? "Seeding demo data..."
                    : "Seed Demo Commerce Data"}
                </button>
                {demoSeedMessage && (
                  <p className="mt-3 text-xs text-slate-400">
                    {demoSeedMessage}
                  </p>
                )}
              </div>
            )}

          </div>

          {/* ---------------------------------------
              AI AUTOPILOT STATUS
          --------------------------------------- */}

          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-6">

            <p className="text-sm text-purple-400">
              AUTOPILOT WORKFLOW
            </p>

            <h3 className="mt-1 text-xl font-bold">
              Current Activity
            </h3>

            <div className="mt-6 space-y-5">

              <Status
                title="Data Analysis"
                text={
                  loadingOpportunities
                    ? "Analyzing commerce events"
                    : opportunities.length > 0
                    ? "Commerce events analyzed"
                    : "Waiting for commerce data"
                }
                done={!loadingOpportunities && opportunities.length > 0}
                active={loadingOpportunities}
              />

              <Status
                title="Opportunity Detection"
                text={`${opportunities.length} opportunities found`}
                done={!loadingOpportunities && opportunities.length > 0}
                active={loadingOpportunities}
              />

              <Status
                title="Strategy Selection"
                text={
                  decisionResults[0]
                    ? `AI decision: ${decisionResults[0].decision}`
                    : "Waiting for AI decision"
                }
                done={!!decisionResults[0]}
                active={loadingDecision === 0 && !decisionResults[0]}
              />

              <Status
                title="Guardrails"
                text={
                  decisionResults[0]
                    ? decisionResults[0].decision === "APPROVE"
                      ? "Merchant constraints passed"
                      : "Strategy not approved for launch"
                    : "Waiting for strategy checks"
                }
                done={decisionResults[0]?.decision === "APPROVE"}
                warning={
                  !!decisionResults[0] &&
                  decisionResults[0].decision !== "APPROVE"
                }
                active={loadingDecision === 0 && !decisionResults[0]}
              />

              <Status
                title="Experiment"
                text={
                  experimentLaunchErrors[0]
                    ? `Not launched: ${experimentLaunchErrors[0]}`
                    : experimentResults[0]
                    ? `Experiment ${experimentResults[0].experiment_status || "launched"}`
                    : "Waiting for approved strategy"
                }
                done={
                  !experimentLaunchErrors[0] &&
                  ["COMPLETED", "STOPPED"].includes(
                    experimentResults[0]?.experiment_status || ""
                  )
                }
                warning={!!experimentLaunchErrors[0]}
                active={
                  !experimentLaunchErrors[0] &&
                  !!experimentResults[0] &&
                  !["COMPLETED", "STOPPED"].includes(
                    experimentResults[0].experiment_status || ""
                  )
                }
              />

              <Status
                title="Measurement"
                text={
                  measurementResults[0]
                    ? formatMeasurementStatus(
                        measurementResults[0].metrics?.sample_status
                      )
                    : "Waiting for experiment measurement"
                }
                done={!!measurementResults[0]}
                active={
                  measurementResults[0]?.metrics?.sample_status ===
                  "insufficient_observation_window"
                }
              />

              <Status
                title="AI Evaluation"
                text={
                  loadingEvaluation === 0
                    ? "Evaluating observed results"
                    : autopilotEvaluation?.decision
                    ? `Evaluation: ${autopilotEvaluation.decision}`
                    : "Waiting for sufficient observed results"
                }
                done={autopilotEvaluation?.decision === "STOP" ||
                  autopilotEvaluation?.decision === "OPTIMIZE"}
                warning={autopilotEvaluation?.decision === "CONTINUE"}
                active={loadingEvaluation === 0}
              />

              <Status
                title="Learning Memory"
                text={
                  autopilotLearningRecord
                    ? `${autopilotLearningRecord.learning_signal} signal stored`
                    : "No learning recorded for this run"
                }
                done={!!autopilotLearningRecord}
              />

              <Status
                title="Next Best Action"
                text={
                  nextActionResults[0]
                    ? nextActionResults[0].recommended_action.label
                    : "Recommendation not requested"
                }
                done={!!nextActionResults[0]}
                active={loadingNextAction === 0}
              />

            </div>

          </div>

        </div>

        {/* ---------------------------------------
            BOTTOM GRID
        --------------------------------------- */}

        <div className="mt-8 grid gap-6 lg:grid-cols-2">

          {/* POLICY */}

          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-6">

            <p className="text-sm text-orange-400">
              POLICY & GUARDRAILS
            </p>

            <h3 className="mt-1 text-xl font-bold">
              Safety Check
            </h3>

            <div className="mt-5 space-y-3 text-sm">

              <Check
                text={`Maximum discount: within ${maxDiscount}% limit`}
              />

              <Check
                text={`Campaign budget: within ₹${budget.toLocaleString(
                  "en-IN"
                )} limit`}
              />

              <Check
                text={`Minimum margin requirement: ${minMargin}%`}
              />

              <div className="flex min-w-0 gap-3 text-sm">
                <span className="mt-0.5 h-2.5 w-2.5 shrink-0 rounded-full bg-slate-500" />
                <div className="min-w-0">
                  <p className="font-medium text-slate-300">
                    Inventory data unavailable
                  </p>
                  <p className="mt-1 break-words text-xs text-slate-500">
                    No inventory integration is connected.
                  </p>
                </div>
              </div>

            </div>

          </div>

          {/* MEASUREMENT / GOAL PROGRESS */}

          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-6">

            <p className="text-sm text-green-400">
              MEASUREMENT ENGINE
            </p>

            <h3 className="mt-1 text-xl font-bold">
              Goal Progress
            </h3>

            <div className="mt-6">

              <div className="mb-2 flex justify-between text-sm">

                <span className="text-slate-400">
                  Revenue Growth
                </span>

                <span className="max-w-[55%] text-right font-bold text-green-400">
                  {!isWaitingForObservation &&
                  lastMeasurement?.revenue_growth_percent !== null &&
                  lastMeasurement?.revenue_growth_percent !== undefined
                    ? `+${lastMeasurement.revenue_growth_percent}%`
                    : "Awaiting observed data"}{" "}
                  / {goal}%
                </span>

              </div>

              <div className="h-3 overflow-hidden rounded-full bg-slate-800">

                <div
                  className="h-full rounded-full bg-green-500 transition-all duration-700"
                  style={{
                    width: `${goalProgress}%`,
                  }}
                />

              </div>

              <p className="mt-4 text-sm text-slate-400">

                {!isWaitingForObservation &&
                lastMeasurement?.revenue_growth_percent !== null &&
                lastMeasurement?.revenue_growth_percent !== undefined
                  ? lastMeasurement.revenue_growth_percent >= goal
                    ? "🎯 Revenue growth goal achieved. AI can continue optimizing the winning strategy."
                    : "AI is measuring campaign performance and working toward the merchant growth target."
                  : waitingForObservationCopy}

              </p>

            </div>

          </div>

        </div>

      </section>

    </main>
  );
}

// ---------------------------------------
// METRIC CARD
// ---------------------------------------

function MetricCard({
  title,
  value,
  change,
  waiting = false,
}: {
  title: string;
  value: string;
  change: string;
  waiting?: boolean;
}) {
  return (
    <div className="min-w-0 rounded-2xl border border-slate-800 bg-slate-900 p-4 sm:p-5">

      <p className="break-words text-sm text-slate-400">
        {title}
      </p>

      <div className="mt-3 flex min-w-0 flex-col items-start gap-2">

        <h3 className={`max-w-full break-words text-xl font-bold leading-tight sm:text-2xl ${
          waiting ? "text-amber-200" : "text-white"
        }`}>
          {value}
        </h3>

        <span className="max-w-full break-words text-xs leading-5 text-slate-400 sm:text-sm">
          {change}
        </span>

      </div>

    </div>
  );
}

// ---------------------------------------
// STATUS
// ---------------------------------------

function Status({
  title,
  text,
  done,
  active,
  warning,
}: {
  title: string;
  text: string;
  done?: boolean;
  active?: boolean;
  warning?: boolean;
}) {
  return (
    <div className="flex gap-3">

      <div
        className={`mt-1 h-3 w-3 rounded-full ${
          done
            ? "bg-green-400"
            : warning
            ? "bg-amber-400"
            : active
            ? "animate-pulse bg-blue-400"
            : "bg-slate-600"
        }`}
      />

      <div>

        <p className="font-medium">
          {title}
        </p>

        <p className="text-sm text-slate-400">
          {text}
        </p>

      </div>

    </div>
  );
}

// ---------------------------------------
// CHECK
// ---------------------------------------

function Check({
  text,
}: {
  text: string;
}) {
  return (
    <div className="flex gap-3">

      <span className="font-bold text-green-400">
        ✓
      </span>

      <span className="text-slate-300">
        {text}
      </span>

    </div>
  );
}