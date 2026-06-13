function SkeletonCard() {
  return (
    <div
      style={{
        border: "0.5px solid var(--color-border-tertiary)",
        borderRadius: 8,
        padding: "14px 16px",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 10 }}>
        <div>
          <div
            style={{
              height: 14,
              width: 220,
              background: "var(--color-border-tertiary)",
              borderRadius: 4,
              marginBottom: 8,
            }}
          />
          <div
            style={{
              height: 18,
              width: 160,
              background: "var(--color-border-tertiary)",
              borderRadius: 4,
            }}
          />
        </div>
        <div
          style={{
            height: 20,
            width: 80,
            background: "var(--color-border-tertiary)",
            borderRadius: 99,
          }}
        />
      </div>
      <div style={{ display: "flex", gap: 20, marginBottom: 12 }}>
        {[60, 55, 50].map((w, i) => (
          <div
            key={i}
            style={{
              height: 13,
              width: w,
              background: "var(--color-border-tertiary)",
              borderRadius: 4,
            }}
          />
        ))}
      </div>
      <div
        style={{
          height: 48,
          background: "var(--color-border-tertiary)",
          borderRadius: 4,
          marginBottom: 12,
          opacity: 0.6,
        }}
      />
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <div
          style={{
            height: 28,
            width: 90,
            background: "var(--color-border-tertiary)",
            borderRadius: 6,
          }}
        />
        <div
          style={{
            height: 13,
            width: 90,
            background: "var(--color-border-tertiary)",
            borderRadius: 4,
            alignSelf: "center",
          }}
        />
      </div>
    </div>
  );
}

export default function Loading() {
  return (
    <>
      {/* DateNav skeleton */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 20,
        }}
      >
        <div
          style={{
            height: 30,
            width: 36,
            background: "var(--color-border-tertiary)",
            borderRadius: 6,
          }}
        />
        <div
          style={{
            height: 20,
            width: 200,
            background: "var(--color-border-tertiary)",
            borderRadius: 4,
          }}
        />
        <div
          style={{
            height: 30,
            width: 36,
            background: "var(--color-border-tertiary)",
            borderRadius: 6,
          }}
        />
      </div>

      {/* SummaryBar skeleton */}
      <div style={{ display: "flex", gap: 10, marginBottom: 24 }}>
        {[1, 2, 3, 4].map((i) => (
          <div
            key={i}
            style={{
              flex: 1,
              height: 72,
              background: "var(--color-background-secondary)",
              border: "0.5px solid var(--color-border-tertiary)",
              borderRadius: 8,
            }}
          />
        ))}
      </div>

      {/* 3 skeleton cards */}
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    </>
  );
}
