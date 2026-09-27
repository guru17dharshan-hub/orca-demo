import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, subscribeAlerts } from "./api";
import type { Message } from "./ui/Chat";
import { STATIC_DEMO, demoScript } from "./demo";
import type { Alert, ChatResponse, GeofenceFeature, Health, Place, Port, ReplayEvent, ReplayInfo } from "./types";

type Theme = "system" | "light" | "dark";

interface AppState {
  health: Health | null;
  replay: ReplayInfo | null;
  events: ReplayEvent[];
  ports: Port[];
  geofences: GeofenceFeature[];
  place: Place;
  setPlace: (p: Place) => void;
  lang: string;
  setLang: (l: string) => void;
  messages: Message[];
  busy: boolean;
  active: ChatResponse | null;
  setActiveId: (id: string) => void;
  ask: (text: string) => Promise<ChatResponse | null>;
  script: string[];
  alerts: Alert[];
  connected: boolean;
  clock: string | null;
  switching: boolean;
  switchEvent: (id: string, asOf?: string) => Promise<void>;
  advance: (hours: number) => Promise<Alert[]>;
  resetClock: () => Promise<void>;
  refreshHealth: () => Promise<void>;
  theme: Theme;
  setTheme: (t: Theme) => void;
  staticDemo: boolean;
  target: Target | null;
  setTarget: (t: Target | null) => void;
}

export interface Target {
  lat: number;
  lon: number;
  label: string;
}

const Ctx = createContext<AppState | null>(null);

const FALLBACK_PLACE: Place = { lat: 15.4, lon: 73.7, label: "off Mormugao, Goa", source: "device" };

function loadTheme(): Theme {
  try {
    const t = localStorage.getItem("orca.theme");
    if (t === "light" || t === "dark" || t === "system") return t;
  } catch {
    /* storage unavailable */
  }
  return "system";
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [events, setEvents] = useState<ReplayEvent[]>([]);
  const [ports, setPorts] = useState<Port[]>([]);
  const [geofences, setGeofences] = useState<GeofenceFeature[]>([]);
  const [place, setPlaceState] = useState<Place>(FALLBACK_PLACE);
  const [lang, setLang] = useState("auto");
  const [messages, setMessages] = useState<Message[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [connected, setConnected] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [script, setScript] = useState<string[]>([]);
  const [theme, setThemeState] = useState<Theme>(loadTheme);
  const [target, setTarget] = useState<Target | null>(null);
  const placeTouched = useRef(false);

  const refreshHealth = useCallback(async () => {
    try {
      const h = await api.health();
      setHealth(h);
      if (h.replay && !placeTouched.current) setPlaceState({ ...h.replay.place, source: "event" });
    } catch {
      /* shown as offline in the bar */
    }
  }, []);

  useEffect(() => {
    refreshHealth();
    api.replayEvents().then((r) => setEvents(r.events)).catch(() => undefined);
    api.ports().then(setPorts).catch(() => undefined);
    api.geofences().then((fc) => setGeofences(fc.features)).catch(() => undefined);
    api.alerts().then((r) => setAlerts(r.alerts)).catch(() => undefined);
    if (STATIC_DEMO) demoScript().then(setScript).catch(() => undefined);
    const stop = subscribeAlerts((a) => setAlerts((prev) => (prev.some((p) => p.id === a.id) ? prev : [a, ...prev])), setConnected);
    const timer = setInterval(refreshHealth, 30000);
    return () => {
      stop();
      clearInterval(timer);
    };
  }, [refreshHealth]);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  const setTheme = (t: Theme) => {
    setThemeState(t);
    try {
      localStorage.setItem("orca.theme", t);
    } catch {
      /* storage unavailable */
    }
  };

  const setPlace = (p: Place) => {
    placeTouched.current = true;
    setPlaceState(p);
  };

  const active = useMemo(() => messages.find((m) => m.id === activeId)?.res ?? null, [messages, activeId]);

  const ask = async (text: string): Promise<ChatResponse | null> => {
    const userMsg: Message = { id: crypto.randomUUID(), role: "user", text };
    setMessages((m) => [...m, userMsg]);
    setBusy(true);
    try {
      const res = await api.chat({
        message: text,
        session_id: sessionId,
        lat: place.lat,
        lon: place.lon,
        location_label: place.label,
        language: lang === "auto" ? null : lang,
      });
      setSessionId(res.session_id);
      // A place named in the question (a port or coordinates) becomes the app's location, so every page's map
      // and data follow the conversation instead of staying on the previous place.
      if (res.place && (res.place.source === "port" || res.place.source === "coordinates"))
        setPlace({ lat: res.place.lat, lon: res.place.lon, label: res.place.label, source: "answer" });
      setMessages((m) => [...m, { id: res.request_id, role: "assistant", text: res.answer, res }]);
      setActiveId(res.request_id);
      return res;
    } catch (e) {
      setMessages((m) => [...m, { id: crypto.randomUUID(), role: "assistant", text: `Request failed: ${(e as Error).message}`, error: true }]);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const switchEvent = async (id: string, asOf?: string) => {
    setSwitching(true);
    try {
      const info = await api.setReplayEvent(id, asOf);
      placeTouched.current = false;
      setPlaceState({ ...info.place, source: "event" });
      setMessages([]);
      setSessionId(null);
      setActiveId(null);
      setAlerts([]);
      setTarget(null);
      if (STATIC_DEMO) demoScript().then(setScript).catch(() => undefined);
      await refreshHealth();
    } finally {
      setSwitching(false);
    }
  };

  const advance = async (hours: number) => {
    const r = await api.advance(hours);
    await refreshHealth();
    return r.alerts;
  };

  const resetClock = async () => {
    await api.resetClock();
    await refreshHealth();
  };

  const value: AppState = {
    health,
    replay: health?.replay ?? null,
    events,
    ports,
    geofences,
    place,
    setPlace,
    lang,
    setLang,
    messages,
    busy,
    active,
    setActiveId,
    ask,
    script,
    alerts,
    connected,
    clock: health?.clock ?? null,
    switching,
    switchEvent,
    advance,
    resetClock,
    refreshHealth,
    theme,
    setTheme,
    staticDemo: STATIC_DEMO,
    target,
    setTarget,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useApp(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useApp outside AppProvider");
  return v;
}

/** Fetch on mount and whenever deps change; `fn` returning null means "not ready yet". */
export function useApi<T>(fn: () => Promise<T> | null, deps: unknown[]): { data: T | null; error: string | null; loading: boolean } {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: true });
  useEffect(() => {
    let alive = true;
    const p = fn();
    if (!p) return;
    setState((s) => ({ ...s, loading: true, error: null }));
    p.then((data) => alive && setState({ data, error: null, loading: false })).catch(
      (e) => alive && setState((s) => ({ data: s.data, error: (e as Error).message, loading: false })),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
