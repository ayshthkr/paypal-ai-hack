"use client";

import { useState } from "react";

type Quantile = { days: number; date: string } | null;

function dateAt(scoringDate: string, days: number) {
  const date = new Date(`${scoringDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toLocaleDateString("en-GB", { timeZone: "UTC", day: "numeric", month: "short", year: "numeric" });
}

function percent(value: number) {
  return value > 0 && value < 0.01 ? "<1%" : `${Math.round(value * 100)}%`;
}

export default function SurvivalCurve({ curve, quantiles, scoringDate }: { curve: number[]; quantiles: Record<string, Quantile>; scoringDate: string }) {
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const width = 720;
  const height = 240;
  const pad = { left: 44, right: 16, top: 12, bottom: 44 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const points = curve.map((value, index) => `${pad.left + index / Math.max(1, curve.length - 1) * innerWidth},${pad.top + (1 - value) * innerHeight}`).join(" ");
  const p10 = quantiles.p10?.days;
  const p90 = quantiles.p90?.days;
  const x = (days: number) => pad.left + Math.min(180, days) / 180 * innerWidth;
  const ticks = [0, 30, 60, 90, 120, 180];
  const pointDay = (index: number) => Math.round(index / Math.max(1, curve.length - 1) * 180);
  const selected = activeIndex == null ? null : { index: activeIndex, day: pointDay(activeIndex), unpaid: curve[activeIndex] };
  const setFromPointer = (clientX: number, rect: DOMRect) => {
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    setActiveIndex(Math.round(ratio * (curve.length - 1)));
  };
  const moveByKeyboard = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    setActiveIndex(current => event.key === "Home" ? 0 : event.key === "End" ? curve.length - 1 : Math.max(0, Math.min(curve.length - 1, (current ?? 0) + (event.key === "ArrowRight" ? 1 : -1))));
  };
  const probabilityDays = [0, 7, 14, 30, 60, 90, 180];

  return <div className="curve-wrap">
    <div className="curve-interaction" role="slider" tabIndex={0} aria-label="Explore daily full-settlement survival forecast" aria-valuemin={0} aria-valuemax={180} aria-valuenow={selected?.day ?? 0} aria-valuetext={selected ? `Day ${selected.day}, ${dateAt(scoringDate, selected.day)}: ${percent(1 - selected.unpaid)} probability paid in full by this date` : `Day 0, ${dateAt(scoringDate, 0)}: current balance unpaid`} aria-describedby="survival-curve-description" onPointerMove={event => setFromPointer(event.clientX, event.currentTarget.getBoundingClientRect())} onPointerLeave={() => setActiveIndex(null)} onFocus={() => setActiveIndex(current => current ?? 0)} onKeyDown={moveByKeyboard}>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Probability that the remaining balance is unpaid from today through day 180" width="100%" height="100%">
        {[0, .25, .5, .75, 1].map(value => <g key={value}><line x1={pad.left} x2={width - pad.right} y1={pad.top + (1 - value) * innerHeight} y2={pad.top + (1 - value) * innerHeight} stroke="#e0e0e0"/><text className="chart-tick" x={pad.left - 8} y={pad.top + (1 - value) * innerHeight + 4} textAnchor="end">{Math.round(value * 100)}%</text></g>)}
        {p10 != null && p90 != null && <rect x={x(p10)} y={pad.top} width={Math.max(0, x(p90) - x(p10))} height={innerHeight} fill="#d0e2ff" opacity=".6"/>}
        <polyline points={points} fill="none" stroke="#0f62fe" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round"><title>Remaining-balance unpaid probability by day</title></polyline>
        {quantiles.p50 && <line x1={x(quantiles.p50.days)} x2={x(quantiles.p50.days)} y1={pad.top} y2={pad.top + innerHeight} stroke="#161616" strokeWidth="1" strokeDasharray="4 4"/>}
        {selected && <><line x1={x(selected.day)} x2={x(selected.day)} y1={pad.top} y2={pad.top + innerHeight} stroke="#161616" strokeWidth="1"/><circle cx={x(selected.day)} cy={pad.top + (1 - selected.unpaid) * innerHeight} r="4" fill="#0f62fe" stroke="#fff" strokeWidth="2"/></>}
        {ticks.map(day => <g key={day}><line x1={x(day)} x2={x(day)} y1={height - pad.bottom} y2={height - pad.bottom + 4} stroke="#8d8d8d"/><text className="chart-tick" x={x(day)} y={height - pad.bottom + 18} textAnchor="middle">{day}</text></g>)}
        <text className="chart-tick" x={pad.left + innerWidth / 2} y={height - 4} textAnchor="middle">Days from snapshot</text>
      </svg>
    </div>
    <p id="survival-curve-description" className="fine-print">The curve tracks time to full settlement of the remaining balance. Partial receipts can reduce the balance before full settlement and are shown separately in the payment history. Hover, touch, or focus the chart; use left/right arrows for exact point values.</p>
    {selected && <div className="curve-readout" role="status" aria-live="polite">Day {selected.day} · {dateAt(scoringDate, selected.day)} · {percent(1 - selected.unpaid)} modeled probability fully settled · {percent(selected.unpaid)} still unpaid</div>}
    <details className="curve-values"><summary>Inspect dated probabilities</summary><div className="table-wrap"><table className="client-invoice-table"><thead><tr><th>Horizon</th><th>Date</th><th>Paid in full by date</th><th>Still unpaid</th></tr></thead><tbody>{probabilityDays.map(day => { const index = Math.round(day / 180 * (curve.length - 1)); const unpaid = curve[index] ?? 1; return <tr key={day}><td>{day === 0 ? "Today" : `${day} days`}</td><td>{dateAt(scoringDate, day)}</td><td>{percent(1 - unpaid)}</td><td>{percent(unpaid)}</td></tr>; })}</tbody></table></div></details>
  </div>;
}
