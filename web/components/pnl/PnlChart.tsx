"use client";

import { useEffect, useRef } from "react";
import { Chart, registerables } from "chart.js";

Chart.register(...registerables);

export interface ChartPoint {
  date: string;
  matchup: string;
  pick: string;
  result: "W" | "L";
  cumulative: number;
}

export default function PnlChart({ data }: { data: ChartPoint[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chartRef = useRef<Chart | null>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    chartRef.current?.destroy();
    chartRef.current = null;

    if (!data.length) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const style = getComputedStyle(document.documentElement);
    const gridColor =
      style.getPropertyValue("--color-border-tertiary").trim() || "#e5e7eb";
    const tickColor =
      style.getPropertyValue("--color-text-secondary").trim() || "#6b7280";
    const dangerColor =
      style.getPropertyValue("--color-text-danger").trim() || "#A32D2D";

    const lastVal = data[data.length - 1].cumulative;
    const lineColor = lastVal >= 0 ? "#1D9E75" : dangerColor;

    chartRef.current = new Chart(ctx, {
      type: "line",
      data: {
        labels: data.map((_, i) => i),
        datasets: [
          {
            data: data.map((d) => d.cumulative),
            borderColor: lineColor,
            backgroundColor: lineColor + "18",
            tension: 0.3,
            pointRadius: 3,
            pointHoverRadius: 6,
            fill: "origin",
            borderWidth: 2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title(items) {
                return data[items[0].dataIndex]?.date ?? "";
              },
              label(item) {
                const d = data[item.dataIndex];
                if (!d) return "";
                const sign = d.cumulative >= 0 ? "+" : "";
                return [
                  d.matchup,
                  `${d.pick} — ${d.result === "W" ? "✓ Won" : "✗ Lost"}`,
                  `Cumulative: ${sign}${d.cumulative.toFixed(2)}u`,
                ];
              },
            },
          },
        },
        scales: {
          x: { display: false },
          y: {
            grid: { color: gridColor },
            ticks: {
              color: tickColor,
              callback(v) {
                return `${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(1)}u`;
              },
            },
          },
        },
      },
    });

    return () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    };
  }, [data]);

  return (
    <div
      style={{
        height: 220,
        position: "relative",
        border: "0.5px solid var(--color-border-tertiary)",
        borderRadius: 8,
        padding: "12px 16px 8px",
        background: "var(--color-background-secondary)",
        marginBottom: 20,
      }}
    >
      {data.length === 0 ? (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 14,
            color: "var(--color-text-secondary)",
          }}
        >
          No resolved picks yet
        </div>
      ) : (
        <canvas ref={canvasRef} />
      )}
    </div>
  );
}
