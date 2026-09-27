import { useMemo } from "react";
import { LANGS } from "../i18n";
import { go } from "../router";
import { useApp } from "../store";
import Chat from "../ui/Chat";
import ChartMap, { GeoLayer, PlaceMarker } from "../ui/ChartMap";
import HourStrip from "../ui/HourStrip";
import Page, { Panel } from "../ui/Page";
import { LevelChip, VerdictDial } from "../ui/Verdict";
import { normMessage } from "../demo";
import { factorText, fmtIST } from "./common";

const STARTERS = [
  "Is it safe to go fishing tomorrow at 6 AM?",
  "Where is the nearest Potential Fishing Zone today?",
  "Are there any lightning or cyclone alerts in my area?",
  "What are the tide, weather, and sea conditions near my fishing location?",
  "कल सुबह समुद्र में जाना सुरक्षित है?",
];

const featureStyle = (f: any) => {
  const k = f.properties.kind;
  if (k === "pfz") return { className: "zone", weight: 2, fillOpacity: 0.22 };
  if (k === "route_recommended") return { className: `route-seg lv-stroke-${(f.properties.level || "").toLowerCase()}`, weight: 6 };
  if (k === "route_direct") return { className: "route-direct", weight: 3, dashArray: "6 8" };
  if (k === "advisory") return { className: `adv adv-${(f.properties.severity || "").toLowerCase()}`, weight: 1.5, fillOpacity: 0.08 };
  if (k === "safety_target") return { className: `target lv-stroke-${(f.properties.level || "").toLowerCase()}`, weight: 3, fillOpacity: 0.15 };
  if (k === "hotspot") return { className: "hotspot", weight: 1, fillOpacity: 0.8 };
  if (k === "harbour") return { className: `target lv-stroke-${(f.properties.level || "").toLowerCase()}`, weight: 3, fillOpacity: 0.35 };
  if (k === "avoid") return { className: "avoid", weight: 2, fillOpacity: 0.8 };
  return { className: "feat", weight: 2 };
};
const featurePopup = (f: any) => `<b>${f.properties.label ?? f.properties.kind}</b>`;

// The map frames the answer's place plus what the answer drew near it (zones, routes, hotspots) — not warning
// or geofence polygons, which can span the whole coast and would zoom the chart out to all of India.
const FOCUS_KINDS = new Set(["pfz", "route_recommended", "route_direct", "safety_target", "hotspot", "avoid", "harbour"]);
const MIN_HALF_SPAN = 0.6;

function answerBounds(focus: { lat: number; lon: number } | null, features: any[]): [[number, number], [number, number]] | null {
  const pts: [number, number][] = focus ? [[focus.lat, focus.lon]] : [];
  const collect = (c: any): void => {
    if (typeof c[0] === "number") pts.push([c[1], c[0]]);
    else c.forEach(collect);
  };
  features.filter((f) => FOCUS_KINDS.has(f.properties.kind)).forEach((f) => collect(f.geometry.coordinates));
  if (!pts.length) return null;
  const lats = pts.map((p) => p[0]), lons = pts.map((p) => p[1]);
  const midLat = (Math.min(...lats) + Math.max(...lats)) / 2, midLon = (Math.min(...lons) + Math.max(...lons)) / 2;
  const hLat = Math.max(MIN_HALF_SPAN, (Math.max(...lats) - Math.min(...lats)) / 2);
  const hLon = Math.max(MIN_HALF_SPAN, (Math.max(...lons) - Math.min(...lons)) / 2);
  return [[midLat - hLat, midLon - hLon], [midLat + hLat, midLon + hLon]];
}

