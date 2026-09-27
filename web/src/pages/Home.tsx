import { useState } from "react";
import { api } from "../api";
import { ROUTES, go, type RouteId } from "../router";
import { useApi, useApp } from "../store";
import { istAt } from "../time";
import ChartMap, { CycloneTrack, GeoLayer, HeatLayer, Legend, PlaceMarker, WAVE_RAMP, WindParticles } from "../ui/ChartMap";
import { LevelChip, VerdictDial } from "../ui/Verdict";
import Icon from "../ui/Icon";

/** Behind the intro: three swell layers drifting at different speeds over faint depth contours, and a sonar
 *  pulse — the sea ORCA reads, drawn as a chart. Purely decorative. */
function OceanBackdrop() {
  const swell = (y: number, amp: number) => {
    let d = `M0 ${y}`;
    for (let x = 0; x < 2400; x += 200) d += ` q50 ${-amp} 100 0 t100 0`;
    return d + " V400 H0 Z";
  };
  return (
    <div className="ocean" aria-hidden>
      <svg className="contours" viewBox="0 0 1200 400" preserveAspectRatio="xMidYMid slice">
        {[0, 1, 2, 3, 4, 5].map((k) => (
          <path key={k} d={`M-50 ${90 + k * 48} C 250 ${40 + k * 52}, 520 ${150 + k * 40}, 820 ${80 + k * 50} S 1150 ${120 + k * 44}, 1300 ${70 + k * 50}`} />
        ))}
      </svg>
      <svg className="swells" viewBox="0 0 1200 400" preserveAspectRatio="none">
        <path className="sw s1" d={swell(300, 14)} />
        <path className="sw s2" d={swell(325, 10)} />
        <path className="sw s3" d={swell(350, 7)} />
      </svg>
      <span className="ocean-ping" />
      <span className="ocean-ping p2" />
    </div>
  );
}
import { REGION_BOUNDS, beaufort, factorText, fmtDay, fmtIST, seaState, useFields, valueAt } from "./common";

const advStyle = (f: any) => ({ className: `adv adv-${(f.properties.severity || "").toLowerCase()} ${f.properties.data_type === "derived" ? "adv-derived" : ""}`, weight: 1.5, fillOpacity: 0.08 });
// SENSE → PREDICT → REASON → DECIDE → EXPLAIN, in the words a fisherman would use.
const FLOW = [
  { k: "Sense", t: "Wind and wave forecasts, satellite sea temperature and chlorophyll, IMD warnings, maritime boundaries." },
  { k: "Predict", t: "Every hour of your trip, not only the sea right now — so you know when it turns." },
  { k: "Reason", t: "Specialist agents work in parallel on weather, ocean, fishing zones, boundaries and routes." },
  { k: "Decide", t: "Fixed, published rules set the risk: WMO sea state, the Beaufort wind scale, IMD warning severity." },
  { k: "Explain", t: "A short answer in your language, by voice or text, with the evidence behind every number." },
];

const advPopup = (f: any) => `<b>${f.properties.headline || f.properties.event}</b><br>${f.properties.severity} — ${f.properties.source}<br><small>${f.properties.area ?? ""}</small>`;

