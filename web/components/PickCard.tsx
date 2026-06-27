"use client";

import { useState } from "react";
import type { PickResponse } from "@/lib/api";
import FollowModal from "@/components/FollowModal";
import { confidenceToStars, renderStars } from "@/lib/utils";

function OutcomeBadge({
  outcome,
  status,
}: {
  outcome: PickResponse["outcome"];
  status: PickResponse["status"];
}) {
  if (outcome === "won" || outcome === "lost" || outcome === "void") {
    const map = {
      won: { bg: "#EAF3DE", color: "#3B6D11", label: "Won" },
      lost: { bg: "#FCEBEB", color: "#A32D2D", label: "Lost" },
      void: { bg: "var(--color-background-secondary)", color: "var(--color-text-secondary)", label: "Void" },
    } as const;
    const s = map[outcome];
    return (
      <span style={{ fontSize: 11, fontWeight: 500, background: s.bg, color: s.color, borderRadius: 99, padding: "2px 8px", whiteSpace: "nowrap" }}>
        {s.label}
      </span>
    );
  }

  if (status === "expired") {
    return (
      <>
        <style>{`@keyframes sentinel-pulse{0%,100%{opacity:1}50%{opacity:.5}}`}</style>
        <span
          style={{
            fontSize: 11,
            fontWeight: 500,
            background: "var(--color-background-secondary)",
            color: "var(--color-text-secondary)",
            borderRadius: 99,
            padding: "2px 8px",
            whiteSpace: "nowrap",
            animation: "sentinel-pulse 2s ease-in-out infinite",
          }}
        >
          In progress
        </span>
      </>
    );
  }

  return (
    <span style={{ fontSize: 11, background: "var(--color-background-secondary)", color: "var(--color-text-secondary)", borderRadius: 99, padding: "2px 8px" }}>
      Pending
    </span>
  );
}

type ModalMode = "follow" | "unfollow" | null;

