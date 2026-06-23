import { getConfig, getGlobalPnl, getPersonalPnl } from "@/lib/api";
import PnlShell from "@/components/pnl/PnlShell";

// Falls back to the same default api/lib/versioning.py uses when
// PRODUCTION_MODEL_BASELINE is unset, so the label still makes sense if
// GET /config is ever unreachable.
const FALLBACK_BASELINE = "poisson_v0.3.0";

export default async function PnlPage() {
  // No model_version param -> server applies its own PRODUCTION_MODEL_BASELINE
  // floor (api/lib/versioning.py) rather than this page hardcoding an exact
  // version string that goes stale the moment a new model version ships.
  const [globalPnl, personalPnl, config] = await Promise.allSettled([
    getGlobalPnl(),
    getPersonalPnl(),
    getConfig(),
  ]);

  return (
    <div>
      <h1
        style={{
          fontSize: 18,
          fontWeight: 600,
          marginBottom: 20,
          color: "var(--color-text-primary)",
        }}
      >
        P&amp;L
      </h1>
      <PnlShell
        globalPnl={globalPnl.status === "fulfilled" ? globalPnl.value : null}
        personalPnl={
          personalPnl.status === "fulfilled" ? personalPnl.value : null
        }
        productionBaseline={
          config.status === "fulfilled"
            ? config.value.production_model_baseline
            : FALLBACK_BASELINE
        }
      />
    </div>
  );
}