export default function Home() {
  const { clock, place, replay, events, switchEvent, switching, ask, busy } = useApp();
  const [q, setQ] = useState("");
  const start = clock ? istAt(clock, 1, 6) : null;
  const end = clock ? istAt(clock, 1, 12) : null;
  const risk = useApi(() => (start && end ? api.risk(place.lat, place.lon, start, end) : null), [place.lat, place.lon, start, end]);
  const fields = useFields(["wind", "waves"]);
  const timeline = useApi(() => (replay ? api.replayTimeline() : null), [replay?.event, clock]);
  const adv = useApi(() => (clock ? api.advisories() : null), [clock, replay?.event]);
  const pfz = useApi(() => (clock ? api.pfz(place.lat, place.lon, 5) : null), [place.lat, place.lon, clock]);

  const ev = events.find((e) => e.id === replay?.event);
  const bounds = ev ? REGION_BOUNDS(ev.region) : null;
  const d = risk.data?.decision;
  const kf = d?.key_factors?.[0];
  const waveNow = valueAt(fields.data?.waves, "hs", place.lat, place.lon);
  const u = valueAt(fields.data?.wind, "u", place.lat, place.lon);
  const v = valueAt(fields.data?.wind, "v", place.lat, place.lon);
  const windNow = u !== null && v !== null ? Math.hypot(u, v) * 3.6 : null;
  const warnings = (timeline.data?.warnings ?? []).filter((w) => !w.expires || !clock || new Date(w.expires) > new Date(clock));
  const here = new Set((d?.advisories ?? []).filter((a) => a.data_type === "official_advisory").map((a) => a.headline + a.area)).size;
  const cyclone = timeline.data?.track_forecast?.[0] ?? null;
  const nearest = pfz.data?.candidates.find((c) => c.viable) ?? pfz.data?.candidates[0];

  const tiles: { id: RouteId; stat: React.ReactNode }[] = [
    { id: "fishermen", stat: <span>Go or don't go, read aloud</span> },
    { id: "ask", stat: <span>Hindi, Tamil, Telugu, Malayalam, English</span> },
    { id: "safety", stat: d ? <LevelChip level={d.risk_level} lang="en" /> : <span className="muted">…</span> },
    { id: "zones", stat: nearest ? <span className="mono">{nearest.distance_km} km {nearest.compass}</span> : <span className="muted">none today</span> },
    { id: "route", stat: <span>Timed to your departure</span> },
    { id: "conditions", stat: waveNow !== null ? <span className="mono">{waveNow.toFixed(1)} m · {seaState(waveNow)?.name}</span> : <span className="muted">…</span> },
    { id: "alerts", stat: <span className="mono">{warnings.length} IMD in force{cyclone ? ` · ${cyclone.category}` : ""}</span> },
    { id: "boundaries", stat: <span>Check any point</span> },
    { id: "replay", stat: <span>{ev ? ev.title.split(" — ")[0] : "Live"}</span> },
    { id: "agents", stat: <span>Plan, steps, evidence</span> },
    { id: "data", stat: <span>Sources and rules</span> },
  ];

  const submit = async (text: string) => {
    if (!text.trim() || busy) return;
    setQ("");
    go("ask");
    await ask(text.trim());
  };

  return (
    <main className="page page-home" id="main">
      <section className="intro" aria-labelledby="intro-title">
        <OceanBackdrop />
        <div className="intro-copy">
          <p className="eyebrow">Smart India Hackathon 2026 · Problem statement 26176 · ISRO / Department of Space</p>
          <h1 id="intro-title" className="reveal-words">
            {["Know", "the", "sea"].map((w, i) => (
              <span key={w} className="rw" style={{ animationDelay: `${150 + i * 90}ms` }}>
                <span>{w}</span>
              </span>
            ))}
            <em>
              {["before", "you", "leave", "the", "harbour"].map((w, i) => (
                <span key={w + i} className="rw" style={{ animationDelay: `${420 + i * 90}ms` }}>
                  <span>{w}</span>
                </span>
              ))}
            </em>
          </h1>
          <p className="intro-lede">
            <b>ORCA</b> (Marine EcOsystem Reasoning with Collaborative Agents) is a decision-support assistant for India's coastal
            fishermen. Ask whether it is safe to go out, where the fish are and how to get there. ORCA's agents read the forecasts,
            satellite data and official warnings, check your trip hour by hour, and answer with a decision, the reason and the evidence.
          </p>
          <p className="intro-langs" lang="mul">
            Ask by voice or text in English, हिन्दी, தமிழ், తెలుగు, മലയാളം and more.
          </p>
          <div className="intro-actions">
            <button className="btn-hero" onClick={() => go("fishermen")}>
              <Icon name="fishermen" size={19} /> For Fishermen
            </button>
            <button className="ghost" onClick={() => go("ask")}>
              <Icon name="ask" size={18} /> Ask ORCA
            </button>
            <button className="ghost" onClick={() => document.getElementById("bridge")?.scrollIntoView({ behavior: "smooth" })}>
              <Icon name="down" size={18} /> Tomorrow's verdict
            </button>
            <button className="ghost" onClick={() => go("agents")}>
              <Icon name="agents" size={18} /> How it decides
            </button>
          </div>
          <p className="intro-sources mono">NOAA GFS &amp; GFS-Wave · NOAA OISST · NOAA-20 VIIRS · IMD warnings (CAP)</p>
        </div>
        <div className="intro-side">
          <ol className="intro-flow">
            {FLOW.map((s, i) => (
              <li key={s.k} style={{ animationDelay: `${80 + i * 70}ms` }}>
                <span className="flow-n mono">0{i + 1}</span>
                <span className="flow-k">{s.k}</span>
                <span className="flow-t">{s.t}</span>
              </li>
            ))}
          </ol>
          <p className="intro-rule">
            <b>The AI explains; it never decides.</b> If the language model's answer changes the risk level or quotes a number that
            is not in the data, ORCA throws it away and gives its own checked answer instead.
          </p>
        </div>
      </section>

      <section className="bridge" id="bridge">
        <ChartMap bounds={bounds} className="bridge-map" label="Sea around you: wind, waves and warnings">
          {fields.data?.waves && <HeatLayer grid={fields.data.waves} values={fields.data.waves.hs} ramp={WAVE_RAMP} opacity={0.75} />}
          {fields.data?.wind && <WindParticles grid={fields.data.wind} u={fields.data.wind.u} v={fields.data.wind.v} />}
          {adv.data && <GeoLayer data={adv.data} style={advStyle} popup={advPopup} />}
          {timeline.data && <CycloneTrack observed={timeline.data.track_observed} forecast={timeline.data.track_forecast} />}
          <PlaceMarker lat={place.lat} lon={place.lon} label={place.label} />
        </ChartMap>
        <div className="bridge-legend">
          <Legend title="Wave height" unit="m" ramp={WAVE_RAMP} ticks={[0, 1.25, 2.5, 4, 6]} />
          <span className="small muted">Streaks: 10 m wind{fields.data?.wind ? ` · GFS run ${fmtIST(fields.data.wind.run)}` : ""}</span>
        </div>

        <article className="bridge-card">
          {replay && (
            <p className="eyebrow">
              Replay · {ev?.title ?? replay.title} · as of {fmtIST(clock)}
            </p>
          )}
          <h2 className="bridge-q">
            Can I go out tomorrow morning?
            <span>{place.label} · 06:00–12:00</span>
          </h2>
          <div className="bridge-verdict">
            <VerdictDial level={d?.risk_level ?? null} lang="en" size={200} />
            <div className="bridge-why">
              {d ? (
                <>
                  <p className="why-k">Deciding factor</p>
                  <p className="why-v">{kf ? factorText(kf) : "Nothing above low risk."}</p>
                  {d.go_windows[0] ? (
                    <p className="small">
                      Lowest-risk window {fmtIST(d.go_windows[0].start, false)}–{fmtIST(d.go_windows[0].end, false)}
                    </p>
                  ) : (
                    d.risk_level !== "LOW" && <p className="small">No low-risk window in these hours.</p>
                  )}
                </>
              ) : (
                <p className="muted">{risk.error ?? "Reading the forecast…"}</p>
              )}
            </div>
          </div>
          <dl className="readouts">
            <div>
              <dt>Waves now</dt>
              <dd className="mono">{waveNow !== null ? `${waveNow.toFixed(1)} m` : "—"}</dd>
              <dd className="small">{seaState(waveNow)?.name ?? ""}</dd>
            </div>
            <div>
              <dt>Wind now</dt>
              <dd className="mono">{windNow !== null ? `${Math.round(windNow)} km/h` : "—"}</dd>
              <dd className="small">{windNow !== null ? `Beaufort ${beaufort(windNow)?.force}` : ""}</dd>
            </div>
            <div>
              <dt>IMD warnings</dt>
              <dd className="mono">{d ? here : "—"}</dd>
              <dd className="small">{cyclone ? cyclone.category : "cover you then"}</dd>
            </div>
          </dl>
          <div className="bridge-actions">
            <button onClick={() => go("safety")}>
              <Icon name="safety" size={18} /> Hour by hour
            </button>
            <button className="ghost" onClick={() => go("route")}>
              <Icon name="route" size={18} /> Plan a route
            </button>
          </div>
          <form
            className="bridge-ask"
            onSubmit={(e) => {
              e.preventDefault();
              submit(q);
            }}
          >
            <label htmlFor="bridge-question" className="sr-only">
              Ask ORCA
            </label>
            <input id="bridge-question" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask in any language — कल सुबह समुद्र में जाना सुरक्षित है?" />
            <button type="submit" className="send-btn" disabled={!q.trim() || busy}>
              <Icon name="send" size={18} /> <span>Ask</span>
            </button>
          </form>
        </article>
      </section>

      <section className="stations" aria-label="Tools">
        {tiles.map(({ id, stat }, i) => {
          const r = ROUTES.find((x) => x.id === id)!;
          return (
            <a key={id} href={`#${id}`} className={`station st-${id}`} style={{ animationDelay: `${120 + i * 45}ms` }}>
              <Icon name={id} size={26} className="station-icon" />
              <span className="station-name">{r.label}</span>
              <span className="station-blurb">{r.blurb}</span>
              <span className="station-stat">{stat}</span>
            </a>
          );
        })}
      </section>

      {events.length > 0 && (
        <section className="events" aria-label="Replay a real event">
          <div className="events-head">
            <h2>Replay a real event</h2>
            <p className="small muted">Archived NOAA forecasts and satellite data, and IMD warnings exactly as issued. ORCA only sees what was published by the replay moment.</p>
          </div>
          <div className="events-row">
            {events.map((e) => (
              <button key={e.id} className={`event-card ek-${e.kind} ${replay?.event === e.id ? "on" : ""}`} disabled={switching || !e.available} onClick={() => switchEvent(e.id)}>
                <span className="event-kind">{e.kind === "cyclone" ? "Cyclone" : "Fishing season"}</span>
                <b>{e.title.split(" — ")[0]}</b>
                <span className="small">{e.title.split(" — ")[1]}</span>
                <span className="small muted">
                  {fmtDay(e.start)} – {fmtDay(e.end)}
                </span>
                {replay?.event === e.id && <span className="event-on">Loaded</span>}
              </button>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
