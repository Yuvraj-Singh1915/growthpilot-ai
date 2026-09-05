"use client";

import { useEffect, useRef, useState } from "react";

type Opportunity = {
  title: string;
  priority: string;
  potential_revenue: number;
  suggested_discount: number;
  recommended_action: string;
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
  experiment_id?: string;
  test_group?: string;
  holdout_group?: string;
  measurement?: string[];
  message?: string;
};

type MeasurementResult = {
  incremental_revenue: number;
  revenue_growth_percent: number;
  conversion_lift: number;
  margin_change: number;
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
  const autopilotRunRef = useRef(false);

  const projectedRevenue = Math.round(100000 * (1 + goal / 100));
  const projectedConversion = (2.6 * (1 + goal / 100)).toFixed(1);
  const projectedAOV = Math.round(1250 * (1 + goal / 200));
  const projectedRecovery = (18 * (1 + goal / 100)).toFixed(1);

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

  useEffect(() => {
    loadOpportunities();
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
        experiment_id: experimentData.experiment_id,
        test_group: experimentData.test_group,
        holdout_group: experimentData.holdout_group,
        measurement: experimentData.measurement,
        message: experimentData.message,
      },
    }));

    // 3. MEASUREMENT
    // Demo experiment data
    const measurementResponse = await fetch(
      "http://127.0.0.1:8000/api/measurement",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          experiment_id: experimentData.experiment_id,
          opportunity: opportunity.title,
          baseline_revenue: 100000,
          experiment_revenue: 115000,
          baseline_conversion: 2.5,
          experiment_conversion: 3.0,
          baseline_margin: 25,
          experiment_margin: 24,
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
          opportunity: opportunity.title,
          revenue_growth_percent:
            measurementData.revenue_growth_percent,
          conversion_lift: measurementData.conversion_lift,
          margin_change: measurementData.margin_change,
          experiment_margin:
            measurementData.metrics.experiment_margin,
          target_growth: goal,
          min_margin: minMargin,
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

  const goalProgress = lastMeasurement
    ? Math.min(
        100,
        (lastMeasurement.revenue_growth_percent /
          Math.max(goal, 1)) *
          100
      )
    : 0;

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
            title="Revenue"
            value={`₹${projectedRevenue.toLocaleString(
              "en-IN"
            )}`}
            change="+12.4%"
          />

          <MetricCard
            title="Conversion Rate"
            value={`${projectedConversion}%`}
            change="+0.8%"
          />

          <MetricCard
            title="Average Order Value"
            value={`₹${projectedAOV.toLocaleString(
              "en-IN"
            )}`}
            change="+8.2%"
          />

          <MetricCard
            title="Cart Recovery"
            value={`${projectedRecovery}%`}
            change="+6.1%"
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
              GrowthPilot continuously detects, decides, tests, measures, and learns.
            </p>
          </div>

          <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
            {[
              ["01", "Opportunity", "Detection", "text-blue-400"],
              ["02", "AI", "Decision", "text-purple-400"],
              ["03", "Safety", "Guardrails", "text-orange-400"],
              ["04", "90 / 10", "Experiment", "text-green-400"],
              ["05", "Impact", "Measurement", "text-cyan-400"],
              ["06", "AI", "Evaluation", "text-pink-400"],
            ].map(([step, label, detail, color], index) => (
              <div key={step} className="relative rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <p className={`text-xs font-semibold ${color}`}>{step}</p>
                <p className="mt-3 text-sm font-semibold text-white">{label}</p>
                <p className="text-xs text-slate-400">{detail}</p>
                {index < 5 && (
                  <span className="absolute -right-3 top-1/2 z-10 hidden -translate-y-1/2 text-slate-600 lg:block">
                    →
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>

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
                            </div>

                            <span className="shrink-0 rounded-full border border-blue-400/20 bg-blue-500/10 px-3 py-1 text-xs font-semibold text-blue-400">
                              {item.priority}
                            </span>

                          </div>

                          <div className="mt-5 rounded-lg border border-green-500/20 bg-green-500/5 px-4 py-3">

                            <p className="text-xs font-medium uppercase tracking-wide text-slate-400">
                              Potential Revenue
                            </p>

                            <p className="mt-1 text-2xl font-bold tracking-tight text-green-400">
                              ₹
                              {item.potential_revenue.toLocaleString(
                                "en-IN"
                              )}
                            </p>

                          </div>

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
                                🚀 Experiment
                                Launched
                              </p>

                              <div className="mt-3 grid grid-cols-2 gap-3 text-sm">

                                <div>
                                  <p className="text-slate-500">
                                    Test Group
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
                                    Holdout
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
                                      experimentResults[
                                        index
                                      ].status
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

                              </div>

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
                                        ₹
                                        {measurementResults[
                                          index
                                        ].incremental_revenue.toLocaleString(
                                          "en-IN"
                                        )}
                                      </p>
                                    </div>

                                    <div>
                                      <p className="text-xs text-slate-500">
                                        Revenue Growth
                                      </p>

                                      <p className="mt-1 font-bold">
                                        +
                                        {
                                          measurementResults[
                                            index
                                          ]
                                            .revenue_growth_percent
                                        }
                                        %
                                      </p>
                                    </div>

                                    <div>
                                      <p className="text-xs text-slate-500">
                                        Conversion Lift
                                      </p>

                                      <p className="mt-1 font-bold">
                                        +
                                        {
                                          measurementResults[
                                            index
                                          ]
                                            .conversion_lift
                                        }
                                        %
                                      </p>
                                    </div>

                                    <div>
                                      <p className="text-xs text-slate-500">
                                        Margin Change
                                      </p>

                                      <p className="mt-1 font-bold">
                                        {
                                          measurementResults[
                                            index
                                          ]
                                            .margin_change
                                        }
                                        %
                                      </p>
                                    </div>

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
              <p className="mt-6 text-slate-500">
                No opportunities loaded yet.
              </p>
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
                text="Commerce events analyzed"
                done
              />

              <Status
                title="Opportunity Detection"
                text={`${opportunities.length} opportunities found`}
                done
              />

              <Status
                title="Strategy Selection"
                text={
                  decisionResults[0]
                    ? `AI decision: ${decisionResults[0].decision}`
                    : "Waiting for AI decision"
                }
                active={
                  !decisionResults[0]
                }
              />

              <Status
                title="Action Execution"
                text={
                  experimentResults[0]
                    ? "Controlled experiment running"
                    : "Waiting for approved strategy"
                }
                done={
                  !!experimentResults[0]
                }
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
                  {lastMeasurement
                    ? `+${lastMeasurement.revenue_growth_percent}%`
                    : "0%"}{" "}
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

                {lastMeasurement
                  ? lastMeasurement.revenue_growth_percent >=
                    goal
                    ? "🎯 Revenue growth goal achieved. AI can continue optimizing the winning strategy."
                    : "AI is measuring campaign performance and working toward the merchant growth target."
                  : "AI estimates the current strategy could reach the target within the active campaign period."}

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

        <span className="text-sm font-semibold text-green-400">
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