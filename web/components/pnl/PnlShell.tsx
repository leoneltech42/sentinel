"use client";

import { useEffect, useMemo, useState } from "react";
import type { OutcomeResponse, PnlResponse } from "@/lib/api";
import { getOutcomes } from "@/lib/api";
import MetricCards from "./MetricCards";
import PnlChart, { type ChartPoint } from "./PnlChart";
import FilterBar, { type Filters } from "./FilterBar";
import OutcomesTable from "./OutcomesTable";

type Mode = "global" | "personal";

function confidenceToStars(c: number): number {
  if (c >= 0.8) return 5;
  if (c >= 0.7) return 4;
  if (c >= 0.6) return 3;
  if (c >= 0.5) return 2;
  return 1;
}

export default function PnlShell({
  globalPnl,
  personalPnl,
}: {
  globalPnl: PnlResponse | null;
  personalPnl: PnlResponse | null;
}) {
  const [mode, setMode] = useState<Mode>("global");
  const [outcomes, setOutcomes] = useState<OutcomeResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<Filters>({
    dateFrom: "",
    dateTo: "",
    sport: "",
    league: "",
    minStars: 1,
  });

  useEffect(() => {
    getOutcomes()
      .then(setOutcomes)
      .catch(() => setOutcomes([]))
      .finally(() => setLoading(false));
  }, []);

  const sports = useMemo(
    () => [...new Set(outcomes.map((o) => o.sport).filter(Boolean))].sort(),
    [outcomes]
  );

  const leagues = useMemo(
    () => [...new Set(outcomes.map((o) => o.league).filter(Boolean))].sort(),
    [outcomes]
  );

  function handleFilterChange(key: keyof Filters, value: string | number) {
    setFilters((prev) => ({ ...prev, [key]: value }));
  }

  const filtered = useMemo(() => {
    return outcomes.filter((o) => {
      if (filters.dateFrom && o.valid_for_date < filters.dateFrom) return false;
      if (filters.dateTo && o.valid_for_date > filters.dateTo) return false;
      if (filters.sport && o.sport !== filters.sport) return false;
      if (filters.league && o.league !== filters.league) return false;
      if (confidenceToStars(o.confidence) < filters.minStars) return false;
      if (mode === "personal" && !o.followed) return false;
      return true;
    });
  }, [outcomes, filters, mode]);

  const chartData = useMemo((): ChartPoint[] => {
    let cumulative = 0;
    return filtered.map((o) => {
      const stake = mode === "personal" ? o.personal_stake : o.stake_units;
      const pnl =
        stake != null
          ? o.was_correct
            ? stake * (o.odds - 1)
            : -stake
          : 0;
      cumulative += pnl;
      return {
        date: o.valid_for_date,
        matchup: o.matchup,
        pick: o.pick,
        result: o.was_correct ? "W" : "L",
        cumulative: Math.round(cumulative * 100) / 100,
      };
    });
  }, [filtered, mode]);

  return (
    <>
      <MetricCards
        globalPnl={globalPnl}
        personalPnl={personalPnl}
        activeMode={mode}
        onSelect={setMode}
      />

      {loading ? (
        <div
          style={{
            height: 220,
            background: "var(--color-background-secondary)",
            border: "0.5px solid var(--color-border-tertiary)",
            borderRadius: 8,
            marginBottom: 20,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--color-text-secondary)",
            fontSize: 14,
          }}
        >
          Loading outcomes…
        </div>
      ) : (
        <PnlChart data={chartData} />
      )}

      <FilterBar
        filters={filters}
        sports={sports}
        leagues={leagues}
        onChange={handleFilterChange}
      />

      {loading ? (
        <div
          style={{
            padding: 40,
            textAlign: "center",
            color: "var(--color-text-secondary)",
            fontSize: 14,
          }}
        >
          Loading…
        </div>
      ) : (
        <OutcomesTable outcomes={filtered} mode={mode} />
      )}
    </>
  );
}
