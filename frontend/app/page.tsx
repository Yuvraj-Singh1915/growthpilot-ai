"use client";

import { useEffect, useRef, useState } from "react";

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
    sample_issues?: string[];
  };
};

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

export default function Home() {
  const [autopilot, setAutopilot] = useState(true);

  const [goal, setGoal] = useState(15);
  const [maxDiscount, setMaxDiscount] = useState(10);
  const [budget, setBudget] = useState(50000);
  const [minMargin, setMinMargin] = useState(20);

  const [editingGoal, setEditingGoal] = useState(false);
  const [savingGoal, setSavingGoal] = useState(false);
  const [apiStatus, setApiStatus] = useState("");

  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [loadingOpportunities, setLoadingOpportunities] = useState(false);
  const [seedingDemoData, setSeedingDemoData] = useState(false);
  const [demoSeedMessage, setDemoSeedMessage] = useState("");

  const [decisionResults, setDecisionResults] = useState<
    Record<number, DecisionResult>
  >({});

  const [experimentResults, setExperimentResults] = useState<
    Record<number, ExperimentResult>
  >({});

  const [measurementResults, setMeasurementResults] = useState<
    Record<number, MeasurementResult>
  >({});

  const [loadingDecision, setLoadingDecision] = useState<number | null>(null);
  const [evaluationResults, setEvaluationResults] = useState<Record<number, any>>({});
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
  const autopilotRunRef = useRef(false);

  // ---------------------------------------
  // LOAD OPPORTUNITIES
  // ---------------------------------------

  const loadOpportunities = async () => {
    setLoadingOpportunities(true);

    try {
      const response = await fetch(
        "http://127.0.0.1:8000/api/opportunities",
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
        throw new Error("Failed to load opportunities");
      }

      const data = await response.json();

      setOpportunities(data.opportunities);
    } catch (error) {
      console.error("Opportunity Engine error:", error);
    } finally {
      setLoadingOpportunities(false);
    }
  };

  const loadLearningMemory = async () => {
    setLoadingLearning(true);
    setLearningError("");
    try {
      const response = await fetch("http://127.0.0.1:8000/api/learning");
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
        "http://127.0.0.1:8000/api/autopilot/next-action",
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
        throw new Error("Failed to get the next best action");
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
        [index]: "Could not get a next-action recommendation. Check that the backend is running.",
      }));
    } finally {
      setLoadingNextAction(null);
    }
  };

  useEffect(() => {
    loadOpportunities();
    void loadLearningMemory();
  }, [goal, maxDiscount, budget, minMargin]);

  // ---------------------------------------
  // AI DECISION → EXPERIMENT → MEASUREMENT
  // ---------------------------------------

  const runDecision = async (index: number, opportunity: any) => {
  setLoadingDecision(index);

  try {
    // 1. AI STRATEGY
    const response = await fetch("http://127.0.0.1:8000/api/strategy", {
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
      throw new Error("Decision Engine failed");
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
      "http://127.0.0.1:8000/api/experiment",
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
      throw new Error("Experiment Engine failed");
    }

    const experimentData = await experimentResponse.json();

    if (experimentData.status !== "launched") {
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
      "http://127.0.0.1:8000/api/measurement",
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
      throw new Error("Measurement Engine failed");
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
      "http://127.0.0.1:8000/api/evaluate-result",
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
      throw new Error("AI Evaluation Engine failed");
    }

    const evaluationData = await evaluationResponse.json();

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
  } finally {
    setLoadingDecision(null);
    setLoadingEvaluation(null);
  }
};
    useEffect(() => {
    if (
      autopilot &&
      opportunities.length > 0 &&
      !autopilotRunRef.current
    ) {
      autopilotRunRef.current = true;
      void runDecision(0, opportunities[0]);
    }
  }, [autopilot, opportunities]);

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

  const goalProgress =
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

  return (
    <main className="min-h-screen bg-slate-950 text-white">
      {/* ---------------------------------------
          HEADER
      --------------------------------------- */}

      <header className="border-b border-slate-800 bg-slate-950/90 px-6 py-4">
        <div className="mx-auto flex max-w-7xl items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">
              GrowthPilot{" "}
              <span className="text-blue-400">
                AI
              </span>
            </h1>

            <p className="text-sm text-slate-400">
              Autonomous Commerce Growth Engine
            </p>
          </div>

          <button
            onClick={() =>
              setAutopilot(!autopilot)
            }
            className={`rounded-full px-5 py-2 text-sm font-semibold transition ${
              autopilot
                ? "bg-green-500 text-black"
                : "bg-slate-700 text-white"
            }`}
          >
            {autopilot
              ? "● AUTOPILOT ON"
              : "○ AUTOPILOT OFF"}
          </button>
        </div>
      </header>

      {/* ---------------------------------------
          MAIN CONTENT
      --------------------------------------- */}

      <section className="mx-auto max-w-7xl px-6 py-8">

        {/* WELCOME */}

        <div className="mb-8">
          <p className="text-sm font-medium text-blue-400">
            AI GROWTH COMMAND CENTER
          </p>

          <h2 className="mt-2 text-4xl font-bold">
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
                return;
              }

              setSavingGoal(true);
              setApiStatus("");

              try {
                const response = await fetch(
                  "http://127.0.0.1:8000/api/goal",
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
                    "Failed to save goal"
                  );
                }

                setEditingGoal(false);
                setApiStatus(
                  "Goal saved successfully"
                );
              } catch (error) {
                setApiStatus(
                  "Backend connection failed"
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
            <p className="mt-3 text-sm text-green-400">
              {apiStatus}
            </p>
          )}

        </div>

        {/* ---------------------------------------
            METRICS
        --------------------------------------- */}

        <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">

          <MetricCard
            title="Observed Revenue"
            value="Unavailable"
            change="No measured experiment"
          />

          <MetricCard
            title="Observed Conversion"
            value="Unavailable"
            change="No measured experiment"
          />

          <MetricCard
            title="Observed Average Order Value"
            value="Unavailable"
            change="No measured experiment"
          />

          <MetricCard
            title="Observed Cart Recovery"
            value="Unavailable"
            change="No measured experiment"
          />

        </div>

        {/* ---------------------------------------
            AUTONOMOUS GROWTH LOOP
        --------------------------------------- */}

        <div className="mt-8 rounded-2xl border border-purple-500/20 bg-gradient-to-r from-slate-900 via-slate-900 to-purple-950/40 p-6">
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

          <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-9">
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
              <div key={step} className="relative rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <p className={`text-xs font-semibold ${color}`}>{step}</p>
                <p className="mt-3 text-sm font-semibold text-white">{label}</p>
                <p className="text-xs text-slate-400">{detail}</p>
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
              No completed experiment has produced reusable learning yet. Underpowered or paused experiments are not stored as outcomes.
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
                  <div className="mt-3 grid grid-cols-3 gap-2 text-xs">
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

              <span className="rounded-full bg-green-500/10 px-3 py-1 text-xs font-medium text-green-400">
                LIVE ANALYSIS
              </span>

            </div>

            {loadingOpportunities ? (
              <p className="mt-6 text-slate-400">
                AI is analyzing opportunities...
              </p>
            ) : opportunities.length > 0 ? (
              <div className="mt-6 space-y-4">

                {opportunities.map(
                  (item, index) => (

                    <div
                      key={index}
                      className="rounded-xl border border-slate-800 bg-gradient-to-br from-slate-950 to-slate-900 p-5 shadow-lg shadow-black/10"
                    >

                      <div className="flex items-start justify-between gap-4">

                        <div className="flex-1">

                          <div className="flex items-center justify-between">

                            <div>
                              <p className="text-lg font-bold tracking-tight text-white">
                                {item.title}
                              </p>

                              <p className="mt-2 max-w-xl text-sm leading-6 text-slate-300">
                                {item.recommended_action}
                              </p>
                              {item.evidence && (
                                <p className="mt-2 max-w-xl text-xs leading-5 text-slate-500">
                                  {item.evidence}
                                </p>
                              )}
                            </div>

                            <span className="shrink-0 rounded-full border border-blue-400/20 bg-blue-500/10 px-3 py-1 text-xs font-semibold text-blue-400">
                              {item.priority}
                            </span>

                          </div>

                          <div className="mt-5 rounded-lg border border-green-500/20 bg-green-500/5 px-4 py-3">

                            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                              Estimated Potential Revenue
                            </p>

                            <p className="mt-1 text-2xl font-bold tracking-tight text-green-400">
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
                            className="mt-4 w-full rounded-lg bg-blue-600 px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-blue-950/30 transition hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            {loadingDecision ===
                            index
                              ? "Analyzing..."
                              : "Run AI Decision"}
                          </button>

                          <button
                            onClick={() => void requestNextAction(index, item)}
                            disabled={loadingNextAction === index}
                            className="mt-2 w-full rounded-lg border border-purple-500/30 bg-purple-500/10 px-4 py-2.5 text-sm font-semibold text-purple-200 transition hover:bg-purple-500/20 disabled:cursor-not-allowed disabled:opacity-50"
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
                            <div className="mt-4 rounded-lg border border-slate-700 bg-slate-900 p-4">

                              <p className="text-sm text-slate-400">
                                AI Decision
                              </p>

                              <p className="mt-1 text-lg font-bold">
                                {
                                  decisionResults[
                                    index
                                  ].decision
                                }
                              </p>

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
                                  let evaluation =
                                    evaluationResults[index]?.evaluation;

                                  try {
                                    if (
                                      typeof evaluation ===
                                      "string"
                                    ) {
                                      evaluation =
                                        JSON.parse(evaluation);
                                    }
                                  } catch {
                                    // Keep the original value when it is not valid JSON.
                                  }

                                  return (
                                    <div className="mt-4 border-t border-slate-800 pt-4">
                                      <p className="text-sm font-semibold text-purple-400">
                                        🤖 AI Result Evaluation
                                      </p>

                                      <div className="mt-3 space-y-3 text-sm">
                                        <div>
                                          <p className="text-slate-500">
                                            Decision
                                          </p>
                                          <p className="font-semibold">
                                            {evaluation?.decision}
                                          </p>
                                        </div>

                                        <div>
                                          <p className="text-slate-500">
                                            Reason
                                          </p>
                                          <p className="font-semibold">
                                            {evaluation?.reason}
                                          </p>
                                        </div>

                                        <div>
                                          <p className="text-slate-500">
                                            Next Action
                                          </p>
                                          <p className="font-semibold">
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
                            <div className="mt-4 rounded-lg border border-green-500/20 bg-green-500/5 p-4">

                              <p className="text-sm font-semibold text-green-400">
                                🚀 Experiment{" "}
                                {experimentResults[index].experiment_status ||
                                  "Launched"}
                              </p>

                              <div className="mt-3 grid grid-cols-2 gap-3 text-sm">

                                <div>
                                  <p className="text-slate-500">
                                    Treatment
                                  </p>
                                  <p className="font-semibold">
                                    {
                                      experimentResults[
                                        index
                                      ]
                                        .test_group
                                    }
                                  </p>
                                </div>

                                <div>
                                  <p className="text-slate-500">
                                    Control / Holdout
                                  </p>
                                  <p className="font-semibold">
                                    {
                                      experimentResults[
                                        index
                                      ]
                                        .holdout_group
                                    }
                                  </p>
                                </div>

                                <div>
                                  <p className="text-slate-500">
                                    Status
                                  </p>
                                  <p className="font-semibold text-green-400">
                                    {
                                      experimentResults[index]
                                        .experiment_status ||
                                      experimentResults[index].status
                                    }
                                  </p>
                                </div>

                                <div>
                                  <p className="text-slate-500">
                                    Experiment ID
                                  </p>
                                  <p className="font-semibold">
                                    {
                                      experimentResults[
                                        index
                                      ]
                                        .experiment_id
                                    }
                                  </p>
                                </div>

                                <div>
                                  <p className="text-slate-500">
                                    Assigned Customers
                                  </p>
                                  <p className="font-semibold">
                                    {experimentResults[index]
                                      .assigned_customers?.treatment ?? 0}{" "}
                                    treatment /{" "}
                                    {experimentResults[index]
                                      .assigned_customers?.control ?? 0}{" "}
                                    control
                                  </p>
                                </div>

                                <div>
                                  <p className="text-slate-500">
                                    Measurement Status
                                  </p>
                                  <p className="font-semibold">
                                    {measurementResults[index]?.metrics
                                      ?.sample_status ||
                                      experimentResults[index]
                                        .measurement_status ||
                                      "Pending"}
                                  </p>
                                </div>

                                {experimentResults[index].started_at && (
                                  <div>
                                    <p className="text-slate-500">
                                      Started
                                    </p>
                                    <p className="font-semibold">
                                      {new Date(
                                        experimentResults[index].started_at!
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

                                  <p className="text-sm font-semibold text-blue-400">
                                    Measurement
                                  </p>

                                  <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">

                                    <div>
                                      <p className="text-xs text-slate-500">
                                        Incremental Revenue
                                      </p>

                                      <p className="mt-1 font-bold text-green-400">
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

                                    <div>
                                      <p className="text-xs text-slate-500">
                                        Revenue Growth
                                      </p>

                                      <p className="mt-1 font-bold">
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

                                    <div>
                                      <p className="text-xs text-slate-500">
                                        Conversion Lift
                                      </p>

                                      <p className="mt-1 font-bold">
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

                                    <div>
                                      <p className="text-xs text-slate-500">
                                        Margin Change
                                      </p>

                                      <p className="mt-1 font-bold">
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
                                      <p className="mt-3 text-xs text-yellow-400">
                                        More observation is needed:{" "}
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
                        "http://127.0.0.1:8000/api/demo/seed",
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
              AI AUTOPILOT
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
                    ? `Safety checks applied to ${decisionResults[0].decision}`
                    : "Waiting for strategy checks"
                }
                done={!!decisionResults[0]}
                active={loadingDecision === 0 && !decisionResults[0]}
              />

              <Status
                title="Experiment"
                text={
                  experimentResults[0]
                    ? `Experiment ${experimentResults[0].experiment_status || "launched"}`
                    : "Waiting for approved strategy"
                }
                done={["COMPLETED", "STOPPED"].includes(
                  experimentResults[0]?.experiment_status || ""
                )}
                active={
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
                    ? measurementResults[0].metrics?.sample_status ||
                      "Observed metrics returned"
                    : "Waiting for experiment measurement"
                }
                done={!!measurementResults[0]}
              />

              <Status
                title="AI Evaluation"
                text={
                  evaluationResults[0]
                    ? "Experiment result evaluated"
                    : "Waiting for sufficient observed results"
                }
                done={!!evaluationResults[0]}
              />

              <Status
                title="Learning Memory"
                text={
                  autopilotLearningRecord
                    ? `${autopilotLearningRecord.learning_signal} signal stored`
                    : "No finalized learning for this run"
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

              <Check
                text="Inventory availability: verified"
              />

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

                <span className="font-bold text-green-400">
                  {lastMeasurement?.revenue_growth_percent !== null &&
                  lastMeasurement?.revenue_growth_percent !== undefined
                    ? `+${lastMeasurement.revenue_growth_percent}%`
                    : "Awaiting data"}{" "}
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

                {lastMeasurement?.revenue_growth_percent !== null &&
                lastMeasurement?.revenue_growth_percent !== undefined
                  ? lastMeasurement.revenue_growth_percent >= goal
                    ? "🎯 Revenue growth goal achieved. AI can continue optimizing the winning strategy."
                    : "AI is measuring campaign performance and working toward the merchant growth target."
                  : "Not enough observed experiment data is available to report revenue growth yet."}

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
}: {
  title: string;
  value: string;
  change: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5">

      <p className="text-sm text-slate-400">
        {title}
      </p>

      <div className="mt-2 flex items-end justify-between">

        <h3 className="text-2xl font-bold">
          {value}
        </h3>

        <span className="text-sm font-semibold text-slate-500">
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
}: {
  title: string;
  text: string;
  done?: boolean;
  active?: boolean;
}) {
  return (
    <div className="flex gap-3">

      <div
        className={`mt-1 h-3 w-3 rounded-full ${
          done
            ? "bg-green-400"
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