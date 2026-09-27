// Chart-style map: offline GLOBE coastline, graticule ticks on the frame like a paper chart,
// and composable layers (wind particles, colour fields, zones, routes, warnings, cyclone track).
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { GeofenceFeature, Grid } from "../types";

const MapCtx = createContext<L.Map | null>(null);
export const useMap = () => useContext(MapCtx);

const LAND_BOUNDS: L.LatLngBoundsExpression = [
  [4, 64],
  [26, 96],
];

export function esc(s: unknown): string {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

interface ChartMapProps {
  center?: [number, number];
  zoom?: number;
  bounds?: [[number, number], [number, number]] | null;
  className?: string;
  onClick?: (lat: number, lon: number) => void;
  label?: string;
  children?: ReactNode;
}

export default function ChartMap({ center = [15.3, 73.3], zoom = 7, bounds, className = "", onClick, label = "Marine chart", children }: ChartMapProps) {
  const el = useRef<HTMLDivElement>(null);
  const [map, setMap] = useState<L.Map | null>(null);
  const clickRef = useRef(onClick);
  clickRef.current = onClick;

  useEffect(() => {
    if (!el.current) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: true, maxZoom: 12, minZoom: 4, zoomSnap: 0.5 }).setView(center, zoom);
    m.createPane("heat").style.zIndex = "140";
    m.createPane("land").style.zIndex = "150";
    m.createPane("particles").style.zIndex = "380";
    L.imageOverlay(`${import.meta.env.BASE_URL}land-india.png`, LAND_BOUNDS, {
      pane: "land",
      className: "land-mask",
      attribution: "Coastline: GLOBE land mask",
    }).addTo(m);
    m.on("click", (e: L.LeafletMouseEvent) => clickRef.current?.(e.latlng.lat, e.latlng.lng));
    const ro = new ResizeObserver(() => m.invalidateSize());
    ro.observe(el.current);
    setMap(m);
    return () => {
      ro.disconnect();
      m.remove();
      setMap(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Follow a changed centre (e.g. a new location) unless explicit bounds control the view.
  const centerKey = `${center[0].toFixed(3)},${center[1].toFixed(3)},${zoom}`;
  useEffect(() => {
    if (map && !bounds) map.setView(center, zoom);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, centerKey]);

  const boundsKey = bounds ? bounds.flat().map((v) => v.toFixed(3)).join(",") : "";
  useEffect(() => {
    if (map && bounds) map.fitBounds(bounds, { padding: [24, 24], maxZoom: 10 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, boundsKey]);

  return (
    <div className={`chart ${className}`}>
      <div ref={el} className="chart-map" role="region" aria-label={label} />
      {map && <Graticule map={map} />}
      <MapCtx.Provider value={map}>{map && children}</MapCtx.Provider>
    </div>
  );
}

/** Degree ticks along the top and left edges, labelled like a nautical chart's border. */
function Graticule({ map }: { map: L.Map }) {
  const [ticks, setTicks] = useState<{ lat: { y: number; t: string }[]; lon: { x: number; t: string }[] }>({ lat: [], lon: [] });
  useEffect(() => {
    const update = () => {
      const b = map.getBounds();
      const span = Math.max(b.getEast() - b.getWest(), b.getNorth() - b.getSouth());
      const step = span > 16 ? 4 : span > 8 ? 2 : span > 3 ? 1 : span > 1.5 ? 0.5 : 0.25;
      const fmt = (v: number, pos: string, neg: string) =>
        `${Math.abs(v) % 1 === 0 ? Math.abs(v).toFixed(0) : Math.abs(v).toFixed(step < 0.5 ? 2 : 1)}°${v >= 0 ? pos : neg}`;
      const lat: { y: number; t: string }[] = [];
      for (let v = Math.ceil(b.getSouth() / step) * step; v <= b.getNorth(); v += step)
        lat.push({ y: map.latLngToContainerPoint([v, b.getWest()]).y, t: fmt(v, "N", "S") });
      const lon: { x: number; t: string }[] = [];
      for (let v = Math.ceil(b.getWest() / step) * step; v <= b.getEast(); v += step)
        lon.push({ x: map.latLngToContainerPoint([b.getNorth(), v]).x, t: fmt(v, "E", "W") });
      setTicks({ lat, lon });
    };
    update();
    map.on("moveend zoomend resize", update);
    return () => {
      map.off("moveend zoomend resize", update);
    };
  }, [map]);
  return (
    <div className="graticule" aria-hidden>
      {ticks.lat.map((t) => (
        <span key={`a${t.t}`} className="tick tick-lat" style={{ top: t.y }}>
          {t.t}
        </span>
      ))}
      {ticks.lon.map((t) => (
        <span key={`o${t.t}`} className="tick tick-lon" style={{ left: t.x }}>
          {t.t}
        </span>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ helpers for gridded fields
export function sampleGrid(g: Grid, values: (number | null)[], lat: number, lon: number): number | null {
  const fi = (lat - g.lat0) / g.dlat;
  const fj = (lon - g.lon0) / g.dlon;
  if (fi < 0 || fj < 0 || fi > g.nlat - 1 || fj > g.nlon - 1) return null;
  const i0 = Math.floor(fi),
    j0 = Math.floor(fj);
  const i1 = Math.min(i0 + 1, g.nlat - 1),
    j1 = Math.min(j0 + 1, g.nlon - 1);
  const wi = fi - i0,
    wj = fj - j0;
  const pts: [number | null, number][] = [
    [values[i0 * g.nlon + j0], (1 - wi) * (1 - wj)],
    [values[i0 * g.nlon + j1], (1 - wi) * wj],
    [values[i1 * g.nlon + j0], wi * (1 - wj)],
    [values[i1 * g.nlon + j1], wi * wj],
  ];
  let s = 0,
    w = 0;
  for (const [v, ww] of pts)
    if (v !== null && v !== undefined) {
      s += v * ww;
      w += ww;
    }
  return w > 0.25 ? s / w : null;
}

export type Ramp = { stops: [number, string][]; log?: boolean };

function hexToRgb(h: string): [number, number, number] {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function rampColor(ramp: Ramp, value: number): [number, number, number] {
  const v = ramp.log ? Math.log10(Math.max(value, 1e-3)) : value;
  const stops = ramp.stops.map(([s, c]) => [ramp.log ? Math.log10(s) : s, c] as [number, string]);
  if (v <= stops[0][0]) return hexToRgb(stops[0][1]);
  for (let k = 1; k < stops.length; k++) {
    if (v <= stops[k][0]) {
      const [a, ca] = stops[k - 1],
        [b, cb] = stops[k];
      const t = (v - a) / (b - a);
      const x = hexToRgb(ca),
        y = hexToRgb(cb);
      return [x[0] + (y[0] - x[0]) * t, x[1] + (y[1] - x[1]) * t, x[2] + (y[2] - x[2]) * t];
    }
  }
  return hexToRgb(stops[stops.length - 1][1]);
}

/** Stretch a ramp's stops over the scene's own range (2nd–98th percentile), for fields like SST. */
export function fitRamp(ramp: Ramp, values: (number | null)[]): { ramp: Ramp; ticks: number[] } {
  const v = values.filter((x): x is number => x !== null).sort((a, b) => a - b);
  if (v.length < 10) return { ramp, ticks: ramp.stops.map((s) => s[0]) };
  const lo = v[Math.floor(v.length * 0.02)],
    hi = v[Math.floor(v.length * 0.98)];
  const a = ramp.stops[0][0],
    b = ramp.stops[ramp.stops.length - 1][0];
  const stops = ramp.stops.map(([s, c]) => [lo + ((s - a) / (b - a)) * (hi - lo), c] as [number, string]);
  const ticks = [0, 1, 2, 3].map((k) => Math.round((lo + ((hi - lo) * k) / 3) * 10) / 10);
  return { ramp: { ...ramp, stops }, ticks };
}

// Wave height breaks follow the WMO sea-state code used by ORCA's rules (1.25 / 2.5 / 4 m).
export const WAVE_RAMP: Ramp = { stops: [[0, "#cfe9f1"], [1.25, "#6cc3cf"], [2.5, "#f0c24a"], [4, "#e4602a"], [6, "#b3122e"], [9, "#5c0b3f"]] };
export const SST_RAMP: Ramp = { stops: [[25, "#23407a"], [26.5, "#2a74a0"], [27.5, "#3aa6a0"], [28.5, "#9fcf6a"], [29.5, "#f3cf52"], [30.5, "#ee8a3c"], [31.5, "#c8452c"]] };
export const CHL_RAMP: Ramp = { log: true, stops: [[0.05, "#f1f3c9"], [0.15, "#cfe6a0"], [0.4, "#8ecb78"], [1, "#44a25d"], [3, "#17714a"], [10, "#0a4430"]] };

const mercY = (lat: number) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI) / 360));
const latFromY = (y: number) => (Math.atan(Math.exp(y)) * 360) / Math.PI - 90;

/** A colour field drawn under the coastline (land hides coastal bleed), rows spaced in Web-Mercator. */
export function HeatLayer({ grid, values, ramp, opacity = 0.85 }: { grid: Grid; values: (number | null)[]; ramp: Ramp; opacity?: number }) {
  const map = useMap();
  useEffect(() => {
    if (!map) return;
    const s = grid.lat0 - grid.dlat / 2,
      n = grid.lat0 + grid.dlat * (grid.nlat - 0.5);
    const w = grid.lon0 - grid.dlon / 2,
      e = grid.lon0 + grid.dlon * (grid.nlon - 0.5);
    const width = Math.min(900, grid.nlon * 6);
    const height = Math.round((width * (mercY(n) - mercY(s))) / (((e - w) * Math.PI) / 180));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d")!;
    const img = ctx.createImageData(width, height);
    for (let r = 0; r < height; r++) {
      const lat = latFromY(mercY(n) - ((r + 0.5) / height) * (mercY(n) - mercY(s)));
      for (let c = 0; c < width; c++) {
        const lon = w + ((c + 0.5) / width) * (e - w);
        const v = sampleGrid(grid, values, lat, lon);
        if (v === null) continue;
        const [R, G, B] = rampColor(ramp, v);
        const k = (r * width + c) * 4;
        // feather the field's rectangular edge over ~2 grid cells
        const ei = Math.min(lat - s, n - lat) / (grid.dlat * 2.5);
        const ej = Math.min(lon - w, e - lon) / (grid.dlon * 2.5);
        img.data[k] = R;
        img.data[k + 1] = G;
        img.data[k + 2] = B;
        img.data[k + 3] = Math.round(255 * Math.min(1, ei, ej));
      }
    }
    ctx.putImageData(img, 0, 0);
    const layer = L.imageOverlay(canvas.toDataURL(), [[s, w], [n, e]], { pane: "heat", opacity, className: "heat-layer" }).addTo(map);
    return () => {
      layer.remove();
    };
  }, [map, grid, values, ramp, opacity]);
  return null;
}

/** Wind as drifting streaks (screen-space particle advection), colour by speed. */
export function WindParticles({ grid, u, v, density = 1 }: { grid: Grid; u: (number | null)[]; v: (number | null)[]; density?: number }) {
  const map = useMap();
  useEffect(() => {
    if (!map) return;
    const pane = map.getPane("particles")!;
    const canvas = L.DomUtil.create("canvas", "wind-canvas", pane);
    const ctx = canvas.getContext("2d")!;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const CELL = 8;
    let field: Float32Array = new Float32Array(0);
    let fw = 0,
      fh = 0,
      W = 0,
      H = 0,
      raf = 0,
      running = true;
    let particles: { x: number; y: number; age: number }[] = [];
    const ink = getComputedStyle(map.getContainer()).getPropertyValue("--wind-ink").trim() || "#0e2a47";

    const build = () => {
      const size = map.getSize();
      W = size.x;
      H = size.y;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = `${W}px`;
      canvas.style.height = `${H}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      fw = Math.ceil(W / CELL) + 1;
      fh = Math.ceil(H / CELL) + 1;
      field = new Float32Array(fw * fh * 2).fill(NaN);
      const pxPerDeg = map.latLngToContainerPoint([0, 1]).x - map.latLngToContainerPoint([0, 0]).x;
      const speedK = Math.max(0.12, Math.min(1.2, pxPerDeg / 90));
      for (let j = 0; j < fh; j++)
        for (let i = 0; i < fw; i++) {
          const ll = map.containerPointToLatLng([i * CELL, j * CELL]);
          const uu = sampleGrid(grid, u, ll.lat, ll.lng),
            vv = sampleGrid(grid, v, ll.lat, ll.lng);
          if (uu === null || vv === null) continue;
          field[(j * fw + i) * 2] = uu * speedK;
          field[(j * fw + i) * 2 + 1] = -vv * speedK;
        }
      const count = Math.round(Math.min(1400, (W * H) / 1400) * density);
      particles = Array.from({ length: count }, () => ({ x: Math.random() * W, y: Math.random() * H, age: Math.random() * 80 }));
      ctx.clearRect(0, 0, W, H);
      pane.appendChild(canvas);
      L.DomUtil.setPosition(canvas, map.containerPointToLayerPoint([0, 0]));
      if (reduce) drawStatic();
    };
    const vel = (x: number, y: number): [number, number] | null => {
      const i = x / CELL,
        j = y / CELL;
      const i0 = Math.floor(i),
        j0 = Math.floor(j);
      if (i0 < 0 || j0 < 0 || i0 >= fw - 1 || j0 >= fh - 1) return null;
      const k = (j0 * fw + i0) * 2;
      const a = field[k],
        b = field[k + 1];
      return Number.isNaN(a) ? null : [a, b];
    };
    const colour = (s: number) => {
      const a = Math.min(0.9, 0.35 + s * 0.12);
      return { a, w: s > 3.5 ? 1.6 : 1.1 };
    };
    const drawStatic = () => {
      ctx.clearRect(0, 0, W, H);
      ctx.strokeStyle = ink;
      ctx.globalAlpha = 0.6;
      for (let y = 20; y < H; y += 44)
        for (let x = 20; x < W; x += 44) {
          const f = vel(x, y);
          if (!f) continue;
          const len = Math.min(18, Math.hypot(f[0], f[1]) * 6);
          const ang = Math.atan2(f[1], f[0]);
          ctx.beginPath();
          ctx.moveTo(x, y);
          ctx.lineTo(x + Math.cos(ang) * len, y + Math.sin(ang) * len);
          ctx.stroke();
        }
      ctx.globalAlpha = 1;
    };
    const frame = () => {
      if (!running) return;
      ctx.globalCompositeOperation = "destination-in";
      ctx.fillStyle = "rgba(0,0,0,0.9)";
      ctx.fillRect(0, 0, W, H);
      ctx.globalCompositeOperation = "source-over";
      ctx.strokeStyle = ink;
      for (const p of particles) {
        const f = vel(p.x, p.y);
        if (!f || p.age > 90) {
          p.x = Math.random() * W;
          p.y = Math.random() * H;
          p.age = 0;
          continue;
        }
        const nx = p.x + f[0],
          ny = p.y + f[1];
        const c = colour(Math.hypot(f[0], f[1]));
        ctx.globalAlpha = c.a;
        ctx.lineWidth = c.w;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(nx, ny);
        ctx.stroke();
        p.x = nx;
        p.y = ny;
        p.age += 1;
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(frame);
    };
    const onMoveStart = () => {
      cancelAnimationFrame(raf);
      ctx.clearRect(0, 0, W, H);
    };
    const onMoveEnd = () => {
      build();
      if (!reduce) {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(frame);
      }
    };
    const onVisibility = () => {
      if (document.hidden) cancelAnimationFrame(raf);
      else if (!reduce) raf = requestAnimationFrame(frame);
    };
    build();
    if (!reduce) raf = requestAnimationFrame(frame);
    map.on("movestart zoomstart", onMoveStart);
    map.on("moveend zoomend resize", onMoveEnd);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      running = false;
      cancelAnimationFrame(raf);
      map.off("movestart zoomstart", onMoveStart);
      map.off("moveend zoomend resize", onMoveEnd);
      document.removeEventListener("visibilitychange", onVisibility);
      canvas.remove();
    };
  }, [map, grid, u, v, density]);
  return null;
}

// Colours come from CSS classes (theme tokens): SVG presentation attributes cannot read CSS variables.
const GEOFENCE_STYLE: Record<string, L.PathOptions> = {
  boundary_line: { className: "gf gf-boundary", weight: 2.5, dashArray: "10 6", fill: false },
  restricted: { className: "gf gf-restricted", weight: 1.5, fillOpacity: 0.14 },
  protected_area: { className: "gf gf-protected", weight: 1.5, fillOpacity: 0.12 },
  sensitive: { className: "gf gf-sensitive", weight: 1.5, fillOpacity: 0.1, dashArray: "4 4" },
};

export function GeofenceLayer({ features, interactive = true }: { features: GeofenceFeature[]; interactive?: boolean }) {
  const map = useMap();
  useEffect(() => {
    if (!map) return;
    const group = L.layerGroup().addTo(map);
    for (const f of features) {
      const p = f.properties;
      L.geoJSON(f as any, { style: () => GEOFENCE_STYLE[p.kind] ?? { color: "#555" }, interactive })
        .bindPopup(
          `<b>${esc(p.name)}</b><br>${esc(p.rule)}<br><small>Authority: ${esc(p.authority)}<br>Accuracy: <b>${esc(p.accuracy)}</b>${
            p.accuracy_note ? " — " + esc(p.accuracy_note) : ""
          }</small>`,
        )
        .addTo(group);
    }
    return () => {
      group.remove();
    };
  }, [map, features, interactive]);
  return null;
}

/** Your position: a sonar ping. */
export function PlaceMarker({ lat, lon, label }: { lat: number; lon: number; label: string }) {
  const map = useMap();
  useEffect(() => {
    if (!map) return;
    const icon = L.divIcon({ className: "ping", html: "<span></span><span></span><i></i>", iconSize: [18, 18] });
    const m = L.marker([lat, lon], { icon, keyboard: false }).bindTooltip(esc(label)).addTo(map);
    return () => {
      m.remove();
    };
  }, [map, lat, lon, label]);
  return null;
}

/** Plain GeoJSON with a style function; popups from a formatter. */
export function GeoLayer({ data, style, popup, animate }: { data: any; style: (f: any) => L.PathOptions; popup?: (f: any) => string; animate?: boolean }) {
  const map = useMap();
  useEffect(() => {
    if (!map || !data) return;
    const layer = L.geoJSON(data, {
      style,
      pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 6, ...style(f) }),
      onEachFeature: (f, lyr) => popup && lyr.bindPopup(popup(f)),
    }).addTo(map);
    if (animate && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      layer.eachLayer((l) => {
        const el = (l as L.Path).getElement?.() as SVGPathElement | undefined;
        if (el && typeof el.getTotalLength === "function") {
          const len = el.getTotalLength();
          el.style.strokeDasharray = `${len}`;
          el.style.strokeDashoffset = `${len}`;
          el.getBoundingClientRect();
          el.style.transition = "stroke-dashoffset 1.6s cubic-bezier(.4,.1,.2,1)";
          el.style.strokeDashoffset = "0";
        }
      });
    }
    return () => {
      layer.remove();
    };
  }, [map, data, style, popup, animate]);
  return null;
}

/** Cyclone: past positions (solid), forecast positions (dashed), spinning symbol at the latest centre. */
export function CycloneTrack({ observed, forecast }: { observed: { lat: number; lon: number; category: string; valid: string; max_wind_kmh: number }[]; forecast: { lat: number; lon: number; category: string; valid: string; max_wind_kmh: number }[] }) {
  const map = useMap();
  useEffect(() => {
    if (!map || (!observed.length && !forecast.length)) return;
    const g = L.layerGroup().addTo(map);
    const fmt = (p: { valid: string; category: string; max_wind_kmh: number }) =>
      `<b>${esc(p.category)}</b><br>${new Date(p.valid).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" })} IST<br>~${p.max_wind_kmh} km/h (model)`;
    if (observed.length > 1) L.polyline(observed.map((p) => [p.lat, p.lon]), { className: "trk trk-past", weight: 2.5 }).addTo(g);
    const joined = observed.length ? [observed[observed.length - 1], ...forecast] : forecast;
    if (joined.length > 1) L.polyline(joined.map((p) => [p.lat, p.lon]), { className: "trk trk-fc", weight: 2.5, dashArray: "6 7" }).addTo(g);
    for (const p of observed) L.circleMarker([p.lat, p.lon], { className: "trk-dot trk-dot-past", radius: 4, fillOpacity: 1, weight: 2 }).bindPopup(fmt(p)).addTo(g);
    for (const p of forecast.slice(1)) L.circleMarker([p.lat, p.lon], { className: "trk-dot trk-dot-fc", radius: 4, fillOpacity: 0.6, weight: 1.5 }).bindPopup(fmt(p)).addTo(g);
    const now = forecast[0] ?? observed[observed.length - 1];
    if (now) {
      const icon = L.divIcon({
        className: "cyclone",
        html: `<svg viewBox="-20 -20 40 40" width="38" height="38" aria-hidden="true"><g><path d="M0 -15 C 9 -15 14 -7 12 1 C 11 -6 5 -9 0 -8 Z" /><path d="M0 15 C -9 15 -14 7 -12 -1 C -11 6 -5 9 0 8 Z" /><circle r="5" /></g></svg>`,
        iconSize: [38, 38],
      });
      L.marker([now.lat, now.lon], { icon }).bindPopup(fmt(now)).addTo(g);
    }
    return () => {
      g.remove();
    };
  }, [map, observed, forecast]);
  return null;
}

export function Legend({ title, ramp, ticks, unit }: { title: string; ramp: Ramp; ticks: number[]; unit: string }) {
  const min = ramp.log ? Math.log10(ticks[0]) : ticks[0];
  const max = ramp.log ? Math.log10(ticks[ticks.length - 1]) : ticks[ticks.length - 1];
  const pos = (t: number) => (((ramp.log ? Math.log10(t) : t) - min) / (max - min)) * 100;
  const stops = ramp.stops
    .filter(([s]) => s >= ticks[0] && s <= ticks[ticks.length - 1])
    .map(([s, c]) => `${c} ${pos(s).toFixed(1)}%`)
    .join(", ");
  return (
    <div className="legend">
      <span className="legend-title">
        {title} <span className="muted">({unit})</span>
      </span>
      <div className="legend-bar" style={{ background: `linear-gradient(90deg, ${stops})` }} />
      <div className="legend-ticks">
        {ticks.map((t) => (
          <span key={t} style={{ left: `${pos(t)}%` }}>
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}
