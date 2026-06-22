export interface PickResponse {
  id: string;
  event_key: string;
  valid_for_date: string;
  sport: string;
  league: string;
  pick: string;
  matchup: string;
  confidence: number;
  ev: number;
  odds: number;
  stake_units: number;
  justification: string | null;
  followed: boolean;
  status: "active" | "expired" | "resolved" | "void";
  outcome: "won" | "lost" | "void" | null;
  score: string | null;
}

export interface OutcomeResponse {
  signal_id: string;
  valid_for_date: string;
  sport: string;
  league: string;
  pick: string;
  matchup: string;
  was_correct: boolean;
  score: string;
  ev: number;
  confidence: number;
  odds: number;
  stake_units: number;
  followed: boolean;
  personal_stake: number | null;
  model_version: string;
}

export interface PnlResponse {
  picks: number;
  wins: number;
  win_rate: number;
  kelly_roi: number;
}

export interface RefreshResponse {
  status: string;
}

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const API_KEY = process.env.API_KEY ?? process.env.NEXT_PUBLIC_API_KEY ?? ''

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      "X-API-Key": API_KEY,
      "Content-Type": "application/json",
      ...(options.headers as Record<string, string>),
    },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function getPicks(
  date: string,
  sport?: string,
  league?: string
): Promise<PickResponse[]> {
  const p = new URLSearchParams({ date });
  if (sport) p.set("sport", sport);
  if (league) p.set("league", league);
  return apiFetch(`/picks?${p}`);
}

export function getOutcomes(
  date?: string,
  modelVersion?: string
): Promise<OutcomeResponse[]> {
  const p = new URLSearchParams();
  if (date) p.set("date", date);
  if (modelVersion) p.set("model_version", modelVersion);
  const qs = p.toString();
  return apiFetch(`/outcomes${qs ? `?${qs}` : ""}`);
}

export function followSignal(id: string, stake: number): Promise<PickResponse> {
  return apiFetch(`/signals/${id}/follow`, {
    method: "POST",
    body: JSON.stringify({ stake }),
  });
}

export function unfollowSignal(id: string): Promise<void> {
  return apiFetch(`/signals/${id}/follow`, { method: "DELETE" });
}

export function getGlobalPnl(modelVersion?: string): Promise<PnlResponse> {
  const p = new URLSearchParams();
  if (modelVersion) p.set("model_version", modelVersion);
  const qs = p.toString();
  return apiFetch(`/pnl/global${qs ? `?${qs}` : ""}`);
}

export function getPersonalPnl(modelVersion?: string): Promise<PnlResponse> {
  const p = new URLSearchParams();
  if (modelVersion) p.set("model_version", modelVersion);
  const qs = p.toString();
  return apiFetch(`/pnl/personal${qs ? `?${qs}` : ""}`);
}

export function postRefresh(): Promise<RefreshResponse> {
  return apiFetch(`/refresh`, { method: "POST" });
}
