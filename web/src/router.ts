import { useEffect, useState } from "react";
import { flushSync } from "react-dom";

export type RouteId = "home" | "fishermen" | "ask" | "safety" | "zones" | "route" | "conditions" | "alerts" | "boundaries" | "replay" | "agents" | "data" | "board";

export interface RouteInfo {
  id: RouteId;
  label: string;
  title: string;
  blurb: string;
  group: "sea" | "insight";
}

// Hash routes are bare words (#zones) so a shared link can open a page directly.
export const ROUTES: RouteInfo[] = [
  { id: "home", label: "Bridge", title: "Bridge", blurb: "Today's verdict, the sea around you and every tool in one view.", group: "sea" },
  { id: "fishermen", label: "For Fishermen", title: "For Fishermen", blurb: "One clear answer before you go to sea, in your language, read aloud.", group: "sea" },
  { id: "ask", label: "Ask ORCA", title: "Ask ORCA", blurb: "Ask in your own language. The agents plan, fetch the data and explain.", group: "sea" },
  { id: "safety", label: "Sea Safety", title: "Sea Safety", blurb: "Can I go? Hour-by-hour risk for your window, with the reason for each change.", group: "sea" },
  { id: "zones", label: "Fishing Zones", title: "Fishing Zones", blurb: "Candidate zones where a temperature front meets chlorophyll-rich water.", group: "sea" },
  { id: "route", label: "Safe Route", title: "Safe Route", blurb: "A route that avoids the rough water on the way, timed to your departure.", group: "sea" },
  { id: "conditions", label: "Conditions", title: "Sea Conditions", blurb: "Waves, wind, rain, visibility and sea temperature for the next 48 hours.", group: "sea" },
  { id: "alerts", label: "Alerts", title: "Alerts & Warnings", blurb: "Official IMD warnings, the cyclone track and alerts for places you watch.", group: "sea" },
  { id: "boundaries", label: "Boundaries", title: "Boundaries", blurb: "Maritime boundary, restricted and protected waters, with a check for any point.", group: "sea" },
  { id: "replay", label: "Time Machine", title: "Time Machine", blurb: "Replay real past events and see how well ORCA's verdicts held up.", group: "insight" },
  { id: "agents", label: "How it decided", title: "How ORCA Decided", blurb: "The plan, each agent's step and the evidence behind the last answer.", group: "insight" },
  { id: "board", label: "Harbour Board", title: "Harbour Board", blurb: "Every harbour at once: where to hold boats, with a bulletin to print or share.", group: "insight" },
  { id: "data", label: "Data & Rules", title: "Data & Rules", blurb: "Every source, when it was published, and the safety rules ORCA applies.", group: "insight" },
];

const valid = new Set(ROUTES.map((r) => r.id));

function current(): RouteId {
  const h = window.location.hash.replace(/^#\/?/, "");
  return (valid.has(h as RouteId) ? h : "home") as RouteId;
}

export function go(id: RouteId): void {
  const target = id === "home" ? "" : id;
  if (window.location.hash.replace(/^#\/?/, "") === target) return;
  window.location.hash = target;
}

export function useRoute(): RouteId {
  const [route, setRoute] = useState<RouteId>(current);
  useEffect(() => {
    const onChange = () => {
      const next = current();
      type Transition = { ready: Promise<void>; finished: Promise<void> };
      const doc = document as Document & { startViewTransition?: (cb: () => void) => Transition };
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (doc.startViewTransition && !reduce) {
        const t = doc.startViewTransition(() => flushSync(() => setRoute(next)));
        // a quick second navigation skips the running transition; that is expected, not an error
        t.ready.catch(() => undefined);
        t.finished.catch(() => undefined);
      } else setRoute(next);
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
