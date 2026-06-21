export interface Filters {
  dateFrom: string;
  dateTo: string;
  sport: string;
  league: string;
}

const inputStyle: React.CSSProperties = {
  padding: "5px 8px",
  fontSize: 13,
  border: "1px solid var(--color-border-secondary)",
  borderRadius: 6,
  background: "var(--color-background-secondary)",
  color: "var(--color-text-primary)",
  outline: "none",
};

const selectStyle: React.CSSProperties = {
  ...inputStyle,
  cursor: "pointer",
};

const STAR_LEVELS = [1, 2, 3, 4, 5] as const;

const DEFAULT_STARS = [1, 2, 3, 4, 5];

// Sentinel value for "use the server's production floor" -- selecting this
// omits model_version from the API call entirely (see PnlShell.tsx), rather
// than sending a hardcoded exact version string that goes stale the moment
// a new model version ships. The label is a human-readable approximation of
// the current PRODUCTION_MODEL_BASELINE; it doesn't read the env var live.
export const PRODUCTION_FILTER_VALUE = "production";
export const PRODUCTION_FILTER_LABEL = "Production (v0.3.0+)";
const DEFAULT_MODEL = PRODUCTION_FILTER_VALUE;

function isDirty(
  filters: Filters,
  modelVersion: string,
  selectedStars: number[]
): boolean {
  return (
    modelVersion !== DEFAULT_MODEL ||
    filters.dateFrom !== "" ||
    filters.dateTo !== "" ||
    filters.sport !== "" ||
    filters.league !== "" ||
    selectedStars.length !== 5 ||
    !DEFAULT_STARS.every((s) => selectedStars.includes(s))
  );
}

export default function FilterBar({
  filters,
  sports,
  leagues,
  onChange,
  modelVersion,
  modelVersions,
  onModelVersionChange,
  selectedStars,
  onStarsChange,
  onResetFilters,
}: {
  filters: Filters;
  sports: string[];
  leagues: string[];
  onChange: (key: keyof Filters, value: string) => void;
  modelVersion: string;
  modelVersions: string[];
  onModelVersionChange: (v: string) => void;
  selectedStars: number[];
  onStarsChange: (stars: number[]) => void;
  onResetFilters: () => void;
}) {
  const dirty = isDirty(filters, modelVersion, selectedStars);

  function toggleStar(level: number) {
    if (selectedStars.includes(level)) {
      // Never deselect the last pill
      if (selectedStars.length === 1) return;
      onStarsChange(selectedStars.filter((s) => s !== level));
    } else {
      onStarsChange([...selectedStars, level]);
    }
  }

  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 14,
        alignItems: "center",
        padding: "12px 14px",
        background: "var(--color-background-secondary)",
        border: "0.5px solid var(--color-border-tertiary)",
        borderRadius: 8,
        marginBottom: 14,
      }}
    >
      <FilterGroup label="Model">
        <select
          value={modelVersion}
          onChange={(e) => onModelVersionChange(e.target.value)}
          style={selectStyle}
        >
          <option value={PRODUCTION_FILTER_VALUE}>{PRODUCTION_FILTER_LABEL}</option>
          <option value="all">All models</option>
          {modelVersions.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </FilterGroup>

      <FilterGroup label="From">
        <input
          type="date"
          value={filters.dateFrom}
          onChange={(e) => onChange("dateFrom", e.target.value)}
          style={inputStyle}
        />
      </FilterGroup>

      <FilterGroup label="To">
        <input
          type="date"
          value={filters.dateTo}
          onChange={(e) => onChange("dateTo", e.target.value)}
          style={inputStyle}
        />
      </FilterGroup>

      <FilterGroup label="Sport">
        <select
          value={filters.sport}
          onChange={(e) => onChange("sport", e.target.value)}
          style={selectStyle}
        >
          <option value="">All sports</option>
          {sports.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </FilterGroup>

      <FilterGroup label="League">
        <select
          value={filters.league}
          onChange={(e) => onChange("league", e.target.value)}
          style={selectStyle}
        >
          <option value="">All leagues</option>
          {leagues.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
      </FilterGroup>

      <FilterGroup label="Stars">
        <div style={{ display: "flex", gap: 4 }}>
          {STAR_LEVELS.map((level) => {
            const active = selectedStars.includes(level);
            return (
              <button
                key={level}
                onClick={() => toggleStar(level)}
                style={{
                  fontSize: 12,
                  padding: "4px 10px",
                  borderRadius: 20,
                  border: `1px solid ${active ? "#9FE1CB" : "var(--color-border-tertiary)"}`,
                  background: active ? "#EAF3DE" : "var(--color-background-secondary)",
                  color: active ? "#3B6D11" : "var(--color-text-secondary)",
                  cursor: "pointer",
                  lineHeight: 1.4,
                  transition: "background 0.1s, border-color 0.1s, color 0.1s",
                }}
              >
                {level}★
              </button>
            );
          })}
        </div>
      </FilterGroup>

      {dirty && (
        <ResetButton onClick={onResetFilters} />
      )}
    </div>
  );
}

function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span
        style={{
          fontSize: 12,
          color: "var(--color-text-secondary)",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
      {children}
    </div>
  );
}

function ResetButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        marginLeft: "auto",
        display: "flex",
        alignItems: "center",
        gap: 4,
        fontSize: 12,
        color: "var(--color-text-secondary)",
        background: "none",
        border: "none",
        cursor: "pointer",
        padding: "2px 0",
        textDecoration: "none",
        whiteSpace: "nowrap",
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLButtonElement).style.textDecoration = "underline";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLButtonElement).style.textDecoration = "none";
      }}
    >
      <svg
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
        <path d="M3 3v5h5" />
      </svg>
      Reset filters
    </button>
  );
}
