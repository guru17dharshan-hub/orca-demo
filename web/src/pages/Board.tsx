// Harbour Board: every harbour the loaded data covers, for one time window, grouped by what to do — and the
// reporting agent's bulletin in the reader's language, to print or share on WhatsApp. For coastal
// authorities, fisheries departments and disaster managers.
import { useMemo, useState } from "react";
import { api } from "../api";
import { useApi } from "../store";
import type { Level } from "../types";
import ChartMap, { GeoLayer } from "../ui/ChartMap";
import Icon from "../ui/Icon";
import Page, { ErrorNote, Loading, Panel } from "../ui/Page";
import { LevelChip } from "../ui/Verdict";
import { fmtIST } from "./common";

const PARTS = [
  { id: "now", label: "Next 6 hours" },
  { id: "morning", label: "Morning 04–12" },
  { id: "afternoon", label: "Afternoon 12–17" },
  { id: "evening", label: "Evening 17–21" },
  { id: "night", label: "Night 21–04" },
] as const;
const LANGS = [
  { code: "en", label: "English" },
  { code: "hi", label: "हिन्दी" },
  { code: "ta", label: "தமிழ்" },
  { code: "te", label: "తెలుగు" },
  { code: "ml", label: "മലയാളം" },
];
/** The deciding factor in a few plain words. */
function why(f: { variable: string; value: number | string | null } | null): string {
  if (!f) return "—";
  const v = typeof f.value === "number" ? f.value : null;
  if (f.variable === "wave_height" && v !== null) return `waves ${v.toFixed(1)} m`;
  if (f.variable === "wind_speed" && v !== null) return `wind ${Math.round(v)} km/h`;
  if (f.variable === "visibility" && v !== null) return `visibility ${(v / 1000).toFixed(1)} km`;
  if (f.variable === "weather_code") return "thunderstorm";
  if (f.variable === "advisory") return `IMD: ${f.value}`;
  return f.variable.replace(/_/g, " ");
}
const GROUP_LABEL: Record<string, string> = { hold: "Hold boats", care: "Go with care", go: "Normal", unknown: "Cannot confirm" };

export default function Board() {
  const [day, setDay] = useState<"today" | "tomorrow">("tomorrow");
  const [part, setPart] = useState<string>("morning");
  const [lang, setLang] = useState("en");
  const [copied, setCopied] = useState(false);
  const effDay = part === "now" ? "today" : day;
  const b = useApi(() => api.bulletin(effDay, part, lang), [effDay, part, lang]);
  const board = b.data?.board;

  const fc = useMemo(
    () => ({
      type: "FeatureCollection",
      features: (board?.rows ?? []).map((r) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [r.lon, r.lat] },
        properties: { label: `${r.harbour}: ${r.level}`, level: r.level },
      })),
    }),
    [board],
  );
  const bounds = useMemo<[[number, number], [number, number]] | null>(() => {
    const rows = board?.rows ?? [];
    if (!rows.length) return null;
    const lats = rows.map((r) => r.lat), lons = rows.map((r) => r.lon);
    return [[Math.min(...lats) - 0.8, Math.min(...lons) - 0.8], [Math.max(...lats) + 0.8, Math.max(...lons) + 0.8]];
  }, [board]);

  const copy = async () => {
    if (!b.data) return;
    try {
      await navigator.clipboard.writeText(b.data.text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked: the text is on screen to select */
    }
  };

  return (
    <Page
      title="Harbour Board"
      blurb="Every harbour at once for one time window: where to hold boats, where to go with care, and a bulletin to print or send to a WhatsApp group."
      actions={
        <div className="route-controls">
          <div className="segmented small" role="radiogroup" aria-label="Day">
            {(["today", "tomorrow"] as const).map((d) => (
              <button key={d} role="radio" aria-checked={effDay === d} className={effDay === d ? "on" : ""} disabled={part === "now"} onClick={() => setDay(d)}>
                {d === "today" ? "Today" : "Tomorrow"}
              </button>
            ))}
          </div>
          <label className="select-inline">
            <span>When</span>
            <select value={part} onChange={(e) => setPart(e.target.value)}>
              {PARTS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      }
      datum={board && <span>Rules {board.rules} · window {fmtIST(board.window.start)} → {fmtIST(board.window.end)} · {board.rows.length} harbours in the loaded data</span>}
      wide
    >
      {b.error && <ErrorNote error={b.error} />}
      {!board ? (
        !b.error && <Loading what="every harbour" />
      ) : (
        <>
          <div className="board-counts">
            {Object.entries(board.counts).map(([g, n]) => (
              <div key={g} className={`board-count g-${g}`}>
                <b>{n}</b>
                <span>{GROUP_LABEL[g]}</span>
              </div>
            ))}
          </div>
          <div className="board-grid">
            <Panel title="Harbours">
              <table className="board-table">
                <thead>
                  <tr>
                    <th>Harbour</th>
                    <th>Verdict</th>
                    <th>Why</th>
                    <th>Lowest-risk window</th>
                    <th>Warnings</th>
                  </tr>
                </thead>
                <tbody>
                  {board.rows.map((r) => (
                    <tr key={r.harbour_id}>
                      <td>
                        <b>{r.harbour}</b>
                        <div className="small muted">{r.state}</div>
                      </td>
                      <td>
                        <LevelChip level={r.level as Level} />
                      </td>
                      <td className="small">{why(r.factor)}</td>
                      <td className="mono small">{r.go_window ? `${fmtIST(r.go_window.start, false)}–${fmtIST(r.go_window.end, false)}` : "none"}</td>
                      <td className="small">{r.warnings.length ? [...new Set(r.warnings.map((w) => w.event))].join(", ") : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Panel>
            <div className="board-side">
              <Panel title="On the chart">
                <ChartMap bounds={bounds} className="board-map" label="Harbours by verdict">
                  <GeoLayer data={fc} style={(f) => ({ className: `target lv-stroke-${(f.properties.level || "").toLowerCase()}`, weight: 3, fillOpacity: 0.4 })} popup={(f) => `<b>${f.properties.label}</b>`} />
                </ChartMap>
              </Panel>
              <Panel
                title="Bulletin"
                className="bulletin-panel"
                aside={
                  <select value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Bulletin language">
                    {LANGS.map((l) => (
                      <option key={l.code} value={l.code}>
                        {l.label}
                      </option>
                    ))}
                  </select>
                }
              >
                <pre className="bulletin-text" lang={lang}>
                  {b.data?.text}
                </pre>
                <div className="row-actions">
                  <button onClick={copy}>
                    <Icon name="data" size={18} /> {copied ? "Copied" : "Copy"}
                  </button>
                  <a className="btn-link ghost-link" href={`https://wa.me/?text=${encodeURIComponent(b.data?.text ?? "")}`} target="_blank" rel="noreferrer">
                    Share on WhatsApp
                  </a>
                  <button className="ghost" onClick={() => window.print()}>
                    Print
                  </button>
                </div>
              </Panel>
            </div>
          </div>
        </>
      )}
    </Page>
  );
}
