"use client";

import { useEffect, useState } from "react";

type Decile = { decile?: number; bin?: number; count: number; predicted?: number; calibrated?: number; observed?: number; mean_predicted?: number; ipcw_observed?: number };
type Metric = { uno_concordance: number | null; integrated_brier_score: number | null; time_dependent_auc: Record<string, number | null> };
type Ablation = { variant: string; feature_count: number; uno_concordance: number | null; integrated_brier_score: number | null; note: string };
type Sensitivity = { assumed_effect_probability: number; assumed_days_advanced: number; expected_dso_saved_days: number | null };
type PublicEvaluation = {
  dataset: string; source: string; license: string; evaluation_type: string;
  cleaning: { source_rows: number; valid_unique_invoice_count: number; conflicting_invoice_id_groups_dropped: number; invalid_or_anomalous_rows_dropped: number };
  split: { training_issue_date_through: string; test_issue_date_after: string; observation_end: string; train_invoice_count: number; train_paid_count: number; test_invoice_count: number; test_paid_count: number; test_right_censored_count: number; benchmark_sample_count: number; lightgbm_train_invoice_count: number; lightgbm_train_paid_count: number; lightgbm_test_invoice_count: number; lightgbm_test_paid_count: number; lightgbm_test_right_censored_count: number };
  survival_metrics: Record<string, Metric>; caveats: string[];
} | null;
type Evaluation = {
  available: boolean; reason?: string; as_of_date?: string; seed?: number; caveat?: string;
  split?: { train_cutoff: string; training_date_range: string[]; validation_cohort: string[]; test_cohort: string[]; unit: string };
  model_comparison_note?: string;
  model_card?: { deployed_model: string; selection_rule: string; validation_integrated_brier: Record<string, number | null>; held_out_metrics_won: string[]; held_out_metrics_lost: string[]; training_window: string[]; training_cutoff: string; known_limits: string[] };
  deployed_forecast_evaluation?: { model: string; scoring_path: string; overdue_checkpoints_days: number[]; observation_count: number; paid_only_p50_median_absolute_error_days: number | null; paid_only_p50_count: number; brier_by_horizon: Record<string, number | null>; brier_observation_counts: Record<string, number>; limitations: string };
  survival_metrics?: Record<string, Metric>;
  concordance_confidence_intervals?: Record<string, { lower: number; upper: number; replicates: number }>;
  calibration_14d?: { count: number; count_unit?: string; raw_brier: number | null; calibrated_brier: number | null; deciles: Decile[] };
  calibration_30d?: { horizon_days: number; count: number; censored_known_negative_count: number; censored_before_horizon_excluded: number; method: string; deciles: Decile[] };
  interval_calibration?: { method: string; count: number; correction_days: number; currency_corrections?: Record<string, { correction_days: number; count: number }>; caveat: string; selection?: { window: string; recent_months: string[]; recent_paid_count: number; validation_residual_drift: { p_value: number | null; drift_detected_at_0_05: boolean | null } } };
  backtest?: { paid_invoice_count: number; p50_median_absolute_error_days: number | null; p10_p90_coverage: number | null; raw_quantile_coverage?: number | null; coverage_invoice_count: number; target_coverage: number; interval_label?: string; invoices: { actual_paid_date: string; predicted_p50_date: string | null; absolute_error_days: number | null; within_interval: boolean }[] };
  coverage_by_issue_month?: { month: string; count: number; coverage: number }[];
  residual_drift?: { calibration_count: number; test_count: number; statistic: number | null; p_value: number | null; drift_detected_at_0_05: boolean | null; calibration_median_residual_days?: number; test_median_residual_days?: number; method: string; caveat: string };
  censor_aware_evaluation?: { test_invoice_count: number; paid_event_count: number; right_censored_count: number; metrics: string };
  ablation_metrics?: Ablation[];
  policy_simulation?: { label: string; invoice_count: number; eligible_invoice_count: number; simulated_dso_saved_days: number; sensitivity?: Sensitivity[]; assumption: string };
  drift_backtest?: { flagged_client_count: number; true_positive_count: number; precision: number | null; recency_only_precision: number | null; recall: number | null; median_lead_time_days: number | null; lead_time_client_count: number; actual_positive_client_count: number; plain_delay_rule?: { name: string; flagged_client_count: number; true_positive_count: number; precision: number | null; recall: number | null } };
  error_by_currency?: { group: string; count: number; median_absolute_error_days: number | null; coverage: number }[];
  error_by_age_bucket?: { group: string; count: number; median_absolute_error_days: number | null; coverage: number }[];
  feature_importance?: { feature: string; importance: number; std?: number }[]; feature_importance_method?: string;
  shap_summary?: { feature: string; mean_absolute_shap: number }[];
  public_evaluation?: PublicEvaluation;
};

