import type { PickResponse } from "@/lib/api";

function MetricCard({
  label,
  value,
  colored,
}: {
  label: string;
  value: string;
  colored?: boolean;
}) {
  return (
    <div
      style={{
        flex: 1,
        background: "var(--color-background-secondary)",
        border: "0.5px solid var(--color-border-tertiary)",
        borderRadius: 8,
        padding: "12px 16px",
        textAlign: "center",
      }}
    >
      <div
        style={{
          fontSize: 11,
          color: "var(--color-text-secondary)",
          textTransform: "uppercase",
          letterSpacing: "0.04em",
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 20,
          fontWeight: 600,
          color: colored
            ? value.startsWith("+") || (!value.startsWith("-") && value !== "—")
              ? "#1D9E75"
              : "var(--color-text-danger)"
            : "var(--color-text-primary)",
        }}
      >
        {value}
      </div>
    </div>
  );
}

export default function SummaryBar({ picks }: { picks: PickResponse[] }) {
  const following = picks.filter((p) => p.followed).length;

  const resolved = picks.filter(
    (p) => p.outcome === "won" || p.outcome === "lost"
  );
  const wins = resolved.filter((p) => p.outcome === "won").length;

  let winRateStr = "—";
  let kellyRoiStr = "—";
  let winRateColored = false;
  let kellyColored = false;

  if (resolved.length > 0) {
    const wr = (wins / resolved.length) * 100;
    winRateStr = `${wr.toFixed(1)}%`;
    winRateColored = true;

    let totalPnl = 0;
    let totalStaked = 0;
    for (const p of resolved) {
      const stake = p.stake_units || 1;
      if (p.outcome === "won") totalPnl += stake * (p.odds - 1);
      else totalPnl -= stake;
      totalStaked += stake;
    }
    const kelly = totalStaked > 0 ? (totalPnl / totalStaked) * 100 : 0;
    kellyRoiStr = `${kelly >= 0 ? "+" : ""}${kelly.toFixed(1)}%`;
    kellyColored = true;
  }

  return (
    <div
      style={{
        display: "flex",
        gap: 10,
        marginBottom: 24,
      }}
    >
      <MetricCard label="Picks" value={String(picks.length)} />
      <MetricCard label="Following" value={String(following)} />
      <MetricCard
        label="Win Rate"
        value={winRateStr}
        colored={winRateColored}
      />
      <MetricCard
        label="Kelly ROI"
        value={kellyRoiStr}
        colored={kellyColored}
      />
    </div>
  );
}