export default function PickCard({
  pick: initialPick,
  isPast,
}: {
  pick: PickResponse;
  isPast: boolean;
}) {
  const [pick, setPick] = useState(initialPick);
  const [followed, setFollowed] = useState(initialPick.followed);
  // personal_stake (what you actually staked, locked in at follow time) takes
  // priority over stake_units (the model's current, possibly-since-changed
  // suggestion) -- otherwise this card silently drifts to the model's latest
  // number after a refresh upserts the signal, even though your real stake
  // never changed.
  const [stake, setStake] = useState(initialPick.personal_stake ?? initialPick.stake_units);
  const [modal, setModal] = useState<ModalMode>(null);

  const edge =
    pick.odds > 0 ? (pick.confidence - 1 / pick.odds) * 100 : null;

  const evLabel = `${pick.ev >= 0 ? "+" : ""}${(pick.ev * 100).toFixed(1)}%`;
  const edgeLabel = edge !== null ? `${edge >= 0 ? "+" : ""}${edge.toFixed(1)}%` : "—";
  const stakeLabel = `${stake.toFixed(1)}u`;

  const cardStyle: React.CSSProperties = {
    border: "0.5px solid var(--color-border-tertiary)",
    borderRadius: 8,
    padding: "14px 16px",
    borderLeft: followed ? "3px solid #1D9E75" : "0.5px solid var(--color-border-tertiary)",
  };

  const handleFollowed = (updated: PickResponse) => {
    setPick(updated);
    setFollowed(true);
    setStake(updated.personal_stake ?? updated.stake_units);
    setModal(null);
  };

  const handleUnfollowed = () => {
    setFollowed(false);
    setModal(null);
  };

  return (
    <>
      <div style={cardStyle}>
        {/* Header row */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "flex-start",
            marginBottom: 10,
            gap: 12,
          }}
        >
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 500,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  color: "var(--color-text-secondary)",
                  background: "var(--color-background-secondary)",
                  border: "0.5px solid var(--color-border-tertiary)",
                  borderRadius: 4,
                  padding: "1px 6px",
                }}
              >
                {pick.league.toUpperCase()}
              </span>
              <span
                style={{
                  fontSize: 13,
                  color: "var(--color-text-secondary)",
                }}
              >
                {pick.matchup}
              </span>
            </div>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span style={{ fontSize: 15, fontWeight: 500 }}>{pick.pick}</span>
              <span style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
                @ {pick.odds.toFixed(2)}
              </span>
            </div>
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              flexShrink: 0,
              paddingTop: 2,
            }}
          >
            <OutcomeBadge outcome={pick.outcome} status={pick.status} />
            <span style={{ fontSize: 13 }}>{renderStars(confidenceToStars(pick.confidence))}</span>
          </div>
        </div>

        {/* Meta row */}
        <div
          style={{
            display: "flex",
            gap: 20,
            marginBottom: pick.justification ? 12 : 10,
            fontSize: 13,
          }}
        >
          <span>
            <span style={{ color: "var(--color-text-secondary)" }}>EV </span>
            <span style={{ color: "#1D9E75", fontWeight: 500 }}>{evLabel}</span>
          </span>
          <span>
            <span style={{ color: "var(--color-text-secondary)" }}>Edge </span>
            <span style={{ color: "#1D9E75", fontWeight: 500 }}>{edgeLabel}</span>
          </span>
          <span>
            <span style={{ color: "var(--color-text-secondary)" }}>Stake </span>
            <span style={{ fontWeight: 500 }}>{stakeLabel}</span>
          </span>
        </div>

        {/* Justification */}
        {pick.justification && (
          <div
            style={{
              borderLeft: "2px solid #1D9E75",
              background: "var(--color-background-secondary)",
              padding: "8px 10px",
              fontSize: 12,
              lineHeight: 1.5,
              color: "var(--color-text-secondary)",
              borderRadius: "0 4px 4px 0",
              marginBottom: 12,
            }}
          >
            {pick.justification}
          </div>
        )}

        {/* Footer row */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          {/* Follow button or plain text */}
          <div>
            {isPast ? (
              followed ? (
                <span
                  style={{ fontSize: 13, color: "#3B6D11", fontWeight: 500 }}
                >
                  Following · {stake.toFixed(1)}u
                </span>
              ) : null
            ) : followed ? (
              <button
                onClick={() => setModal("unfollow")}
                style={{
                  fontSize: 13,
                  fontWeight: 500,
                  background: "#EAF3DE",
                  color: "#3B6D11",
                  border: "1px solid #9FE1CB",
                  borderRadius: 6,
                  padding: "5px 12px",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 5,
                }}
              >
                <BookmarkIcon filled /> Following · {stake.toFixed(1)}u
              </button>
            ) : (
              <button
                onClick={() => setModal("follow")}
                style={{
                  fontSize: 13,
                  background: "none",
                  color: "var(--color-text-secondary)",
                  border: "1px solid var(--color-border-secondary)",
                  borderRadius: 6,
                  padding: "5px 12px",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 5,
                }}
              >
                <BookmarkIcon filled={false} /> Follow
              </button>
            )}
          </div>

          {/* Right side: result or suggested */}
          <div style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
            {pick.outcome === "won" || pick.outcome === "lost" ? (
              <span>
                Result:{" "}
                <span style={{ color: "var(--color-text-primary)" }}>
                  {pick.score ?? "—"}{" "}
                  {pick.outcome === "won" ? "✓" : "✗"}
                </span>
              </span>
            ) : pick.outcome === null && !followed ? (
              <span>Suggested: {pick.stake_units.toFixed(1)}u</span>
            ) : null}
          </div>
        </div>
      </div>

      {modal && (
        <FollowModal
          pick={pick}
          mode={modal}
          currentStake={stake}
          onClose={() => setModal(null)}
          onFollowed={handleFollowed}
          onUnfollowed={handleUnfollowed}
        />
      )}
    </>
  );
}

function BookmarkIcon({ filled }: { filled: boolean }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 13 13"
      fill={filled ? "#3B6D11" : "none"}
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2 1h9v11L6.5 9 2 12V1z" />
    </svg>
  );
}
