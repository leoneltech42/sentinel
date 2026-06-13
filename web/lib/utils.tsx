import type { ReactElement } from "react";

export function confidenceToStars(c: number): number {
  if (c >= 0.9) return 5;
  if (c >= 0.8) return 4;
  if (c >= 0.7) return 3;
  if (c >= 0.6) return 2;
  return 1;
}

export function renderStars(starLevel: number): ReactElement {
  return (
    <span style={{ letterSpacing: 1 }}>
      {Array.from({ length: 5 }, (_, i) => (
        <span
          key={i}
          style={{ color: i < starLevel ? "#1D9E75" : "var(--color-text-secondary)" }}
        >
          {i < starLevel ? "★" : "☆"}
        </span>
      ))}
    </span>
  );
}
