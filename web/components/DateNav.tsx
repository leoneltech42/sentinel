"use client";

import { useRouter } from "next/navigation";
import { useRef } from "react";

function formatDate(dateStr: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-US", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function addDays(dateStr: string, days: number): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(y, m - 1, d + days);
  return dt.toISOString().split("T")[0];
}

export default function DateNav({
  date,
  today,
}: {
  date: string;
  today: string;
}) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);

  const goTo = (d: string) => router.push(`/date/${d}`);

  const btn = (label: string, onClick: () => void, title?: string) => (
    <button
      onClick={onClick}
      title={title}
      style={{
        background: "none",
        border: "1px solid var(--color-border-secondary)",
        borderRadius: 6,
        padding: "4px 10px",
        cursor: "pointer",
        color: "var(--color-text-secondary)",
        fontSize: 16,
        lineHeight: 1,
        display: "flex",
        alignItems: "center",
      }}
    >
      {label}
    </button>
  );

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        marginBottom: 20,
        gap: 12,
      }}
    >
      {btn("←", () => goTo(addDays(date, -1)), "Previous day")}

      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontSize: 15, fontWeight: 500 }}>{formatDate(date)}</span>
        {date === today && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 500,
              background: "#1D9E75",
              color: "white",
              borderRadius: 99,
              padding: "2px 8px",
            }}
          >
            Today
          </span>
        )}
        <button
          onClick={() => inputRef.current?.showPicker?.() ?? inputRef.current?.click()}
          title="Pick a date"
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            padding: 2,
            color: "var(--color-text-secondary)",
            display: "flex",
            alignItems: "center",
          }}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="1" y="2" width="14" height="13" rx="2" />
            <path d="M1 6h14" />
            <path d="M5 1v2M11 1v2" />
            <circle cx="5" cy="10" r="0.75" fill="currentColor" />
            <circle cx="8" cy="10" r="0.75" fill="currentColor" />
            <circle cx="11" cy="10" r="0.75" fill="currentColor" />
          </svg>
          <input
            ref={inputRef}
            type="date"
            value={date}
            onChange={(e) => { if (e.target.value) goTo(e.target.value); }}
            style={{ position: "absolute", opacity: 0, width: 0, height: 0, pointerEvents: "none" }}
            tabIndex={-1}
          />
        </button>
      </div>

      {btn("→", () => goTo(addDays(date, 1)), "Next day")}
    </div>
  );
}
