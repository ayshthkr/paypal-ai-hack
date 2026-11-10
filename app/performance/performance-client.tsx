"use client";

import { useEffect, useState } from "react";

type Decile = { decile: number; count: number; predicted: number; observed: number; calibrated?: number };
type Evaluation = {
  available: boolean; reason?: string; as_of_date?: string; seed?: number;
  split?: { train_cutoff: string; training_date_range: string[]; validation_cohort: string[]; test_cohort: string[]; unit: string };
  survival_metrics?: Record<string, { uno_concordance: number | null; integrated_brier_score: number | null; time_dependent_auc: Record<string, number | null> }>;
  calibration_14d?: { count: number; raw_brier: number | null; calibrated_brier: number | null; deciles: Decile[] };
  backtest?: { paid_invoice_count: number; p50_median_absolute_error_days: number | null; p10_p90_coverage: number | null; target_coverage: number; invoices: { actual_paid_date: string; predicted_p50_date: string; absolute_error_days: number; within_p10_p90: boolean }[] };
  drift_backtest?: { flagged_client_count: number; true_positive_count: number; precision: number; recency_only_precision: number };
  feature_importance?: { feature: string; importance: number; std?: number }[]; feature_importance_method?: string; caveat?: string;
};

function percent(value: number | null | undefined) { return value == null ? "—" : `${(value * 100).toFixed(1)}%`; }
function score(value: number | null | undefined) { return value == null ? "—" : value.toFixed(3); }

