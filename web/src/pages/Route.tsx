import { useEffect, useMemo, useState } from "react";
import L from "leaflet";
import { api } from "../api";
import { levelLabel } from "../i18n";
import { useApi, useApp } from "../store";
import { floorHour, istAt } from "../time";
import type { RoutePlan } from "../types";
import ChartMap, { GeoLayer, GeofenceLayer, HeatLayer, Legend, PlaceMarker, WAVE_RAMP, useMap } from "../ui/ChartMap";
import Page, { ErrorNote, Loading, Panel } from "../ui/Page";
import { LevelChip } from "../ui/Verdict";
import { fmtIST, useFields } from "./common";

const LEVELS = ["LOW", "MODERATE", "HIGH", "SEVERE", "INSUFFICIENT_DATA"] as const;

function BoatMarker({ lat, lon }: { lat: number; lon: number }) {
  const map = useMap();
  useEffect(() => {
    if (!map) return;
    const icon = L.divIcon({ className: "boat", html: `<svg viewBox="-12 -12 24 24" width="26" height="26" aria-hidden="true"><path d="M-9 3 L9 3 L6 8 L-6 8 Z"/><path d="M0 3 L0 -10 L7 1 Z"/></svg>`, iconSize: [26, 26] });
    const m = L.marker([lat, lon], { icon, interactive: false }).addTo(map);
    return () => {
      m.remove();
    };
  }, [map, lat, lon]);
  return null;
}

function positionAt(plan: RoutePlan, t: number): { lat: number; lon: number; level: string | null } | null {
  for (const s of plan.segments) {
    const a = new Date(s.eta_start).getTime(),
      b = new Date(s.eta_end).getTime();
    if (t >= a && t <= b) {
      const k = b > a ? (t - a) / (b - a) : 0;
      return { lat: s.start[0] + (s.end[0] - s.start[0]) * k, lon: s.start[1] + (s.end[1] - s.start[1]) * k, level: s.level };
    }
  }
  const last = plan.segments[plan.segments.length - 1];
  return last ? { lat: last.end[0], lon: last.end[1], level: last.level } : null;
}

function LevelHours({ plan, max }: { plan: RoutePlan; max: number }) {
  return (
    <div className="lh-bar" role="img" aria-label="Hours at each risk level">
      {LEVELS.filter((l) => (plan.level_hours[l] ?? 0) > 0).map((l) => (
        <span key={l} className={`lh lv-${l.toLowerCase()}`} style={{ width: `${((plan.level_hours[l] ?? 0) / max) * 100}%` }} title={`${levelLabel("en", l)}: ${(plan.level_hours[l] ?? 0).toFixed(1)} h`}>
          {(plan.level_hours[l] ?? 0) >= 0.6 ? `${(plan.level_hours[l] ?? 0).toFixed(1)}h` : ""}
        </span>
      ))}
    </div>
  );
}

