import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { levelLabel } from "../i18n";
import { useApi, useApp } from "../store";
import type { Backtest, Level } from "../types";
import Page, { Loading, Panel } from "../ui/Page";
import { fmtDay, fmtIST } from "./common";

const ORDER: Level[] = ["LOW", "MODERATE", "HIGH", "SEVERE"];
const DANGER = new Set(["HIGH", "SEVERE"]);

function outcome(f: string, o: string): "hit" | "miss" | "false" | "calm" | "na" {
  if (!ORDER.includes(f as Level) || !ORDER.includes(o as Level)) return "na";
  if (DANGER.has(o)) return DANGER.has(f) ? "hit" : "miss";
  return DANGER.has(f) ? "false" : "calm";
}

function Scrubber() {
  const { replay, clock, switchEvent, switching, staticDemo } = useApp();
  const timeline = useApi(() => (replay ? api.replayTimeline() : null), [replay?.event, clock]);
  const start = replay ? new Date(replay.start).getTime() : 0;
  const end = replay ? new Date(replay.end).getTime() : 1;
  const steps = Math.floor((end - start) / 3600_000);
  const cur = clock ? Math.round((new Date(clock).getTime() - start) / 3600_000) : 0;
  const [pos, setPos] = useState(cur);
  useEffect(() => setPos(cur), [cur]);
  if (!replay) return <p className="muted">Historical replay is off. Start ORCA with ORCA_DATA_MODE=historical.</p>;
  const at = new Date(start + pos * 3600_000).toISOString();
  const x = (iso: string) => `${Math.min(100, Math.max(0, ((new Date(iso).getTime() - start) / (end - start)) * 100))}%`;
  const runs = timeline.data?.runs ?? [];
  return (
    <div className="tm">
      <div className="tm-track" aria-hidden>
        {runs.map((r) => (
          <span key={r.cycle} className="tm-run" style={{ left: x(r.published) }} title={`GFS ${fmtIST(r.cycle)} run on NOAA's server ${fmtIST(r.published)}`} />
        ))}
        {(timeline.data?.warnings ?? []).map((w) => (
          <span key={w.id} className="tm-warn" style={{ left: x(w.sent ?? w.onset ?? replay.start) }} title={`${w.headline} — sent ${fmtIST(w.sent)}`} />
        ))}
        <span className="tm-now" style={{ left: x(clock ?? replay.as_of) }} />
      </div>
      <label htmlFor="tm-range" className="sr-only">
        Replay moment
      </label>
      <input id="tm-range" type="range" min={0} max={steps} value={pos} onChange={(e) => setPos(Number(e.target.value))} disabled={staticDemo} />
      <div className="tm-row">
        <span className="small muted">{fmtIST(replay.start)}</span>
        <span className="tm-at mono">{fmtIST(at)}</span>
        <span className="small muted">{fmtIST(replay.end)}</span>
      </div>
      <div className="tm-legend small">
        <span>
          <i className="tm-run" /> GFS run published
        </span>
        <span>
          <i className="tm-warn" /> IMD warning sent
        </span>
        <span>
          <i className="tm-now" /> replay moment
        </span>
      </div>
      <div className="row-actions">
        <button disabled={staticDemo || switching || pos === cur} onClick={() => switchEvent(replay.event, at)}>
          Move the replay to {fmtIST(at)}
        </button>
        {staticDemo && <span className="small muted">The preview keeps each event at its recorded moment.</span>}
      </div>
    </div>
  );
}

