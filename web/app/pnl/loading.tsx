const shimmer: React.CSSProperties = {
  background:
    "linear-gradient(90deg, var(--color-background-secondary) 25%, var(--color-background-tertiary, #f3f4f6) 50%, var(--color-background-secondary) 75%)",
  backgroundSize: "200% 100%",
  animation: "shimmer 1.4s infinite",
  borderRadius: 6,
};

export default function PnlLoading() {
  return (
    <div>
      <div
        style={{ ...shimmer, width: 60, height: 22, marginBottom: 20, borderRadius: 4 }}
      />

      {/* Metric cards skeleton */}
      <div style={{ display: "flex", gap: 12, marginBottom: 20 }}>
        {[0, 1].map((i) => (
          <div
            key={i}
            style={{
              flex: 1,
              height: 110,
              border: "0.5px solid var(--color-border-tertiary)",
              borderRadius: 8,
              padding: "14px 18px",
              display: "flex",
              flexDirection: "column",
              gap: 12,
            }}
          >
            <div style={{ ...shimmer, width: 60, height: 11, borderRadius: 3 }} />
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px 24px" }}>
              {[0, 1, 2, 3].map((j) => (
                <div key={j}>
                  <div style={{ ...shimmer, width: 40, height: 10, marginBottom: 4, borderRadius: 3 }} />
                  <div style={{ ...shimmer, width: 56, height: 18, borderRadius: 3 }} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Chart skeleton */}
      <div
        style={{
          height: 220,
          border: "0.5px solid var(--color-border-tertiary)",
          borderRadius: 8,
          marginBottom: 20,
          ...shimmer,
        }}
      />

      {/* FilterBar skeleton */}
      <div
        style={{
          height: 46,
          border: "0.5px solid var(--color-border-tertiary)",
          borderRadius: 8,
          marginBottom: 14,
          ...shimmer,
        }}
      />

      {/* Table skeleton rows */}
      <div
        style={{
          border: "0.5px solid var(--color-border-tertiary)",
          borderRadius: 8,
          overflow: "hidden",
        }}
      >
        {[0, 1, 2, 3, 4].map((i) => (
          <div
            key={i}
            style={{
              height: 44,
              borderBottom: i < 4 ? "0.5px solid var(--color-border-tertiary)" : undefined,
              padding: "0 10px",
              display: "flex",
              alignItems: "center",
              gap: 16,
            }}
          >
            <div style={{ ...shimmer, width: 80, height: 12, borderRadius: 3 }} />
            <div style={{ ...shimmer, flex: 1, height: 12, borderRadius: 3 }} />
            <div style={{ ...shimmer, width: 60, height: 12, borderRadius: 3 }} />
            <div style={{ ...shimmer, width: 40, height: 12, borderRadius: 3 }} />
            <div style={{ ...shimmer, width: 50, height: 12, borderRadius: 3 }} />
          </div>
        ))}
      </div>

      <style>{`
        @keyframes shimmer {
          0% { background-position: 200% 0; }
          100% { background-position: -200% 0; }
        }
      `}</style>
    </div>
  );
}
