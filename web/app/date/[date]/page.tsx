import { getPicks } from "@/lib/api";
import DateNav from "@/components/DateNav";
import SummaryBar from "@/components/SummaryBar";
import PickCard from "@/components/PickCard";

export default async function DatePage({
  params,
}: {
  params: Promise<{ date: string }>;
}) {
  const { date } = await params;
  const today = new Date().toISOString().split("T")[0];

  let picks = await getPicks(date).catch(() => null);

  if (!picks) {
    return (
      <>
        <DateNav date={date} today={today} />
        <p
          style={{
            textAlign: "center",
            color: "var(--color-text-secondary)",
            marginTop: 48,
            fontSize: 14,
          }}
        >
          Could not load picks. Check API connection.
        </p>
      </>
    );
  }

  // Sort by confidence desc, then ev desc
  picks = [...picks].sort(
    (a, b) => b.confidence - a.confidence || b.ev - a.ev
  );

  const isPast = date < today;

  return (
    <>
      <DateNav date={date} today={today} />
      <SummaryBar picks={picks} />
      {picks.length === 0 ? (
        <p
          style={{
            textAlign: "center",
            color: "var(--color-text-secondary)",
            marginTop: 48,
            fontSize: 14,
          }}
        >
          No picks for this date.
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {picks.map((pick) => (
            <PickCard key={pick.id} pick={pick} isPast={isPast} />
          ))}
        </div>
      )}
    </>
  );
}