export default function Route() {
  const { clock, place, ports, target, setTarget, geofences, staticDemo, events, replay } = useApp();
  const nearestPort = useMemo(() => {
    if (!ports.length) return null;
    return ports.reduce((best, p) => ((p.lat - place.lat) ** 2 + (p.lon - place.lon) ** 2 < (best.lat - place.lat) ** 2 + (best.lon - place.lon) ** 2 ? p : best));
  }, [ports, place.lat, place.lon]);
  const [portId, setPortId] = useState<string | null>(null);
  const origin = ports.find((p) => p.id === portId) ?? nearestPort;
  // Zones are searched from the chosen departure port, and only a reachable (viable) zone is offered: a zone on
  // another coast is not a trip. Otherwise the user taps the chart to choose where to go.
  const pfz = useApi(() => (clock && origin && !target ? api.pfz(origin.lat, origin.lon, 3) : null), [origin?.id, clock, !!target]);
  const topZone = pfz.data?.candidates.find((c) => c.viable);
  // region = [lat_min, lat_max, lon_min, lon_max] of the replay's archived data
  const ev = events.find((e) => e.id === replay?.event);
  const outsideReplay =
    !!ev && !!origin && !(origin.lat >= ev.region[0] && origin.lat <= ev.region[1] && origin.lon >= ev.region[2] && origin.lon <= ev.region[3]);
  const dest = target ?? (topZone ? { lat: topZone.zone.centroid[0], lon: topZone.zone.centroid[1], label: topZone.zone.name } : null);
  const [dep, setDep] = useState<"soon" | "t06">("t06");
  const [speed, setSpeed] = useState(8);
  const departure = clock ? (dep === "soon" ? floorHour(clock, 1) : istAt(clock, 1, 6)) : null;
  const aheadH = clock && departure ? Math.max(0, Math.round((new Date(departure).getTime() - new Date(floorHour(clock)).getTime()) / 3600_000)) : 0;
  const waves = useFields(["waves"], aheadH);

  const route = useApi(
    () => (origin && dest && departure ? api.route({ start_port: origin.id, end_lat: dest.lat, end_lon: dest.lon, departure, speed_knots: speed }) : null),
    [origin?.id, dest?.lat, dest?.lon, departure, speed],
  );
  const r = route.data;
  const rec = r?.recommended ?? null;
  const [scrub, setScrub] = useState(0);
  useEffect(() => setScrub(0), [r]);

  const fc = useMemo(() => {
    if (!r) return null;
    const feats: any[] = [];
    feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: r.direct.waypoints.map((w) => [w.lon, w.lat]) }, properties: { kind: "direct" } });
    for (const s of rec?.segments ?? [])
      feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: [[s.start[1], s.start[0]], [s.end[1], s.end[0]]] }, properties: { kind: "seg", level: s.level, eta: s.eta_start } });
    return { type: "FeatureCollection", features: feats };
  }, [r, rec]);
  const bounds = useMemo<[[number, number], [number, number]] | null>(() => {
    if (!r) return null;
    const pts = [...r.direct.waypoints, ...(rec?.waypoints ?? [])];
    return [
      [Math.min(...pts.map((p) => p.lat)) - 0.15, Math.min(...pts.map((p) => p.lon)) - 0.15],
      [Math.max(...pts.map((p) => p.lat)) + 0.15, Math.max(...pts.map((p) => p.lon)) + 0.15],
    ];
  }, [r, rec]);

  const t0 = rec ? new Date(rec.segments[0]?.eta_start ?? r!.departure).getTime() : 0;
  const t1 = rec ? new Date(rec.segments[rec.segments.length - 1]?.eta_end ?? r!.departure).getTime() : 0;
  const tNow = t0 + ((t1 - t0) * scrub) / 100;
  const boat = rec ? positionAt(rec, tNow) : null;
  const maxH = r ? Math.max(rec?.duration_h ?? 0, r.direct.duration_h) : 1;

  return (
    <Page
      title="Safe Route"
      blurb="The planner checks every leg against the forecast for the hour the boat will be there, and goes around rough water when it can."
      actions={
        <div className="route-controls">
          {!staticDemo && (
            <label className="select-inline">
              <span>From</span>
              <select id="route-from" value={origin?.id ?? ""} onChange={(e) => setPortId(e.target.value)}>
                {ports.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="segmented small" role="radiogroup" aria-label="Departure">
            <button role="radio" aria-checked={dep === "soon"} className={dep === "soon" ? "on" : ""} onClick={() => setDep("soon")}>
              In 1 hour
            </button>
            <button role="radio" aria-checked={dep === "t06"} className={dep === "t06" ? "on" : ""} onClick={() => setDep("t06")}>
              Tomorrow 06:00
            </button>
          </div>
          <div className="segmented small" role="radiogroup" aria-label="Boat speed">
            {[6, 8, 10].map((s) => (
              <button key={s} role="radio" aria-checked={speed === s} className={speed === s ? "on" : ""} onClick={() => setSpeed(s)}>
                {s} kn
              </button>
            ))}
          </div>
        </div>
      }
      datum={
        <span>
          {origin ? `From ${origin.name}` : ""} {dest ? `→ ${dest.label}` : ""} · cost = travel time × (1 + risk weight) · land, restricted waters, boundary crossings and HIGH or SEVERE seas are forbidden
          {target && (
            <button className="linkish" onClick={() => setTarget(null)}>
              use nearest zone instead
            </button>
          )}
        </span>
      }
      wide
    >
      {route.error && <ErrorNote error={route.error} />}
      <div className="route-grid">
        <div className="route-map">
          <ChartMap
            bounds={bounds}
            center={origin ? [origin.lat, origin.lon] : [place.lat, place.lon]}
            zoom={7}
            onClick={(lat, lon) => setTarget({ lat, lon, label: `${lat.toFixed(2)}°N ${lon.toFixed(2)}°E` })}
            label="Recommended and direct routes. Tap the chart to choose a destination."
          >
            {waves.data?.waves && <HeatLayer grid={waves.data.waves} values={waves.data.waves.hs} ramp={WAVE_RAMP} opacity={0.7} />}
            <GeofenceLayer features={geofences} interactive={false} />
            {fc && (
              <GeoLayer
                data={fc}
                style={(f) => (f.properties.kind === "direct" ? { className: "route-direct", weight: 3, dashArray: "6 8" } : { className: `route-seg lv-stroke-${(f.properties.level || "").toLowerCase()}`, weight: 6 })}
                popup={(f) => (f.properties.kind === "direct" ? "Direct line" : `${levelLabel("en", f.properties.level)} from ${fmtIST(f.properties.eta)}`)}
                animate
              />
            )}
            {boat && <BoatMarker lat={boat.lat} lon={boat.lon} />}
            {origin && <PlaceMarker lat={origin.lat} lon={origin.lon} label={`From ${origin.name}`} />}
            {dest && <PlaceMarker lat={dest.lat} lon={dest.lon} label={`To ${dest.label}`} />}
          </ChartMap>
          <p className="small muted">Tap the chart to choose a destination.</p>
          <Legend title={`Wave height at departure${waves.data ? `, ${fmtIST(waves.data.valid)}` : ""}`} unit="m" ramp={WAVE_RAMP} ticks={[0, 1.25, 2.5, 4, 6]} />
          {rec && (
            <div className="scrubber">
              <label htmlFor="route-scrub" className="small">
                Voyage clock
              </label>
              <input id="route-scrub" type="range" min={0} max={100} value={scrub} onChange={(e) => setScrub(Number(e.target.value))} />
              <span className="mono small">
                {fmtIST(new Date(tNow).toISOString())} {boat?.level && <LevelChip level={boat.level} />}
              </span>
            </div>
          )}
        </div>
        <div className="route-side">
          {!dest && !pfz.loading ? (
            <Panel title="Choose a destination">
              <p>
                No fishing zone within reach of {origin?.name ?? "this port"} in the loaded data. Tap the chart to choose where you
                want to go.
              </p>
              {outsideReplay && (
                <p className="small warn">
                  The loaded replay, {ev!.title}, has no sea data around {origin!.name}. Load an event for this coast on the{" "}
                  <a href="#replay">Time Machine</a> page.
                </p>
              )}
            </Panel>
          ) : !r ? (
            <Loading what="a route" />
          ) : (
            <>
              <Panel title={rec ? "Recommended" : "No safe route"} className={rec ? "" : "panel-alert"}>
                {rec ? (
                  <>
                    <div className="big-stats">
                      <div>
                        <span className="mono">{rec.distance_km.toFixed(1)}</span> km
                      </div>
                      <div>
                        <span className="mono">{rec.duration_h.toFixed(1)}</span> h
                      </div>
                      <div>
                        worst <LevelChip level={rec.max_level} />
                      </div>
                    </div>
                    <LevelHours plan={rec} max={maxH} />
                  </>
                ) : (
                  <p>No route avoids HIGH/SEVERE seas, land and restricted waters in the planning window. Do not depart; wait for conditions to improve.</p>
                )}
              </Panel>
              <Panel title="Direct line">
                <div className="big-stats muted-stats">
                  <div>
                    <span className="mono">{r.direct.distance_km.toFixed(1)}</span> km
                  </div>
                  <div>
                    <span className="mono">{r.direct.duration_h.toFixed(1)}</span> h
                  </div>
                  <div>{r.direct.feasible ? <LevelChip level={r.direct.max_level} /> : <span className="chip-level lv-severe">Not allowed</span>}</div>
                </div>
                <LevelHours plan={r.direct} max={maxH} />
                {r.direct.violations.length > 0 && <p className="small warn">{r.direct.violations.join("; ")}</p>}
              </Panel>
              <Panel title="Why this route">
                <ul className="reasons">
                  {r.reasons.map((x) => (
                    <li key={x}>{x}</li>
                  ))}
                </ul>
              </Panel>
            </>
          )}
        </div>
      </div>
    </Page>
  );
}
