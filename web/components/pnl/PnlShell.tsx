"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { OutcomeResponse, PnlResponse } from "@/lib/api";
import { getGlobalPnl, getOutcomes, getPersonalPnl } from "@/lib/api";
import { confidenceToStars } from "@/lib/utils";
import MetricCards from "./MetricCards";
import FilteredMetrics from "./FilteredMetrics";
import PnlChart, { type ChartPoint } from "./PnlChart";
import FilterBar, { PRODUCTION_FILTER_VALUE, type Filters } from "./FilterBar";
import OutcomesTable from "./OutcomesTable";

type Mode = "global" | "personal";

const DEFAULT_VERSION = PRODUCTION_FILTER_VALUE;

// PRODUCTION_FILTER_VALUE is a UI-only sentinel -- translate it to "omit the
// param" so the server applies its own PRODUCTION_MODEL_BASELINE floor
// instead of us sending a hardcoded exact version string.
function apiVersionParam(modelVersion: string): string | undefined {
  return modelVersion === PRODUCTION_FILTER_VALUE ? undefined : modelVersion;
}

export default function PnlShell({
  globalPnl: initialGlobalPnl,
  personalPnl: initialPersonalPnl,
  productionBaseline,
}: {
  globalPnl: PnlResponse | null;
  personalPnl: PnlResponse | null;
  productionBaseline: string;
}) {
  const [mode, setMode] = useState<Mode>("global");
  const [outcomes, setOutcomes] = useState<OutcomeResponse[]>([]);
  const [allVersionOutcomes, setAllVersionOutcomes] = useState<OutcomeResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [modelVersion, setModelVersion] = useState(DEFAULT_VERSION);
  const [globalPnl, setGlobalPnl] = useState<PnlResponse | null>(initialGlobalPnl);
  const [personalPnl, setPersonalPnl] = useState<PnlResponse | null>(initialPersonalPnl);
  const [filters, setFilters] = useState<Filters>({
    dateFrom: "",
    dateTo: "",
    sport: "",
    league: "",
  });
  const [selectedStars, setSelectedStars] = useState<number[]>([1, 2, 3, 4, 5]);

  // Mount: fetch all-versions (for dropdown) + default-version outcomes in parallel
  useEffect(() => {
    setLoading(true);
    Promise.all([
      getOutcomes(undefined, "all").catch(() => [] as OutcomeResponse[]),
      getOutcomes(undefined, apiVersionParam(DEFAULT_VERSION)).catch(() => [] as OutcomeResponse[]),
    ]).then(([allRows, defaultRows]) => {
      setAllVersionOutcomes(allRows);
      setOutcomes(defaultRows);
      setLoading(false);
    });
  }, []);

  // Model version change: re-fetch outcomes + metric cards (skip on first render)
  const isFirstVersionChange = useRef(true);
  useEffect(() => {
    if (isFirstVersionChange.current) {
      isFirstVersionChange.current = false;
      return;
    }
    setLoading(true);
    const versionParam = apiVersionParam(modelVersion);
    Promise.all([
      getOutcomes(undefined, versionParam).catch(() => [] as OutcomeResponse[]),
      getGlobalPnl(versionParam).catch(() => null),
      getPersonalPnl(versionParam).catch(() => null),
    ]).then(([newOutcomes, newGlobal, newPersonal]) => {
      setOutcomes(newOutcomes);
      setGlobalPnl(newGlobal);
      setPersonalPnl(newPersonal);
      setLoading(false);
    });
  }, [modelVersion]);

  // Derive available model versions from the all-versions fetch (sorted desc)
  const modelVersions = useMemo(() => {
    const versions = [
      ...new Set(allVersionOutcomes.map((o) => o.model_version).filter(Boolean)),
    ];
    return versions.sort().reverse();
  }, [allVersionOutcomes]);

  const sports = useMemo(
    () => [...new Set(outcomes.map((o) => o.sport).filter(Boolean))].sort(),
    [outcomes]
  );

  const leagues = useMemo(
    () => [...new Set(outcomes.map((o) => o.league).filter(Boolean))].sort(),
    [outcomes]
  );

  function handleFilterChange(key: keyof Filters, value: string) {
    setFilters((prev) => ({ ...prev, [key]: value }));
  }

  function handleResetFilters() {
    setFilters({ dateFrom: "", dateTo: "", sport: "", league: "" });
    setSelectedStars([1, 2, 3, 4, 5]);
    setModelVersion(DEFAULT_VERSION);
  }

  const filtered = useMemo(() => {
    return outcomes.filter((o) => {
      if (filters.dateFrom && o.valid_for_date < filters.dateFrom) return false;
      if (filters.dateTo && o.valid_for_date > filters.dateTo) return false;
      if (filters.sport && o.sport !== filters.sport) return false;
      if (filters.league && o.league !== filters.league) return false;
      if (!selectedStars.includes(confidenceToStars(o.confidence))) return false;
      if (mode === "personal" && !o.followed) return false;
      return true;
    });
  }, [outcomes, filters, selectedStars, mode]);

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

      <FilteredMetrics outcomes={filtered} mode={mode} />

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
        modelVersion={modelVersion}
        modelVersions={modelVersions}
        onModelVersionChange={setModelVersion}
        selectedStars={selectedStars}
        onStarsChange={setSelectedStars}
        onResetFilters={handleResetFilters}
        productionBaseline={productionBaseline}
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