export default function Ask() {
  const { messages, busy, lang, setLang, active, setActiveId, ask, place, script, staticDemo } = useApp();
  const asked = useMemo(() => new Set(messages.filter((m) => m.role === "user").map((m) => normMessage(m.text))), [messages]);
  const suggestions = staticDemo ? script.filter((s) => !asked.has(normMessage(s))).slice(0, 5) : active?.suggestions?.length ? active.suggestions : STARTERS;
  const features = active?.map.features.filter((f) => f.properties.kind !== "location") ?? [];
  const fc = useMemo(() => ({ type: "FeatureCollection", features }), [features]);
  const safety = active?.cards.safety;
  const compare = active?.cards.compare;
  const focus = active?.place ?? null;
  const bounds = useMemo(() => answerBounds(focus, features), [focus, fc]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Page
      title="Ask ORCA"
      blurb="Ask the way you would ask a friend on the jetty. ORCA works out what you need, fetches the data, and answers in your language."
      actions={
        <label className="select-inline">
          <span>Reply in</span>
          <select id="reply-language" value={lang} onChange={(e) => setLang(e.target.value)}>
            {LANGS.map((l) => (
              <option key={l.code} value={l.code}>
                {l.label}
              </option>
            ))}
          </select>
        </label>
      }
      datum={<span>Location: {place.label} · {place.lat.toFixed(2)}°N {place.lon.toFixed(2)}°E</span>}
      wide
    >
      <div className="ask-grid">
        <div className="ask-chat">
          <Chat messages={messages} busy={busy} lang={lang} activeId={active?.request_id ?? null} suggestions={suggestions} onSend={(t) => ask(t)} onSelect={setActiveId} />
        </div>
        <div className="ask-desk">
          {!active ? (
            <Panel title="What ORCA can do">
              <ul className="capability-list">
                <li>Sea safety for a time you name, hour by hour</li>
                <li>Nearest fishing zone from satellite fronts</li>
                <li>A safer route that goes around rough water</li>
                <li>Official IMD warnings and cyclone watches</li>
                <li>Boundary and protected-area checks</li>
                <li>Replies in English, हिन्दी, தமிழ், తెలుగు, മലയാളം</li>
              </ul>
            </Panel>
          ) : (
            <>
              {safety && (
                <Panel title="Verdict" aside={<span className="small muted">{fmtIST(safety.valid_from)} → {fmtIST(safety.valid_until)}</span>}>
                  <div className="desk-verdict">
                    <VerdictDial level={safety.risk_level} lang={active.language} size={170} />
                    <ul className="factor-list">
                      {safety.key_factors.slice(0, 4).map((f) => (
                        <li key={f.variable + String(f.value)}>
                          <LevelChip level={f.level} lang={active.language} /> {factorText(f)}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <HourStrip hours={safety.timeline} lang={active.language} />
                </Panel>
              )}
              {compare && (
                <Panel
                  title="Harbours compared"
                  aside={<span className="small muted">{compare.planner === "llm" ? "planned by the LLM, checked" : "planned by rules"} · verdicts from the risk rules</span>}
                >
                  <ol className="compare-list">
                    {compare.rows
                      .filter((r) => r.tool === "harbour_safety")
                      .map((r) => (
                        <li key={r.harbour_id + r.window.start} className={r.harbour_id === compare.best ? "best" : ""}>
                          <span className="compare-name">{r.harbour}</span>
                          <LevelChip level={r.level} lang={active.language} />
                          <span className="small muted">
                            {r.go_window ? `go ${fmtIST(r.go_window.start, false)}–${fmtIST(r.go_window.end, false)}` : "no low-risk window"}
                          </span>
                        </li>
                      ))}
                  </ol>
                  {compare.notes.length > 0 && <p className="small muted">Plan checks: {compare.notes.join("; ")}</p>}
                </Panel>
              )}
              {(features.length > 0 || focus) && (
                <Panel title="On the chart">
                  <ChartMap className="desk-map" label="Map for the answer" bounds={bounds}>
                    <GeoLayer data={fc} style={featureStyle} popup={featurePopup} animate />
                    <PlaceMarker lat={(focus ?? place).lat} lon={(focus ?? place).lon} label={(focus ?? place).label} />
                  </ChartMap>
                </Panel>
              )}
              <Panel title="Behind this answer">
                <p className="small">
                  {active.trace.steps.length} agent steps · {active.evidence.length} pieces of evidence · explanation by {active.answer_source === "llm" ? "LLM, checked by the verdict lock" : "template"}
                </p>
                <div className="row-actions">
                  <button className="ghost" onClick={() => go("agents")}>
                    See the plan and evidence
                  </button>
                  {safety && <button className="ghost" onClick={() => go("safety")}>Open Sea Safety</button>}
                </div>
              </Panel>
            </>
          )}
        </div>
      </div>
    </Page>
  );
}
