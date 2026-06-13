import type { OutcomeResponse } from "@/lib/api";

type Mode = "global" | "personal";

export default function FilteredMetrics({
  outcomes,
  mode,
}: {
  outcomes: OutcomeResponse[];
  mode: Mode;
}) {
  const containerStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "10px 16px",
    background: "var(--color-background-secondary)",
    border: "0.5px solid var(--color-border-tertiary)",
    borderRadius: 8,
    marginBottom: 14,
    fontSize: 13,
    flexWrap: "wrap",
  };

  if (outcomes.length === 0) {
    return (
      <div style={containerStyle}>
        <span style={{ fontSize: 12, color: "var(--color-text-secondary)", fontWeight: 600 }}>
          Filtered
        </span>
        <span style={{ color: "var(--color-text-secondary)", fontSize: 13 }}>
          No picks match current filters
        </span>
      </div>
    );
  }

  let wins = 0;
  let totalPnl = 0;

  for (const o of outcomes) {
    if (o.was_correct) wins++;
    const stake = mode === "personal" ? o.personal_stake : o.stake_units;
    if (stake == null) continue;
    totalPnl += o.was_correct ? stake * (o.odds - 1) : -stake;
  }

  const n = outcomes.length;
  const winRate = (wins / n) * 100;
  const pnlPositive = totalPnl >= 0;

  return (
    <div style={containerStyle}>
      <span
        style={{
          fontSize: 12,
          color: "var(--color-text-secondary)",
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          marginRight: 4,
        }}
      >
        Filtered
      </span>

      <Metric label={`${n} pick${n !== 1 ? "s" : ""}`} />
      <Sep />
      <Metric label={`${wins}W`} />
      <Sep />
      <Metric
        label={`${winRate >= 0 ? "+" : ""}${winRate.toFixed(1)}% win rate`}
        color={winRate > 0 ? "#1D9E75" : "var(--color-text-danger)"}
      />
      <Sep />
      <Metric
        label={`${pnlPositive ? "+" : ""}${totalPnl.toFixed(2)}u P&L`}
        color={pnlPositive ? "#1D9E75" : "var(--color-text-danger)"}
      />
    </div>
  );
}

function Metric({ label, color }: { label: string; color?: string }) {
  return (
    <span style={{ color: color ?? "var(--color-text-primary)", fontWeight: color ? 500 : 400 }}>
      {label}
    </span>
  );
}

function Sep() {
  return <span style={{ color: "var(--color-border-secondary)" }}>·</span>;
}
