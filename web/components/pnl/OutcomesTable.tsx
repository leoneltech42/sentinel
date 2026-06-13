"use client";

import { useState } from "react";
import type { OutcomeResponse } from "@/lib/api";

type Mode = "global" | "personal";

type SortKey =
  | "valid_for_date"
  | "matchup"
  | "pick"
  | "odds"
  | "ev"
  | "confidence"
  | "stake_units"
  | "was_correct"
  | "pnl";

function confidenceToStars(c: number): number {
  if (c >= 0.8) return 5;
  if (c >= 0.7) return 4;
  if (c >= 0.6) return 3;
  if (c >= 0.5) return 2;
  return 1;
}

function Stars({ confidence }: { confidence: number }) {
  const filled = confidenceToStars(confidence);
  return (
    <span style={{ letterSpacing: 1, fontSize: 12 }}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} style={{ color: i <= filled ? "#1D9E75" : "var(--color-text-secondary)" }}>
          ★
        </span>
      ))}
    </span>
  );
}

function computePnl(row: OutcomeResponse, mode: Mode): number | null {
  const stake = mode === "personal" ? row.personal_stake : row.stake_units;
  if (stake == null) return null;
  if (row.was_correct) return stake * (row.odds - 1);
  return -stake;
}

export default function OutcomesTable({
  outcomes,
  mode,
}: {
  outcomes: OutcomeResponse[];
  mode: Mode;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("valid_for_date");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const sorted = [...outcomes].sort((a, b) => {
    let av: number | string | boolean;
    let bv: number | string | boolean;
    if (sortKey === "pnl") {
      av = computePnl(a, mode) ?? -Infinity;
      bv = computePnl(b, mode) ?? -Infinity;
    } else if (sortKey === "confidence") {
      av = a.confidence;
      bv = b.confidence;
    } else {
      av = a[sortKey] as number | string | boolean;
      bv = b[sortKey] as number | string | boolean;
    }
    if (av < bv) return sortDir === "asc" ? -1 : 1;
    if (av > bv) return sortDir === "asc" ? 1 : -1;
    return 0;
  });

  const thStyle = (key: SortKey): React.CSSProperties => ({
    padding: "8px 10px",
    textAlign: "left",
    fontSize: 11,
    fontWeight: 600,
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    color: sortKey === key ? "#1D9E75" : "var(--color-text-secondary)",
    cursor: "pointer",
    whiteSpace: "nowrap" as const,
    userSelect: "none" as const,
    borderBottom: "1px solid var(--color-border-secondary)",
  });

  const tdStyle: React.CSSProperties = {
    padding: "10px 10px",
    fontSize: 13,
    borderBottom: "0.5px solid var(--color-border-tertiary)",
    verticalAlign: "middle",
  };

  const arrow = (key: SortKey) =>
    sortKey === key ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  if (outcomes.length === 0) {
    return (
      <div
        style={{
          textAlign: "center",
          padding: 40,
          fontSize: 14,
          color: "var(--color-text-secondary)",
        }}
      >
        No outcomes match the current filters.
      </div>
    );
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>
            <th style={thStyle("valid_for_date")} onClick={() => handleSort("valid_for_date")}>
              Date{arrow("valid_for_date")}
            </th>
            <th style={thStyle("matchup")} onClick={() => handleSort("matchup")}>
              Matchup{arrow("matchup")}
            </th>
            <th style={thStyle("pick")} onClick={() => handleSort("pick")}>
              Pick{arrow("pick")}
            </th>
            <th style={{ ...thStyle("odds"), textAlign: "right" }} onClick={() => handleSort("odds")}>
              Odds{arrow("odds")}
            </th>
            <th style={{ ...thStyle("ev"), textAlign: "right" }} onClick={() => handleSort("ev")}>
              EV{arrow("ev")}
            </th>
            <th style={thStyle("confidence")} onClick={() => handleSort("confidence")}>
              Stars{arrow("confidence")}
            </th>
            <th style={{ ...thStyle("stake_units"), textAlign: "right" }} onClick={() => handleSort("stake_units")}>
              Stake{arrow("stake_units")}
            </th>
            <th style={thStyle("was_correct")} onClick={() => handleSort("was_correct")}>
              Result{arrow("was_correct")}
            </th>
            <th style={{ ...thStyle("pnl"), textAlign: "right" }} onClick={() => handleSort("pnl")}>
              P&amp;L{arrow("pnl")}
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            const pnl = computePnl(row, mode);
            const stake =
              mode === "personal" ? row.personal_stake : row.stake_units;
            return (
              <tr key={row.signal_id}>
                <td style={{ ...tdStyle, color: "var(--color-text-secondary)", whiteSpace: "nowrap" }}>
                  {row.valid_for_date}
                </td>
                <td style={{ ...tdStyle, maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {row.matchup}
                </td>
                <td style={tdStyle}>{row.pick}</td>
                <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                  {row.odds.toFixed(2)}
                </td>
                <td
                  style={{
                    ...tdStyle,
                    textAlign: "right",
                    fontVariantNumeric: "tabular-nums",
                    color: row.ev > 0 ? "#1D9E75" : "var(--color-text-danger)",
                  }}
                >
                  {row.ev >= 0 ? "+" : ""}
                  {(row.ev * 100).toFixed(1)}%
                </td>
                <td style={tdStyle}>
                  <Stars confidence={row.confidence} />
                </td>
                <td style={{ ...tdStyle, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                  {stake != null ? `${stake.toFixed(1)}u` : <span style={{ color: "var(--color-text-secondary)" }}>—</span>}
                </td>
                <td style={tdStyle}>
                  <span
                    style={{
                      display: "inline-block",
                      padding: "2px 8px",
                      borderRadius: 4,
                      fontSize: 12,
                      fontWeight: 600,
                      background: row.was_correct ? "#EAF3DE" : "#FCEBEB",
                      color: row.was_correct ? "#3B6D11" : "#A32D2D",
                    }}
                  >
                    {row.was_correct ? "Won" : "Lost"}
                  </span>
                </td>
                <td
                  style={{
                    ...tdStyle,
                    textAlign: "right",
                    fontVariantNumeric: "tabular-nums",
                    fontWeight: 600,
                    color:
                      pnl == null
                        ? "var(--color-text-secondary)"
                        : pnl > 0
                        ? "#1D9E75"
                        : "var(--color-text-danger)",
                  }}
                >
                  {pnl == null ? (
                    <span style={{ fontWeight: 400 }}>—</span>
                  ) : (
                    `${pnl >= 0 ? "+" : ""}${pnl.toFixed(2)}u`
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