export default function ModelPerformance() {
  const [data, setData] = useState<Evaluation | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    void fetch("/api/evaluation", { cache: "no-store" }).then(async response => {
      const value = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(value.detail || `Request failed (${response.status})`);
      if (active) { setData(value as Evaluation); setLoading(false); }
    }).catch(cause => { if (active) { setError(cause instanceof Error ? cause.message : "Could not load evaluation."); setLoading(false); } });
    return () => { active = false; };
  }, [reload]);
  if (error) return <div className="error-box">{error.includes("unavailable") || error.includes("502") ? "The API may be waking up. Refresh the evaluation to retry." : error} <button className="btn" onClick={() => { setError(""); setData(null); setLoading(true); setReload(value => value + 1); }}>Retry</button></div>;
  if (loading || !data) return <div className="empty-box">Loading model evaluation…</div>;
  if (!data.available) return <section className="panel"><div className="eyebrow">Model performance</div><h1>Evaluation unavailable</h1><p className="subheading">{data.reason || "Train the model to create its evaluation report."}</p></section>;

  const calibration = data.calibration_14d?.deciles || [];
  const backtest = data.backtest?.invoices || [];
  const points = calibration.map(row => `${40 + row.predicted * 420},${250 - row.observed * 220}`).join(" ");
  const epochDays = backtest.flatMap(row => [new Date(row.actual_paid_date).getTime(), new Date(row.predicted_p50_date).getTime()]).filter(Number.isFinite);
  const minDay = epochDays.length ? Math.min(...epochDays) : 0;
  const maxDay = epochDays.length ? Math.max(...epochDays) : 1;
  const plotX = (value: string) => 40 + (new Date(value).getTime() - minDay) / Math.max(1, maxDay - minDay) * 420;
  const plotY = (value: string) => 250 - (new Date(value).getTime() - minDay) / Math.max(1, maxDay - minDay) * 220;
  const importance = (data.feature_importance || []).slice(0, 8);
  const maxImportance = Math.max(0.000000001, ...importance.map(row => row.importance));
  return <>
    <div className="heading-row"><div><div className="eyebrow">Held-out synthetic cohort · {data.as_of_date}</div><h1>Model performance</h1><p className="subheading">Time-based evaluation of payment forecasts and client timing signals.</p></div></div>
    <div className="stats-grid"><article className="stat-card"><div className="stat-label">Paid invoices in backtest</div><div className="stat-value">{data.backtest?.paid_invoice_count ?? 0}</div><div className="stat-note">Held-out paid invoices</div></article><article className="stat-card"><div className="stat-label">Median p50 error</div><div className="stat-value">{data.backtest?.p50_median_absolute_error_days == null ? "—" : `${data.backtest.p50_median_absolute_error_days.toFixed(0)}d`}</div><div className="stat-note">Absolute days from predicted to actual</div></article><article className="stat-card"><div className="stat-label">p10–p90 coverage</div><div className="stat-value">{percent(data.backtest?.p10_p90_coverage)}</div><div className="stat-note">Target {percent(data.backtest?.target_coverage)}</div></article><article className="stat-card"><div className="stat-label">Drift precision</div><div className="stat-value">{percent(data.drift_backtest?.precision)}</div><div className="stat-note">Recency-only baseline {percent(data.drift_backtest?.recency_only_precision)}</div></article></div>
    <section className="performance-grid">
      <article className="panel"><div className="panel-head"><div><div className="panel-title">14-day calibration</div><div className="panel-subtitle">Predicted probability against observed share, grouped into deciles.</div></div></div>{calibration.length ? <><svg className="performance-chart" viewBox="0 0 500 300" role="img" aria-label="Calibration plot comparing predicted and observed payment probabilities"><line x1="40" y1="250" x2="460" y2="30" className="chart-diagonal"/><line x1="40" y1="250" x2="460" y2="250" className="chart-axis"/><line x1="40" y1="250" x2="40" y2="30" className="chart-axis"/><polyline points={points} className="chart-line"/>{calibration.map(row => <circle key={row.decile} cx={40 + row.predicted * 420} cy={250 - row.observed * 220} r="4" className="chart-point"><title>{`Decile ${row.decile}: predicted ${percent(row.predicted)}, observed ${percent(row.observed)}, n=${row.count}`}</title></circle>)}<text x="250" y="288" className="chart-label">Predicted paid within 14 days</text><text x="12" y="145" className="chart-label" transform="rotate(-90 12 145)">Observed paid share</text></svg><div className="fine-print">{data.calibration_14d?.count} held-out observations · Brier {score(data.calibration_14d?.raw_brier)} raw / {score(data.calibration_14d?.calibrated_brier)} calibrated</div></> : <div className="empty-inline">No calibration deciles were generated.</div>}</article>
      <article className="panel"><div className="panel-head"><div><div className="panel-title">Survival metrics</div><div className="panel-subtitle">Lower integrated Brier score is better; higher concordance and AUC are better.</div></div></div><div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Model</th><th>Uno C</th><th>Integrated Brier</th><th>AUC · due</th><th>+14d</th><th>+30d</th></tr></thead><tbody>{Object.entries(data.survival_metrics || {}).map(([name, metric]) => <tr key={name}><td>{name.replaceAll("_", " ")}</td><td>{score(metric.uno_concordance)}</td><td>{score(metric.integrated_brier_score)}</td><td>{score(metric.time_dependent_auc.due_date)}</td><td>{score(metric.time_dependent_auc.due_plus_14_days)}</td><td>{score(metric.time_dependent_auc.due_plus_30_days)}</td></tr>)}</tbody></table></div></article>
      <article className="panel"><div className="panel-head"><div><div className="panel-title">Predicted vs actual pay dates</div><div className="panel-subtitle">Each point is one paid invoice in the held-out issue-date backtest.</div></div></div>{backtest.length ? <><svg className="performance-chart" viewBox="0 0 500 300" role="img" aria-label="Scatter plot of predicted versus actual invoice payment dates"><line x1="40" y1="250" x2="460" y2="30" className="chart-diagonal"/><line x1="40" y1="250" x2="460" y2="250" className="chart-axis"/><line x1="40" y1="250" x2="40" y2="30" className="chart-axis"/>{backtest.map((row, index) => <circle key={`${row.actual_paid_date}-${index}`} cx={plotX(row.predicted_p50_date)} cy={plotY(row.actual_paid_date)} r="3" className={row.within_p10_p90 ? "chart-point" : "chart-point outside"}><title>{`Actual ${row.actual_paid_date}, predicted p50 ${row.predicted_p50_date}, error ${row.absolute_error_days} days`}</title></circle>)}<text x="250" y="288" className="chart-label">Predicted p50 date</text><text x="12" y="145" className="chart-label" transform="rotate(-90 12 145)">Actual pay date</text></svg><div className="fine-print">Blue points fell inside p10–p90; orange points fell outside. Median absolute error {data.backtest?.p50_median_absolute_error_days?.toFixed(1) ?? "—"} days.</div></> : <div className="empty-inline">No paid invoices are available for the held-out backtest.</div>}</article>
      <article className="panel"><div className="panel-head"><div><div className="panel-title">Leading model features</div><div className="panel-subtitle">{data.feature_importance_method || "Global feature importance from the training run."}</div></div></div>{importance.length ? importance.map(row => <div className="importance-row" key={row.feature}><span>{row.feature.replaceAll("_", " ")}</span><div className="importance-track"><span style={{ width: `${Math.max(2, Math.max(0, row.importance) / maxImportance * 100)}%` }}/></div><strong>{row.importance.toFixed(4)}</strong></div>) : <div className="empty-inline">Feature importance is unavailable.</div>}</article>
    </section>
    <section className="panel"><div className="panel-title">Evaluation split</div><div className="panel-subtitle">Train through {data.split?.train_cutoff} · validation {data.split?.validation_cohort.join(" to ")} · test {data.split?.test_cohort.join(" to ")} · grouped by {data.split?.unit || "invoice"}.</div><p className="fine-print" style={{ marginTop: 12 }}>{data.caveat}</p></section>
  </>;
}
