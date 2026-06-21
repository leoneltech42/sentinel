import { getGlobalPnl, getPersonalPnl } from "@/lib/api";
import PnlShell from "@/components/pnl/PnlShell";

export default async function PnlPage() {
  // No model_version param -> server applies its own PRODUCTION_MODEL_BASELINE
  // floor (api/lib/versioning.py) rather than this page hardcoding an exact
  // version string that goes stale the moment a new model version ships.
  const [globalPnl, personalPnl] = await Promise.allSettled([
    getGlobalPnl(),
    getPersonalPnl(),
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
      />
    </div>
  );
}