function Matrix({ m, title }: { m: Backtest["matrix"]; title: string }) {
  const max = Math.max(...ORDER.flatMap((f) => ORDER.map((o) => m[f]?.[o] ?? 0)));
  return (
    <div className="table-wrap">
      <table className="matrix">
        <caption>{title}</caption>
        <thead>
          <tr>
            <th scope="col">Forecast ↓ · Observed →</th>
            {ORDER.map((o) => (
              <th key={o} scope="col">
                {levelLabel("en", o)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {ORDER.map((f) => (
            <tr key={f}>
              <th scope="row">{levelLabel("en", f)}</th>
              {ORDER.map((o) => {
                const v = m[f]?.[o] ?? 0;
                const kind = outcome(f, o);
                return (
                  <td key={o} className={`mx mx-${kind} ${f === o ? "mx-diag" : ""}`} style={{ ["--a" as string]: max ? String(0.12 + (v / max) * 0.8) : "0" }}>
                    {v}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Backtesting() {
  const [attempt, setAttempt] = useState(0);
  const bt = useApi(() => api.backtest(), [attempt]);
  const data = bt.data;
  const grid = useMemo(() => {
    if (!data) return null;
    const dates = [...new Set(data.rows.map((r) => r.date))].sort();
    const by = new Map(data.rows.map((r) => [`${r.site}|${r.date}`, r]));
    return { dates, by };
  }, [data]);
  // Only a 404 means there are no results; anything else (server restarting, offline) is worth a retry.
  if (bt.error?.startsWith("no backtest results")) return <p className="muted">No backtest results yet. Run python scripts/historical/backtest.py.</p>;
  if (bt.error)
    return (
      <p className="muted">
        Could not load the backtest results ({bt.error}).{" "}
        <button className="ghost" onClick={() => setAttempt((n) => n + 1)}>
          Retry
        </button>
      </p>
    );
  if (!data || !grid) return <Loading what="backtest results" />;
  const s = data.scores,
    p = data.persistence_scores;
  const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);
  return (
    <>
      <div className="bt-stats">
        <div className="bt-stat">
          <span className="bt-k">Dangerous mornings caught</span>
          <span className="bt-v mono">{pct(s.pod)}</span>
          <span className="bt-bars">
            <i style={{ width: pct(s.pod) }} className="orca" />
            <i style={{ width: pct(p.pod) }} className="base" />
          </span>
          <span className="small muted">
            {s.hits} of {s.dangerous_observed} · persistence {pct(p.pod)}
          </span>
        </div>
        <div className="bt-stat">
          <span className="bt-k">Missed dangerous mornings</span>
          <span className="bt-v mono">{s.misses}</span>
          <span className="small muted">persistence missed {p.misses}</span>
        </div>
        <div className="bt-stat">
          <span className="bt-k">False alarms</span>
          <span className="bt-v mono">{pct(s.far)}</span>
          <span className="bt-bars">
            <i style={{ width: pct(s.far) }} className="orca" />
            <i style={{ width: pct(p.far) }} className="base" />
          </span>
          <span className="small muted">
            of danger calls · persistence {pct(p.far)}
          </span>
        </div>
        <div className="bt-stat">
          <span className="bt-k">Exact level</span>
          <span className="bt-v mono">{pct(s.exact_level_accuracy)}</span>
          <span className="small muted">
            within one level: {pct(s.within_one_level)} · persistence {pct(p.exact_level_accuracy)}
          </span>
        </div>
      </div>
      <p className="bt-legend small">
        <span className="swatch-inline orca" /> ORCA forecast <span className="swatch-inline base" /> persistence (tomorrow = this evening)
      </p>

      <div className="bt-grid-wrap">
        <div className="bt-grid" style={{ gridTemplateColumns: `8.5rem repeat(${grid.dates.length}, minmax(9px, 1fr))` }} role="img" aria-label="Outcome for each harbour and morning">
          <span />
          {grid.dates.map((d, i) => (
            <span key={d} className="bt-date mono">
              {i % 7 === 0 ? fmtDay(d).replace(/ \d{4}$/, "") : ""}
            </span>
          ))}
          {data.sites.map((site, si) => (
            <div key={site.id} className="bt-row" style={{ display: "contents" }}>
              <span className="bt-site">{site.name.split(" (")[0]}</span>
              {grid.dates.map((d, di) => {
                const r = grid.by.get(`${site.id}|${d}`);
                const k = r ? outcome(r.forecast, r.observed) : "na";
                return (
                  <span
                    key={d}
                    className={`bt-cell oc-${k}`}
                    style={{ animationDelay: `${di * 12 + si * 8}ms` }}
                    title={r ? `${site.name}, morning after ${fmtDay(d)}: forecast ${r.forecast}, observed ${r.observed} (waves up to ${r.obs_wave_max} m, wind ${r.obs_wind_max} km/h)` : ""}
                  />
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <p className="bt-key small">
        <span className="oc oc-hit" /> danger caught <span className="oc oc-miss" /> danger missed <span className="oc oc-false" /> false alarm <span className="oc oc-calm" /> calm, correctly
      </p>

      <div className="bt-matrices">
        <Matrix m={data.matrix} title="ORCA's evening forecast" />
        <Matrix m={data.persistence_matrix} title="Persistence baseline" />
      </div>
      <dl className="method-list small">
        {Object.entries(data.method).map(([k, v]) => (
          <div key={k}>
            <dt>{k[0].toUpperCase() + k.slice(1)}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}

export default function Replay() {
  const { events, replay, switchEvent, switching } = useApp();
  return (
    <Page
      title="Time Machine"
      blurb="Put ORCA back in a real past moment. It sees only the forecasts, satellite passes and IMD warnings that had been published by then, so you can judge its advice against what happened next."
      datum={<span>NOAA GFS and GFS-Wave runs from NOAA Open Data · NOAA OISST · NOAA-20 VIIRS · IMD CAP via the WMO Alert Hub · truth: ECMWF ERA5</span>}
      wide
    >
      <div className="tm-events">
        {events.map((e, i) => (
          <button key={e.id} className={`event-card big ek-${e.kind} ${replay?.event === e.id ? "on" : ""}`} disabled={switching || !e.available} onClick={() => switchEvent(e.id)} style={{ animationDelay: `${i * 70}ms` }}>
            <span className="event-kind">{e.kind === "cyclone" ? "Cyclone" : "Fishing season"}</span>
            <b>{e.title.split(" — ")[0]}</b>
            <span className="small">{e.title.split(" — ")[1]}</span>
            <span className="event-summary small">{e.summary}</span>
            <span className="small muted mono">
              {fmtDay(e.start)} – {fmtDay(e.end)}
            </span>
            <span className="event-tags">
              {e.tags.map((t) => (
                <span key={t} className="tag">
                  {t}
                </span>
              ))}
            </span>
            {replay?.event === e.id && <span className="event-on">Loaded</span>}
          </button>
        ))}
      </div>
      <Panel title="Replay moment">
        <Scrubber />
      </Panel>
      <Panel title="Would ORCA's evening verdict have held?" className="backtest">
        <p className="small">
          Every evening from 1 May to 15 June 2021, at twelve west-coast harbours: ORCA's verdict for 06:00–12:00 next morning, made from the forecast NOAA had published by 21:30 IST, checked
          against ECMWF's ERA5 record of what the sea actually did. The season includes Cyclone Tauktae and the monsoon's arrival.
        </p>
        <Backtesting />
      </Panel>
    </Page>
  );
}
