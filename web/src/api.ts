import { STATIC_DEMO, demoRequest, demoSubscribe } from "./demo";
import type {
  Alert,
  Backtest,
  ChatResponse,
  ConditionsSeries,
  FieldLayers,
  GeofenceFeature,
  HarbourBoard,
  Health,
  PfzResponse,
  Port,
  ReplayEvent,
  ReplayInfo,
  ReplayTimeline,
  RiskCell,
  RiskResponse,
  RouteComparison,
} from "./types";

// Deployments that set ORCA_ADMIN_TOKEN gate the clock/replay controls; an operator stores the token once with
// localStorage.setItem("orca.adminToken", "<token>") in the browser console.
function adminHeaders(): Record<string, string> {
  try {
    const token = localStorage.getItem("orca.adminToken");
    return token ? { "X-Orca-Admin-Token": token } : {};
  } catch {
    return {};
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (STATIC_DEMO) return demoRequest<T>(path, init);
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...adminHeaders(), ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

const q = (params: Record<string, string | number | undefined | null>) => {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null) s.set(k, String(v));
  return s.toString();
};
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export interface ChatBody {
  message: string;
  session_id?: string | null;
  lat?: number;
  lon?: number;
  location_label?: string;
  language?: string | null;
}

export interface RouteBody {
  start_port?: string;
  start_lat?: number;
  start_lon?: number;
  end_lat: number;
  end_lon: number;
  departure?: string;
  speed_knots?: number;
}

export const api = {
  health: () => request<Health>("/api/health"),
  ports: () => request<Port[]>("/api/ports"),
  rules: () => request<Record<string, any>>("/api/rules"),
  geofences: () => request<{ type: "FeatureCollection"; features: GeofenceFeature[] }>("/api/geofences"),
  geofenceCheck: (lat: number, lon: number) => request<{ status: string; hits: any[]; hard_constraints: string[] }>(`/api/geofence/check?${q({ lat, lon })}`),
  advisories: () => request<{ used: string[]; errors: string[]; type: "FeatureCollection"; features: any[] }>("/api/advisories"),

  chat: (body: ChatBody) => request<ChatResponse>("/api/chat", post(body)),
  board: (day: string, part: string) => request<HarbourBoard>(`/api/board?${q({ day, part })}`),
  bulletin: (day: string, part: string, language: string) =>
    request<{ language: string; lines: string[]; text: string; board: HarbourBoard }>(`/api/bulletin?${q({ day, part, language })}`),
  subscribe: (body: { phone: string; channel: "sms" | "whatsapp"; harbour_id: string; language: string }) =>
    request<{ id: string; phone: string; harbour: string; provider: string }>("/api/subscriptions", post(body)),
  speak: async (text: string, language: string): Promise<Blob> => {
    const res = await fetch("/api/speak", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, language }) });
    if (!res.ok) throw new Error(`speech ${res.status}`);
    return res.blob();
  },
  transcribe: (wav: Blob, language?: string) =>
    request<{ text: string; language: string | null; engine: string; latency_ms: number }>(
      `/api/transcribe${language ? `?${q({ language })}` : ""}`,
      { method: "POST", body: wav, headers: { "Content-Type": "audio/wav" } },
    ),
  risk: (lat: number, lon: number, start: string, end: string) => request<RiskResponse>(`/api/risk?${q({ lat, lon, start, end })}`),
  conditions: (lat: number, lon: number, hours = 48) => request<ConditionsSeries>(`/api/conditions?${q({ lat, lon, hours })}`),
  pfz: (lat: number, lon: number, limit = 8) => request<PfzResponse>(`/api/pfz?${q({ lat, lon, limit })}`),
  route: (body: RouteBody) => request<RouteComparison>("/api/route", post(body)),
  riskLayer: (bbox: { lat_min: number; lat_max: number; lon_min: number; lon_max: number }, time: string, step = 0.25) =>
    request<{ time: string; step: number; marine_source: string; cells: RiskCell[] }>(`/api/layers/risk?${q({ ...bbox, time, step })}`),
  fields: (fields: string[], time: string) => request<FieldLayers>(`/api/layers/fields?${q({ fields: fields.join(","), time })}`),

  replayEvents: () => request<{ active: ReplayInfo | null; events: ReplayEvent[] }>("/api/replay/events"),
  setReplayEvent: (event_id: string, as_of?: string) => request<ReplayInfo>("/api/replay/event", post(as_of ? { event_id, as_of } : { event_id })),
  replayTimeline: () => request<ReplayTimeline>("/api/replay/timeline"),
  backtest: () => request<Backtest>("/api/backtest"),

  alerts: () => request<{ alerts: Alert[]; watches: { id: string; lat: number; lon: number; label: string }[] }>("/api/alerts"),
  watch: (lat: number, lon: number, label: string, language: string) =>
    request<{ watch: { id: string }; alerts: Alert[] }>("/api/alerts/watch", post({ lat, lon, label, language })),
  unwatch: (id: string) => request<{ deleted: string }>(`/api/alerts/watch/${id}`, { method: "DELETE" }),
  advance: (hours: number) => request<{ clock: string; offset_hours: number; alerts: Alert[] }>("/api/sim/advance", post({ hours })),
  resetClock: () => request<{ clock: string; offset_hours: number }>("/api/sim/reset", { method: "POST" }),
  track: (vessel_id: string, lat: number, lon: number, language: string) =>
    request<{ geofence: { status: string; hits: any[] }; alert: Alert | null }>("/api/track", post({ vessel_id, lat, lon, language })),
};

function liveSubscribe(onAlert: (a: Alert) => void, onStatus: (connected: boolean) => void): () => void {
  const source = new EventSource("/api/alerts/stream");
  source.addEventListener("hello", () => onStatus(true));
  source.addEventListener("alert", (ev) => onAlert(JSON.parse((ev as MessageEvent).data)));
  source.onerror = () => onStatus(false);
  return () => source.close();
}

export const subscribeAlerts = STATIC_DEMO ? demoSubscribe : liveSubscribe;
