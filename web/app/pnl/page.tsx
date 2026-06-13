import { getGlobalPnl, getPersonalPnl } from "@/lib/api";
import PnlShell from "@/components/pnl/PnlShell";

export default async function PnlPage() {
  const [globalPnl, personalPnl] = await Promise.allSettled([
    getGlobalPnl("poisson_v0.3.0"),
    getPersonalPnl("poisson_v0.3.0"),
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
