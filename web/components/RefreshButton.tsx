"use client";

import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { getPicks, postRefresh, type PickResponse } from "@/lib/api";

type RefreshState = "idle" | "loading" | "success" | "error";

const POLL_INTERVAL_MS = 3000;
const POLL_TIMEOUT_MS = 30000;

function didPicksChange(baseline: PickResponse[], current: PickResponse[]): boolean {
  if (baseline.length !== current.length) return true;
  return current.some((pick) => {
    const prev = baseline.find((b) => b.id === pick.id);
    return !prev || prev.ev !== pick.ev || prev.odds !== pick.odds;
  });
}

export default function RefreshButton() {
  const pathname = usePathname();
  const router = useRouter();
  const [state, setState] = useState<RefreshState>("idle");

  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelled = useRef(false);

  useEffect(() => {
    return () => {
      cancelled.current = true;
      if (resetTimer.current) clearTimeout(resetTimer.current);
      if (pollTimer.current) clearTimeout(pollTimer.current);
    };
  }, []);

  const today = new Date().toISOString().split("T")[0];
  // "/" always redirects to "/date/{today}" (see app/page.tsx) -- the only
  // routes that should show this button are the picks page itself, and
  // only when viewing today (not /pnl, not a past/future date).
  const isPicksToday = pathname === "/" || pathname === `/date/${today}`;

  if (!isPicksToday) return null;

  function scheduleReset(ms: number) {
    if (resetTimer.current) clearTimeout(resetTimer.current);
    resetTimer.current = setTimeout(() => {
      if (!cancelled.current) setState("idle");
    }, ms);
  }

  function finish(result: "success" | "error") {
    if (cancelled.current) return;
    setState(result);
    if (result === "success") {
      router.refresh();
      scheduleReset(3000);
    } else {
      scheduleReset(5000);
    }
  }

  async function handleClick() {
    if (state === "loading") return;
    setState("loading");

    try {
      await postRefresh();
    } catch {
      finish("error");
      return;
    }

    // The endpoint returns immediately ({"status": "started"}) while the
    // actual refresh runs server-side -- poll for the effect rather than
    // waiting on the request itself.
    const baseline = await getPicks(today).catch(() => null);
    const startedAt = Date.now();

    const poll = async () => {
      if (cancelled.current) return;
      if (Date.now() - startedAt >= POLL_TIMEOUT_MS) {
        finish("success");
        return;
      }
      const current = await getPicks(today).catch(() => null);
      if (baseline && current && didPicksChange(baseline, current)) {
        finish("success");
        return;
      }
      pollTimer.current = setTimeout(poll, POLL_INTERVAL_MS);
    };
    pollTimer.current = setTimeout(poll, POLL_INTERVAL_MS);
  }

  const disabled = state === "loading";

  const content: Record<RefreshState, { label: string; color: string; icon: React.ReactNode }> = {
    idle: { label: "Refresh odds", color: "var(--color-text-secondary)", icon: <RefreshIcon /> },
    loading: { label: "Refreshing…", color: "var(--color-text-secondary)", icon: <RefreshIcon spinning /> },
    success: { label: "Updated", color: "#1D9E75", icon: <CheckIcon /> },
    error: { label: "Refresh failed", color: "var(--color-text-danger)", icon: <XIcon /> },
  };
  const { label, color, icon } = content[state];

  return (
    <button
      onClick={handleClick}
      disabled={disabled}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        fontSize: 14,
        fontWeight: 400,
        color,
        background: "none",
        border: "1px solid var(--color-border-secondary)",
        borderRadius: 6,
        padding: "5px 10px",
        cursor: disabled ? "default" : "pointer",
        opacity: disabled ? 0.7 : 1,
      }}
    >
      {icon}
      {label}
    </button>
  );
}

function RefreshIcon({ spinning = false }: { spinning?: boolean }) {
  return (
    <>
      {spinning && (
        <style>{`@keyframes sentinel-spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}`}</style>
      )}
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={spinning ? { animation: "sentinel-spin 0.8s linear infinite" } : undefined}
      >
        <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
        <path d="M21 3v5h-5" />
        <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
        <path d="M3 21v-5h5" />
      </svg>
    </>
  );
}

function CheckIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function XIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 6 6 18" />
      <path d="M6 6l12 12" />
    </svg>
  );
}
