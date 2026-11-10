type Quantile = { days: number; date: string } | null;

export default function SurvivalCurve({ curve, quantiles }: { curve: number[]; quantiles: Record<string, Quantile> }) {
  const width = 720;
  const height = 240;
  const pad = { left: 40, right: 12, top: 12, bottom: 28 };
  const innerWidth = width - pad.left - pad.right;
  const innerHeight = height - pad.top - pad.bottom;
  const points = curve.map((value, index) => `${pad.left + index / Math.max(1, curve.length - 1) * innerWidth},${pad.top + (1 - value) * innerHeight}`).join(" ");
  const p10 = quantiles.p10?.days;
  const p90 = quantiles.p90?.days;
  const x = (days: number) => pad.left + Math.min(180, days) / 180 * innerWidth;
  const ticks = [0, 30, 60, 90, 120, 180];
  return <div className="curve-wrap">
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Conditional survival curve. The y-axis shows the probability that the invoice remains unpaid; the x-axis shows days from today." width="100%" height="100%">
      {[0, .25, .5, .75, 1].map(value => <g key={value}><line x1={pad.left} x2={width - pad.right} y1={pad.top + (1 - value) * innerHeight} y2={pad.top + (1 - value) * innerHeight} stroke="#e9eef1" strokeDasharray={value === 0 || value === 1 ? "0" : "3 5"}/><text x={pad.left - 9} y={pad.top + (1 - value) * innerHeight + 4} textAnchor="end" fill="#87949d" fontSize="10">{Math.round(value * 100)}%</text></g>)}
      {p10 != null && p90 != null && <rect x={x(p10)} y={pad.top} width={Math.max(0, x(p90) - x(p10))} height={innerHeight} fill="#e7efff" opacity=".68"/>}
      <polyline points={points} fill="none" stroke="#155eef" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round"/>
      {quantiles.p50 && <line x1={x(quantiles.p50.days)} x2={x(quantiles.p50.days)} y1={pad.top} y2={pad.top + innerHeight} stroke="#132f51" strokeWidth="1.3" strokeDasharray="4 4"/>}
      {ticks.map(day => <g key={day}><line x1={x(day)} x2={x(day)} y1={height - pad.bottom} y2={height - pad.bottom + 4} stroke="#8897a1"/><text x={x(day)} y={height - 7} textAnchor="middle" fill="#87949d" fontSize="10">{day}</text></g>)}
      <text x={width - 2} y={height - 7} textAnchor="end" fill="#87949d" fontSize="9">days</text>
    </svg>
  </div>;
}
