export interface Filters {
  dateFrom: string;
  dateTo: string;
  sport: string;
  league: string;
  minStars: number;
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

export default function FilterBar({
  filters,
  sports,
  leagues,
  onChange,
}: {
  filters: Filters;
  sports: string[];
  leagues: string[];
  onChange: (key: keyof Filters, value: string | number) => void;
}) {
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

      <FilterGroup label="Min confidence">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <input
            type="range"
            min={1}
            max={5}
            step={1}
            value={filters.minStars}
            onChange={(e) => onChange("minStars", Number(e.target.value))}
            style={{ width: 80, accentColor: "#1D9E75" }}
          />
          <span style={{ fontSize: 12, color: "var(--color-text-secondary)", whiteSpace: "nowrap" }}>
            {filters.minStars}★ or more
          </span>
        </div>
      </FilterGroup>
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
      <span style={{ fontSize: 12, color: "var(--color-text-secondary)", whiteSpace: "nowrap" }}>
        {label}
      </span>
      {children}
    </div>
  );
}
