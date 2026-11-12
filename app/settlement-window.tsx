type Quantile = { days: number; date?: string } | null | undefined;

const HORIZON_DAYS = 180;

function position(days: number | undefined | null) {
  if (days == null) return 100;
  return Math.min(100, Math.max(0, (days / HORIZON_DAYS) * 100));
}

/**
 * Ruled 0–180 day track showing the forecast's p10–p90 settlement window with a p50 tick.
 * A quantile that is not reached within the horizon runs to the right edge and is drawn open.
 */
export default function SettlementWindow({ p10, p50, p90, compact = false }: { p10: Quantile; p50: Quantile; p90: Quantile; compact?: boolean }) {
  const start = position(p10?.days ?? (p50 ? p50.days : null));
  const end = position(p90?.days);
  const open = !p90;
  const label = `Settlement window: p10 ${p10 ? `day ${p10.days}` : "beyond 180 days"}, p50 ${p50 ? `day ${p50.days}` : "beyond 180 days"}, p90 ${p90 ? `day ${p90.days}` : "beyond 180 days"}`;
  return <span className={`settlement-window${compact ? " compact" : ""}`} role="img" aria-label={label}>
    <span className="settlement-window-range" data-open={open || undefined} style={{ left: `${start}%`, width: `${Math.max(1, end - start)}%` }}/>
    {p50 && <span className="settlement-window-median" style={{ left: `${position(p50.days)}%` }}/>}
  </span>;
}
