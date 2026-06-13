"use client";

import { useState } from "react";
import { followSignal, unfollowSignal, type PickResponse } from "@/lib/api";

interface FollowModalProps {
  pick: PickResponse;
  mode: "follow" | "unfollow";
  currentStake: number;
  onClose: () => void;
  onFollowed: (updated: PickResponse) => void;
  onUnfollowed: () => void;
}

export default function FollowModal({
  pick,
  mode,
  currentStake,
  onClose,
  onFollowed,
  onUnfollowed,
}: FollowModalProps) {
  const [stake, setStake] = useState(
    String(currentStake > 0 ? currentStake : pick.stake_units)
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const oddsLabel = `@ ${pick.odds.toFixed(2)}`;

  const handleConfirm = async () => {
    setError(null);
    setLoading(true);
    try {
      if (mode === "follow") {
        const stakeNum = parseFloat(stake);
        const updated = await followSignal(pick.id, isNaN(stakeNum) ? pick.stake_units : stakeNum);
        onFollowed(updated);
      } else {
        await unfollowSignal(pick.id);
        onUnfollowed();
      }
    } catch {
      setError("Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.35)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 50,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--color-background)",
          border: "1px solid var(--color-border-secondary)",
          borderRadius: 10,
          padding: "24px 24px 20px",
          width: "100%",
          maxWidth: 320,
          boxShadow: "none",
        }}
      >
        <h3
          style={{
            margin: "0 0 4px",
            fontSize: 16,
            fontWeight: 600,
            color: "var(--color-text-primary)",
          }}
        >
          {mode === "follow" ? "Follow this pick" : "Unfollow this pick?"}
        </h3>
        <p
          style={{
            margin: "0 0 20px",
            fontSize: 13,
            color: "var(--color-text-secondary)",
          }}
        >
          {mode === "follow"
            ? `${pick.pick} ${oddsLabel}`
            : `${pick.pick} ${oddsLabel} · currently ${currentStake.toFixed(1)}u`}
        </p>

        {mode === "follow" && (
          <div style={{ marginBottom: 20 }}>
            <input
              type="number"
              step="0.1"
              min="0"
              value={stake}
              onChange={(e) => setStake(e.target.value)}
              style={{
                width: 120,
                padding: "7px 10px",
                fontSize: 14,
                border: "1px solid var(--color-border-secondary)",
                borderRadius: 6,
                background: "var(--color-background-secondary)",
                color: "var(--color-text-primary)",
                outline: "none",
              }}
            />
            <p
              style={{
                margin: "6px 0 0",
                fontSize: 11,
                color: "var(--color-text-secondary)",
              }}
            >
              Suggested by model: {pick.stake_units.toFixed(1)}u · 1u = 1% bankroll
            </p>
          </div>
        )}

        {error && (
          <p
            style={{
              fontSize: 12,
              color: "var(--color-text-danger)",
              marginBottom: 12,
              margin: "0 0 12px",
            }}
          >
            {error}
          </p>
        )}

        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
          }}
        >
          <button
            onClick={onClose}
            disabled={loading}
            style={{
              padding: "7px 14px",
              fontSize: 13,
              borderRadius: 6,
              border: "1px solid var(--color-border-secondary)",
              background: "var(--color-background-secondary)",
              color: "var(--color-text-secondary)",
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
          {mode === "follow" ? (
            <button
              onClick={handleConfirm}
              disabled={loading}
              style={{
                padding: "7px 14px",
                fontSize: 13,
                borderRadius: 6,
                border: "none",
                background: "#1D9E75",
                color: "white",
                fontWeight: 500,
                cursor: loading ? "default" : "pointer",
                opacity: loading ? 0.7 : 1,
              }}
            >
              {loading ? "…" : "Confirm"}
            </button>
          ) : (
            <button
              onClick={handleConfirm}
              disabled={loading}
              style={{
                padding: "7px 14px",
                fontSize: 13,
                borderRadius: 6,
                border: "none",
                background: "var(--color-background-danger)",
                color: "var(--color-text-danger)",
                fontWeight: 500,
                cursor: loading ? "default" : "pointer",
                opacity: loading ? 0.7 : 1,
              }}
            >
              {loading ? "…" : "Unfollow"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