function percent(value: number | null | undefined) { return value == null ? "—" : `${(value * 100).toFixed(1)}%`; }
function score(value: number | null | undefined) { return value == null ? "—" : value.toFixed(3); }
function medianError(value: number | null | undefined) { return value == null ? "—" : `${value.toFixed(1)} days`; }

function MetricTable({ metrics, intervals }: { metrics: Record<string, Metric>; intervals?: Record<string, { lower: number; upper: number; replicates: number }> }) {
  return <div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Model</th><th>Uno C</th><th>Integrated Brier</th><th>AUC · due</th><th>AUC · +14d</th><th>AUC · +30d</th></tr></thead><tbody>{Object.entries(metrics).map(([name, metric]) => {
    const interval = intervals?.[name];
    return <tr key={name}><td>{name.replaceAll("_", " ")}</td><td>{score(metric.uno_concordance)}{interval && <small> [{score(interval.lower)}, {score(interval.upper)}]</small>}</td><td>{score(metric.integrated_brier_score)}</td><td>{score(metric.time_dependent_auc.due_date)}</td><td>{score(metric.time_dependent_auc.due_plus_14_days)}</td><td>{score(metric.time_dependent_auc.due_plus_30_days)}</td></tr>;
  })}</tbody></table></div>;
}

export default function ModelPerformance() {
  const [data, setData] = useState<Evaluation | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [waking, setWaking] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => setWaking(true), 1_000);
    void fetch("/api/evaluation", { cache: "no-store", signal: AbortSignal.timeout(30_000) })
      .then(async response => {
        const value = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
        if (active) setData(value as Evaluation);
      })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : "Could not load evaluation."); })
      .finally(() => { window.clearTimeout(timer); if (active) { setLoading(false); setWaking(false); } });
    return () => { active = false; window.clearTimeout(timer); };
  }, [reload]);

  if (loading) return <div className="empty-box" role="status">{waking ? "Waking the API to load evaluation…" : "Loading model evaluation…"}</div>;
  if (error || !data) return <div className="error-box" role="alert">{error || "Evaluation is unavailable."} <button className="btn" onClick={() => { setError(""); setLoading(true); setReload(value => value + 1); }}>Retry</button></div>;
  if (!data.available) return <section className="panel"><div className="eyebrow">Model performance</div><h1>Evaluation unavailable</h1><p className="subheading">{data.reason || "Train the model to create its evaluation report."}</p></section>;

  const backtest = data.backtest;
  const intervalCoverage = backtest?.p10_p90_coverage;
  const activeModel = data.model_card?.deployed_model || "unavailable";
  const calibration30 = data.calibration_30d;
  const publicEval = data.public_evaluation;

  return <>
    <div className="heading-row"><div><div className="eyebrow">Held-out synthetic cohort · {data.as_of_date}</div><h1>Model performance</h1><p className="subheading">A time-separated evaluation of settlement timing, calibration, and client signals.</p></div></div>

    <section className="panel" aria-labelledby="evaluation-summary-title">
      <div className="eyebrow">Read this first</div>
      <h2 id="evaluation-summary-title">The 80% coverage interval covered {percent(intervalCoverage)} of {backtest?.coverage_invoice_count ?? 0} eligible paid test invoices.</h2>
      <p>{data.censor_aware_evaluation?.test_invoice_count ?? 0} held-out invoices were evaluated with censor-aware survival metrics, including {data.censor_aware_evaluation?.right_censored_count ?? 0} right-censored open invoices. Paid-only date error and interval coverage remain descriptive.</p>
      {data.deployed_forecast_evaluation && <p>The full conditional scoring path was replayed at overdue checkpoints for {data.deployed_forecast_evaluation.observation_count} eligible observations: median paid-only P50 error {medianError(data.deployed_forecast_evaluation.paid_only_p50_median_absolute_error_days)} across {data.deployed_forecast_evaluation.paid_only_p50_count} paid outcomes; Brier scores at 14/30/60 days {Object.entries(data.deployed_forecast_evaluation.brier_by_horizon).map(([horizon, value]) => `${horizon} ${score(value)}`).join(" · ")}.</p>}
      <p className="fine-print">Raw curve P10–P90 coverage was {percent(backtest?.raw_quantile_coverage)}. The wider calibrated coverage interval is a separate output. This is a synthetic pipeline check, not evidence of real-world accuracy.</p>
    </section>

    {data.model_card && <section className="panel"><div className="panel-title">Model card</div><div className="invoice-facts">
      <div className="fact"><div className="fact-label">Deployed model</div><div className="fact-value">{activeModel.replaceAll("_", " ")}</div></div>
      <div className="fact"><div className="fact-label">Training window</div><div className="fact-value">{data.model_card.training_window.join(" to ")}</div></div>
      <div className="fact"><div className="fact-label">Training cutoff</div><div className="fact-value">{data.model_card.training_cutoff}</div></div>
    </div><p>{data.model_card.selection_rule}</p><p>Validation integrated Brier: {Object.entries(data.model_card.validation_integrated_brier).map(([name, value]) => `${name.replaceAll("_", " ")} ${score(value)}`).join(" · ")}.</p>
      <p><strong>Held-out wins:</strong> {data.model_card.held_out_metrics_won.join(" · ") || "none in this comparison"}</p><p><strong>Held-out losses:</strong> {data.model_card.held_out_metrics_lost.join(" · ") || "none in this comparison"}</p>
      <ul>{data.model_card.known_limits.map(limit => <li key={limit}>{limit}</li>)}</ul>
    </section>}

    <section className="stats-grid">
      <article className="stat-card"><div className="stat-label">Held-out paid invoices</div><div className="stat-value">{backtest?.paid_invoice_count ?? 0}</div><div className="stat-note">Paid-only date-error cohort</div></article>
      <article className="stat-card"><div className="stat-label">Median P50 error</div><div className="stat-value">{medianError(backtest?.p50_median_absolute_error_days)}</div><div className="stat-note">Absolute issue-date error</div></article>
      <article className="stat-card"><div className="stat-label">Calibrated interval coverage</div><div className="stat-value">{percent(intervalCoverage)}</div><div className="stat-note">Target {percent(backtest?.target_coverage)}</div></article>
      <article className="stat-card"><div className="stat-label">Drift detector precision</div><div className="stat-value">{percent(data.drift_backtest?.precision)}</div><div className="stat-note">Generator-defined labels · {data.drift_backtest?.true_positive_count ?? 0}/{data.drift_backtest?.flagged_client_count ?? 0}</div></article>
    </section>

    <details className="panel statistics-details"><summary>Detailed statistical tables, validation splits, and comparator outputs</summary>

    <section className="panel"><div className="panel-title">Censor-aware survival metrics</div><p className="panel-subtitle">Open invoices censored before the scoring horizon remain in the test cohort. Lower integrated Brier is better; higher Uno C and time-dependent AUC are better.</p>{data.survival_metrics && <MetricTable metrics={data.survival_metrics} intervals={data.concordance_confidence_intervals}/>}</section>

    {calibration30 && <section className="panel"><div className="panel-title">Paid within 30 days · IPCW calibration</div><p>{calibration30.count} invoices were evaluable at day 30, including {calibration30.censored_known_negative_count} open invoices known to remain unpaid through the horizon. {calibration30.censored_before_horizon_excluded} invoices censored earlier were excluded from the binary calibration bins and remain in the survival scores.</p><div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Probability bin</th><th>Invoices</th><th>Mean predicted</th><th>IPCW observed</th></tr></thead><tbody>{calibration30.deciles.map(row => <tr key={row.bin}><td>{row.bin}</td><td>{row.count}</td><td>{percent(row.mean_predicted)}</td><td>{percent(row.ipcw_observed)}</td></tr>)}</tbody></table></div><div className="fine-print">{calibration30.method}</div></section>}

    {data.calibration_14d && <section className="panel"><div className="panel-title">14-day calibration</div><p>{data.calibration_14d.count} held-out invoice-probability observations · Brier {score(data.calibration_14d.raw_brier)} raw / {score(data.calibration_14d.calibrated_brier)} calibrated.</p><div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Bin</th><th>Invoices</th><th>Mean predicted</th><th>Observed</th></tr></thead><tbody>{data.calibration_14d.deciles.map(row => <tr key={row.decile}><td>{row.decile}</td><td>{row.count}</td><td>{percent(row.calibrated ?? row.predicted)}</td><td>{percent(row.observed)}</td></tr>)}</tbody></table></div></section>}

    {data.residual_drift && <section className="panel"><div className="panel-title">Calibration-to-test residual drift</div><p>{data.residual_drift.method}. Calibration n={data.residual_drift.calibration_count}, test n={data.residual_drift.test_count}; median residual {medianError(data.residual_drift.calibration_median_residual_days)} versus {medianError(data.residual_drift.test_median_residual_days)}; KS p={score(data.residual_drift.p_value)}.</p><p className="fine-print">{data.residual_drift.drift_detected_at_0_05 ? "The paid-residual distributions differ at the 0.05 threshold." : "No difference was detected at the 0.05 threshold."} {data.residual_drift.caveat}</p></section>}

    {data.interval_calibration && <section className="panel"><div className="panel-title">Interval calibration and coverage by issue month</div><p>{data.interval_calibration.method} · {data.interval_calibration.count} validation invoices · correction {data.interval_calibration.correction_days} days. Calibration window: {data.interval_calibration.selection?.window || "full validation window"}.</p><p className="fine-print">{data.interval_calibration.caveat} {data.interval_calibration.selection?.validation_residual_drift?.drift_detected_at_0_05 ? "Validation residual drift was detected; the recent-window rule was applied." : "Recent-window calibration was not selected because validation drift was not detected or the recent sample was too small."}</p>
      <div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Issue month</th><th>Paid invoices</th><th>Coverage interval hit rate</th></tr></thead><tbody>{(data.coverage_by_issue_month || []).map(row => <tr key={row.month}><td>{row.month}</td><td>{row.count}</td><td>{percent(row.coverage)}</td></tr>)}</tbody></table></div>
    </section>}

    {data.ablation_metrics && <section className="panel"><div className="panel-title">Feature ablations</div><p className="fine-print">Fixed temporal test split and training recipe. Weekday is already absent from the deployed schema; other rows are retrained ablations.</p><div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Variant</th><th>Features</th><th>Uno C</th><th>Integrated Brier</th><th>Notes</th></tr></thead><tbody>{data.ablation_metrics.map(row => <tr key={row.variant}><td>{row.variant.replaceAll("_", " ")}</td><td>{row.feature_count}</td><td>{score(row.uno_concordance)}</td><td>{score(row.integrated_brier_score)}</td><td>{row.note}</td></tr>)}</tbody></table></div></section>}

    {data.drift_backtest && <section className="panel"><div className="panel-title">Client drift versus simple baselines</div><p className="fine-print">Evaluation labels are generated from synthetic behavior: no invoice in 90 days or future paid-invoice median delay at least seven days above its prior median.</p><div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Detector</th><th>Flagged</th><th>True positives</th><th>Precision</th><th>Recall</th></tr></thead><tbody><tr><td>Current drift detector</td><td>{data.drift_backtest.flagged_client_count}</td><td>{data.drift_backtest.true_positive_count}</td><td>{percent(data.drift_backtest.precision)}</td><td>{percent(data.drift_backtest.recall)}</td></tr><tr><td>Recency-only top-k</td><td>Matched to detector</td><td>—</td><td>{percent(data.drift_backtest.recency_only_precision)}</td><td>—</td></tr>{data.drift_backtest.plain_delay_rule && <tr><td>{data.drift_backtest.plain_delay_rule.name}</td><td>{data.drift_backtest.plain_delay_rule.flagged_client_count}</td><td>{data.drift_backtest.plain_delay_rule.true_positive_count}</td><td>{percent(data.drift_backtest.plain_delay_rule.precision)}</td><td>{percent(data.drift_backtest.plain_delay_rule.recall)}</td></tr>}</tbody></table></div></section>}

    {data.policy_simulation && <section className="panel"><div className="panel-title">{data.policy_simulation.label}</div><p>{data.policy_simulation.eligible_invoice_count} eligible of {data.policy_simulation.invoice_count} paid invoices. The displayed DSO values are simulated from an assumed 10% reminder effect and five-day advance; they are not observed payment results.</p><div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Assumed chance</th><th>Days advanced</th><th>Modeled weighted DSO change</th></tr></thead><tbody>{data.policy_simulation.sensitivity?.map(row => <tr key={`${row.assumed_effect_probability}-${row.assumed_days_advanced}`}><td>{percent(row.assumed_effect_probability)}</td><td>{row.assumed_days_advanced}</td><td>{row.expected_dso_saved_days == null ? "—" : row.expected_dso_saved_days.toFixed(3)}</td></tr>)}</tbody></table></div><p className="fine-print">{data.policy_simulation.assumption} Reminder causality requires a controlled pilot. Observed payment outcomes and measured workflow review time are reported separately.</p></section>}

    {publicEval ? <section className="panel"><div className="panel-title">Independent public-data evaluation</div><p><a href={publicEval.source} target="_blank" rel="noreferrer">{publicEval.dataset}</a> · {publicEval.license} · temporal test ending {publicEval.split.observation_end}.</p><p>{publicEval.evaluation_type}</p><p className="fine-print">Cox and pooled Kaplan–Meier use {publicEval.split.benchmark_sample_count.toLocaleString("en-US")} sampled invoices from {publicEval.cleaning.valid_unique_invoice_count.toLocaleString("en-US")} cleaned unique records; their test has n={publicEval.split.test_invoice_count}, paid {publicEval.split.test_paid_count}, right-censored {publicEval.split.test_right_censored_count}. The daily LightGBM hazard refit uses a separate sample of {publicEval.split.lightgbm_train_invoice_count + publicEval.split.lightgbm_test_invoice_count} invoices; its test has n={publicEval.split.lightgbm_test_invoice_count}, paid {publicEval.split.lightgbm_test_paid_count}, right-censored {publicEval.split.lightgbm_test_right_censored_count}. Cleaning dropped {publicEval.cleaning.conflicting_invoice_id_groups_dropped} conflicting ID groups and {publicEval.cleaning.invalid_or_anomalous_rows_dropped} invalid/anomalous rows.</p>{Object.keys(publicEval.survival_metrics).length > 0 && <MetricTable metrics={publicEval.survival_metrics}/>}<ul>{publicEval.caveats.map(caveat => <li key={caveat}>{caveat}</li>)}</ul></section> : <section className="panel"><div className="panel-title">Independent public-data evaluation</div><p>Not generated in this deployment. See the reproducible command in the repository&apos;s model evaluation docs.</p></section>}

    <section className="performance-grid">
      {[ ["Error by currency", data.error_by_currency], ["Conditional error by age bucket", data.error_by_age_bucket] ].map(([title, rows]) => <article className="panel" key={String(title)}><div className="panel-title">{String(title)}</div><div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Group</th><th>Count</th><th>Median absolute error</th><th>Coverage</th></tr></thead><tbody>{(rows as Evaluation["error_by_currency"])?.map(row => <tr key={row.group}><td>{row.group}</td><td>{row.count}</td><td>{medianError(row.median_absolute_error_days)}</td><td>{percent(row.coverage)}</td></tr>)}</tbody></table></div><div className="fine-print">{title === "Conditional error by age bucket" ? "An invoice can contribute multiple conditional observations." : "Paid held-out invoices; coverage is the calibrated interval hit rate."}</div></article>)}
    </section>

    <section className="performance-grid"><article className="panel"><div className="panel-title">LightGBM comparator features</div><div className="fine-print">{data.feature_importance_method || "Validation permutation importance"}; the selected release model is {activeModel.replaceAll("_", " ")}.</div>{(data.feature_importance || []).slice(0, 8).map(row => <div className="importance-row" key={row.feature}><span>{row.feature.replaceAll("_", " ")}</span><strong>{row.importance.toFixed(4)}</strong></div>)}</article><article className="panel"><div className="panel-title">SHAP summary · LightGBM validation risk rows</div>{(data.shap_summary || []).slice(0, 8).map(row => <div className="importance-row" key={row.feature}><span>{row.feature.replaceAll("_", " ")}</span><strong>{row.mean_absolute_shap.toFixed(3)}</strong></div>)}</article></section>

    <section className="panel"><div className="panel-title">Evaluation split</div><p>Train through {data.split?.train_cutoff} · validation {data.split?.validation_cohort.join(" to ")} · test {data.split?.test_cohort.join(" to ")} · grouped by {data.split?.unit || "invoice"}.</p><p className="fine-print">{data.model_comparison_note} {data.caveat}</p></section>
    </details>
  </>;
}
