import type { PnlResponse } from "@/lib/api";

type Mode = "global" | "personal";

function fmt(n: number, isRate: boolean): { text: string; positive: boolean; zero: boolean } {
  const val = isRate ? n * 100 : n;
  const text = isRate
    ? `${val >= 0 ? "+" : ""}${val.toFixed(1)}%`
    : String(Math.round(val));
  return { text, positive: val > 0, zero: val === 0 };
}

function PnlCard({
  label,
  pnl,
  active,
  onClick,
}: {
  label: string;
  pnl: PnlResponse | null;
  active: boolean;
  onClick: () => void;
}) {
  const wr = pnl ? fmt(pnl.win_rate, true) : null;
  const roi = pnl ? fmt(pnl.kelly_roi, true) : null;

  const metricColor = (f: { positive: boolean; zero: boolean } | null) => {
    if (!f) return "var(--color-text-secondary)";
    if (f.zero) return "var(--color-text-secondary)";
    return f.positive ? "#1D9E75" : "var(--color-text-danger)";
  };

  return (
    <button
      onClick={onClick}
      style={{
        flex: 1,
        textAlign: "left",
        background: "var(--color-background-secondary)",
        border: `${active ? "2px" : "0.5px"} solid ${active ? "#1D9E75" : "var(--color-border-tertiary)"}`,
        borderRadius: 8,
        padding: "14px 18px",
        cursor: "pointer",
        transition: "border-color 0.15s",
      }}
    >
      <div
        style={{
          fontSize: 11,
          fontWeight: 600,
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          color: active ? "#1D9E75" : "var(--color-text-secondary)",
          marginBottom: 12,
        }}
      >
        {label}
      </div>
      {pnl ? (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 24px" }}>
          <Stat label="Picks" value={String(pnl.picks)} />
          <Stat label="Wins" value={String(pnl.wins)} />
          <Stat label="Win rate" value={wr!.text} color={metricColor(wr)} />
          <Stat label="Kelly ROI" value={roi!.text} color={metricColor(roi)} />
        </div>
      ) : (
        <p style={{ fontSize: 13, color: "var(--color-text-secondary)", margin: 0 }}>
          Unavailable
        </p>
      )}
    </button>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 600, color: color ?? "var(--color-text-primary)" }}>
        {value}
      </div>
    </div>
  );
}

export default function MetricCards({
  globalPnl,
  personalPnl,
  activeMode,
  onSelect,
}: {
  globalPnl: PnlResponse | null;
  personalPnl: PnlResponse | null;
  activeMode: Mode;
  onSelect: (mode: Mode) => void;
}) {
  return (
    <div style={{ display: "flex", gap: 12, marginBottom: 20 }}>
      <PnlCard
        label="Global"
        pnl={globalPnl}
        active={activeMode === "global"}
        onClick={() => onSelect("global")}
      />
      <PnlCard
        label="Personal"
        pnl={personalPnl}
        active={activeMode === "personal"}
        onClick={() => onSelect("personal")}
      />
    </div>
  );
}
