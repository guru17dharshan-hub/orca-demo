import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import Icon from "./Icon";
import { ROUTES, go, type RouteId } from "../router";
import { useApp } from "../store";

const IST_FMT: Intl.DateTimeFormatOptions = { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" };

const PRIMARY: RouteId[] = ["fishermen", "ask", "safety", "zones", "route", "conditions", "alerts", "replay"];
const MORE: RouteId[] = ["board", "boundaries", "agents", "data"];

/** The mark: an orca surfacing inside a sonar ring, a wave running beneath. The ring draws itself in, the wave
 *  keeps moving, and on hover the orca lifts out of the water. */
export function OrcaMark({ size = 28 }: { size?: number }) {
  const clip = `om-${useId().replace(/:/g, "")}`;
  return (
    <svg viewBox="0 0 40 40" width={size} height={size} aria-hidden="true" className="orca-mark">
      <defs>
        <clipPath id={clip}>
          <circle cx="20" cy="20" r="16.6" />
        </clipPath>
      </defs>
      <circle cx="20" cy="20" r="18.6" className="om-ring" pathLength={1} />
      <g clipPath={`url(#${clip})`}>
        <circle cx="20" cy="20" r="16.6" className="om-disc" />
        <g className="om-body">
          <path d="M8.2 24.6C10.8 21.4 15 19.4 20 18.6c5.2-.8 9.8-.3 12.4 1.8 1.5 1.2 2 2.5 1.6 3.3-.6 1.2-2.6 1.9-5.5 2.4-4.7.9-10.2 1.1-14.7.4-2.2-.3-4.1-.9-5.6-1.9z" />
          <path d="M16.6 19.3c.2-3.4 1-6.7 2.4-9.8.7 3.3 1.9 6.2 3.6 8.9z" />
          <path d="M8.8 24.8L4.4 21.6l1.4 3.5-1.7 3.2z" />
          <ellipse cx="28.4" cy="20.9" rx="2.5" ry="0.95" transform="rotate(-10 28.4 20.9)" className="om-patch" />
          <path d="M33.6 23.4c-1.4 1.1-3.8 1.7-6.6 2.1" className="om-chin" />
        </g>
        <path className="om-wave" d="M-6 31.5c3.5-2.4 7-2.4 10.5 0s7 2.4 10.5 0 7-2.4 10.5 0 7 2.4 10.5 0 7-2.4 10.5 0 7 2.4 10.5 0" />
      </g>
    </svg>
  );
}

function ReplayChip({ compact = false }: { compact?: boolean }) {
  const { health, replay } = useApp();
  if (!health) return <span className="mode-chip off">Connecting…</span>;
  if (!replay) {
    const live = health.data_mode === "live" || health.last_marine_source === "live";
    return <span className={`mode-chip ${live ? "live" : "sim"}`}>{live ? "LIVE DATA" : "SIMULATED"}</span>;
  }
  const when = new Date(health.clock).toLocaleString("en-GB", IST_FMT);
  const short = replay.title.split(" — ")[0];
  return (
    <button className="mode-chip replay" onClick={() => go("replay")} title="Historical replay: open the Time Machine">
      <span className="rec" aria-hidden />
      <span className="chip-k">REPLAY</span>
      {!compact && <span className="chip-e">{short}</span>}
      <span className="chip-t mono">
        {when.replace(",", " ·")}
        <span className="chip-tz"> IST</span>
      </span>
      {health.clock_offset_hours !== 0 && <span className="chip-ff mono">+{health.clock_offset_hours} h</span>}
    </button>
  );
}

export default function Navbar({ route }: { route: RouteId }) {
  const { theme, setTheme, alerts, staticDemo } = useApp();
  const [open, setOpen] = useState(false);
  const [more, setMore] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const [ink, setInk] = useState<{ left: number; width: number } | null>(null);

  useLayoutEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-route="${route}"]`);
    setInk(el ? { left: el.offsetLeft, width: el.offsetWidth } : null);
  }, [route]);

  useEffect(() => {
    const onResize = () => {
      const el = listRef.current?.querySelector<HTMLElement>(`[data-route="${route}"]`);
      setInk(el ? { left: el.offsetLeft, width: el.offsetWidth } : null);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [route]);

  useEffect(() => {
    setOpen(false);
    setMore(false);
  }, [route]);

  const nextTheme = theme === "system" ? "light" : theme === "light" ? "dark" : "system";
  const themeLabel = theme === "system" ? "Auto" : theme === "light" ? "Day" : "Night";
  const info = (id: RouteId) => ROUTES.find((r) => r.id === id)!;
  const unread = alerts.length;

  return (
    <>
      <header className="navbar">
        <a href="#" className="brand" onClick={(e) => (e.preventDefault(), go("home"))} aria-label="ORCA home">
          <OrcaMark />
          <span className="brand-word">ORCA</span>
          <span className="brand-sub">Marine intelligence</span>
        </a>
        <nav className="nav-links" aria-label="Pages">
          <div className="nav-list" ref={listRef}>
            {PRIMARY.map((id) => (
              <a
                key={id}
                href={`#${id}`}
                data-route={id}
                className={`nav-link ${route === id ? "active" : ""} ${id === "replay" ? "nav-replay" : ""} ${id === "fishermen" ? "nav-fisher" : ""}`}
                aria-current={route === id ? "page" : undefined}
              >
                {id === "fishermen" && <Icon name="fishermen" size={17} />}
                {info(id).label}
                {id === "alerts" && unread > 0 && <span className="badge-count">{unread}</span>}
              </a>
            ))}
            <div className="nav-more">
              <button className={`nav-link ${MORE.includes(route) ? "active" : ""}`} data-route={MORE.includes(route) ? route : undefined} aria-expanded={more} onClick={() => setMore((m) => !m)}>
                More
              </button>
              {more && (
                <div className="more-menu" role="menu">
                  {MORE.map((id) => (
                    <a key={id} role="menuitem" href={`#${id}`} className={route === id ? "active" : ""}>
                      <b>
                        <Icon name={id} size={18} /> {info(id).label}
                      </b>
                      <span>{info(id).blurb}</span>
                    </a>
                  ))}
                </div>
              )}
            </div>
            {ink && <span className="nav-ink" style={{ transform: `translateX(${ink.left}px)`, width: ink.width }} aria-hidden />}
          </div>
        </nav>
        <div className="nav-right">
          <ReplayChip />
          <button className="theme-btn" onClick={() => setTheme(nextTheme)} title={`Display: ${themeLabel} (switch to ${nextTheme})`} aria-label={`Display theme: ${themeLabel}`}>
            <Icon name={theme === "system" ? "auto" : theme === "light" ? "sun" : "moon"} size={17} />
            <span className="theme-label">{themeLabel}</span>
          </button>
          <button className="menu-btn" aria-expanded={open} aria-controls="sheet" onClick={() => setOpen((o) => !o)} aria-label="All pages">
            <Icon name="menu" size={22} />
          </button>
        </div>
      </header>
      <div className="navbar-sub">
        <ReplayChip compact />
      </div>
      {staticDemo && (
        <div className="preview-note" role="note">
          <b>Preview:</b> every answer here was recorded from the real ORCA backend replaying real archived data. Run ORCA locally to ask anything, anywhere.
        </div>
      )}
      {open && (
        <div className="sheet" id="sheet" role="dialog" aria-label="All pages" onClick={() => setOpen(false)}>
          <div className="sheet-body" onClick={(e) => e.stopPropagation()}>
            {(["sea", "insight"] as const).map((g) => (
              <div key={g} className="sheet-group">
                <h2>{g === "sea" ? "At sea" : "Behind the answers"}</h2>
                <div className="sheet-grid">
                  {ROUTES.filter((r) => r.group === g).map((r, i) => (
                    <a key={r.id} href={r.id === "home" ? "#" : `#${r.id}`} onClick={(e) => (e.preventDefault(), go(r.id))} className={route === r.id ? "active" : ""} style={{ animationDelay: `${i * 35}ms` }}>
                      <Icon name={r.id} size={24} />
                      <b>{r.label}</b>
                      <span>{r.blurb}</span>
                    </a>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
