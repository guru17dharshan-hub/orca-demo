// The fishermen's page: one decision in the fisherman's language, big enough to read on a phone at the jetty
// before dawn, and read aloud. Everything comes from the same deterministic engine as the rest of ORCA
// (/api/risk at the harbour's sea point, /api/pfz from the harbour); nothing here is generated text.
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { SPEECH_LOCALE } from "../i18n";
import { go } from "../router";
import { useApi, useApp } from "../store";
import { istAt } from "../time";
import type { Level, SafetyCard } from "../types";
import Icon from "../ui/Icon";
import { FM_LANGS, dir8, fm, type FmLang } from "./fishermen-i18n";
import "./fishermen.css";

type Verdict = "go" | "care" | "no" | "unsure";
const VERDICT: Record<Level, Verdict> = { LOW: "go", MODERATE: "care", HIGH: "no", SEVERE: "no", INSUFFICIENT_DATA: "unsure" };
const RANK: Record<string, number> = { LOW: 0, MODERATE: 1, HIGH: 2, SEVERE: 3 };

function load(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function save(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable: the choice lasts for this visit only */
  }
}

/** Does this phone have its own voice for the language? (Many have Hindi and English but not Tamil, Telugu or
 *  Malayalam.) The phone's voice is instant and free; the server voice covers the rest. */
function hasLocalVoice(lang: string): boolean {
  if (!("speechSynthesis" in window)) return false;
  return window.speechSynthesis.getVoices().some((v) => v.lang.toLowerCase().startsWith(lang));
}

const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });

/** Largest value of a variable over the window's hours, with its level. */
function peak(d: SafetyCard, variable: string): { value: number; level: Level } | null {
  let best: { value: number; level: Level } | null = null;
  for (const h of d.timeline) {
    const f = h.factors[variable];
    if (f && typeof f.value === "number" && (!best || f.value > best.value)) best = { value: f.value, level: f.level as Level };
  }
  return best;
}

function VerdictGlyph({ v }: { v: Verdict }) {
  // A boat on the water; the mark beside it carries the verdict for anyone who does not read.
  return (
    <svg viewBox="0 0 96 96" className={`fm-glyph g-${v}`} aria-hidden>
      <circle cx="48" cy="48" r="44" className="g-disc" />
      <g className="g-boat">
        <path d="M20 56h46l-7 11H27z" />
        <path d="M40 56V26l18 26H40" className="g-sail" />
      </g>
      <path d="M12 74c6-4 12-4 18 0s12 4 18 0 12-4 18 0 12 4 18 0" className="g-sea" />
      {v === "go" && <path d="M64 30l6 6 12-13" className="g-mark" />}
      {v === "care" && <path d="M74 20v12M74 38v.5" className="g-mark" />}
      {v === "no" && <path d="M66 22l14 14M80 22L66 36" className="g-mark" />}
      {v === "unsure" && <path d="M68 24a6 6 0 1 1 8 6c-2 1-2 2-2 4M74 40v.5" className="g-mark" />}
    </svg>
  );
}

export default function Fishermen() {
  const { clock, ports, place, replay, events, setLang, health } = useApp();
  const [lang, setFmLang] = useState<FmLang>(() => (load("orca.fmLang") as FmLang) || "en");
  const [portId, setPortId] = useState<string | null>(() => load("orca.harbour"));
  const [when, setWhen] = useState<"now" | "tomorrow">("tomorrow");
  const [phone, setPhone] = useState("");
  const [channel, setChannel] = useState<"sms" | "whatsapp">("sms");
  const [subState, setSubState] = useState<"idle" | "sending" | "done" | "bad">("idle");
  const [online, setOnline] = useState(() => (typeof navigator === "undefined" ? true : navigator.onLine));
  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener("online", up);
    window.addEventListener("offline", down);
    return () => {
      window.removeEventListener("online", up);
      window.removeEventListener("offline", down);
    };
  }, []);
  const t = (k: string, vars?: Record<string, string | number>) => fm(lang, k, vars);

  useEffect(() => {
    save("orca.fmLang", lang);
    setLang(lang); // answers in Ask ORCA follow the same language
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lang]);

  const nearest = useMemo(
    () => (ports.length ? ports.reduce((b, p) => ((p.lat - place.lat) ** 2 + (p.lon - place.lon) ** 2 < (b.lat - place.lat) ** 2 + (b.lon - place.lon) ** 2 ? p : b)) : null),
    [ports, place.lat, place.lon],
  );
  const port = ports.find((p) => p.id === portId) ?? nearest;
  const [lat, lon] = port ? port.sea_point : [place.lat, place.lon];

  // Now = the next 8 hours; tomorrow = 04:00–12:00 IST, when boats leave and come back.
  const span = useMemo(() => {
    if (!clock) return null;
    if (when === "now") {
      const s = new Date(clock);
      s.setUTCMinutes(0, 0, 0);
      return { start: s.toISOString().replace(".000Z", "Z"), end: new Date(s.getTime() + 8 * 3600_000).toISOString().replace(".000Z", "Z") };
    }
    return { start: istAt(clock, 1, 4), end: istAt(clock, 1, 12) };
  }, [clock, when]);

  const risk = useApi(() => (port && span ? api.risk(lat, lon, span.start, span.end) : null), [lat, lon, span?.start, span?.end]);
  const pfz = useApi(() => (port && clock ? api.pfz(port.lat, port.lon, 3) : null), [port?.id, clock]);
  const d = risk.data?.decision ?? null;
  const verdict: Verdict | null = d ? VERDICT[d.risk_level] : null;

  const best = d?.go_windows.length ? [...d.go_windows].sort((a, b) => b.hours - a.hours)[0] : null;
  const turn = d?.change_points.find((c) => c.direction === "rising" && RANK[c.to] >= 2) ?? null;
  const waves = d ? peak(d, "wave_height") : null;
  const wind = d ? peak(d, "wind_speed") : null;
  const warnings = (d?.advisories ?? []).filter((a) => a.data_type === "official_advisory");
  const zone = pfz.data?.candidates.find((c) => c.viable) ?? null;
  // Why: the factor that set the verdict (the engine's first key factor), named in plain words.
  const cause = verdict === "care" || verdict === "no" ? d?.key_factors[0]?.variable ?? null : null;
  const why = cause ? `${t("because")} ${t(`r_${cause}`)}` : null;
  const ev = events.find((e) => e.id === replay?.event);

  const seaWord = (l: Level | undefined) => ({ LOW: "calm", MODERATE: "moderate", HIGH: "rough", SEVERE: "veryRough" } as Record<string, string>)[l ?? ""] ?? "moderate";
  const windWord = (l: Level | undefined) => ({ LOW: "light", MODERATE: "moderate", HIGH: "strong", SEVERE: "gale" } as Record<string, string>)[l ?? ""] ?? "moderate";

  // What is read aloud: the verdict and what to do, in the chosen language.
  const spoken = useMemo(() => {
    if (!verdict) return "";
    const parts = [t(verdict) + ".", t(`${verdict}Line`)];
    if (why) parts.push(why + ".");
    if (best && verdict !== "no") parts.push(`${t("bestTime")}: ${hhmm(best.start)} – ${hhmm(best.end)}.`);
    if (turn) parts.push(`${t("backBy")} ${hhmm(turn.time)}${turn.cause ? `, ${t("because")} ${t(`r_${turn.cause.variable}`)}` : ""}.`);
    if (warnings.length) parts.push(`${t("warnings")}: ${warnings.length}.`);
    return parts.join(" ");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [verdict, why, best, turn, warnings.length, lang]);

  const [speaking, setSpeaking] = useState(false);
  const [fetching, setFetching] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Server audio takes ~10–15 s to make, so it is fetched as soon as the verdict is known — only for languages
  // the phone cannot speak itself — and is ready by the time "Listen" is tapped.
  const prefetch = useRef<{ key: string; audio: Promise<Blob> } | null>(null);
  const serverVoice = !!health?.tts?.available;
  useEffect(() => {
    if (!spoken || !serverVoice || hasLocalVoice(lang)) return;
    const key = `${lang}|${spoken}`;
    if (prefetch.current?.key === key) return;
    const audio = api.speak(spoken, lang);
    audio.catch(() => undefined);
    prefetch.current = { key, audio };
  }, [spoken, lang, serverVoice]);
  useEffect(() => () => stopSpeaking(), []); // eslint-disable-line react-hooks/exhaustive-deps
  const stopSpeaking = () => {
    setFetching(false);
    audioRef.current?.pause();
    audioRef.current = null;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    setSpeaking(false);
  };
  const speak = async () => {
    if (speaking || fetching) return stopSpeaking();
    if (!spoken) return;
    // The phone's own voice when it has this language; otherwise the server voice (prefetched).
    if (!hasLocalVoice(lang) && serverVoice) {
      const key = `${lang}|${spoken}`;
      const pending = prefetch.current?.key === key ? prefetch.current.audio : api.speak(spoken, lang);
      setFetching(true);
      try {
        const wav = await pending;
        const audio = new Audio(URL.createObjectURL(wav));
        audioRef.current = audio;
        audio.onended = () => setSpeaking(false);
        setFetching(false);
        setSpeaking(true);
        await audio.play();
        return;
      } catch {
        setFetching(false); // fall through to whatever voice the phone has
      }
    }
    setSpeaking(true);
    if ("speechSynthesis" in window) {
      const u = new SpeechSynthesisUtterance(spoken);
      u.lang = SPEECH_LOCALE[lang] ?? "en-IN";
      u.onend = () => setSpeaking(false);
      window.speechSynthesis.speak(u);
    } else setSpeaking(false);
  };
  useEffect(stopSpeaking, [spoken]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <main className="page fm" id="main" lang={lang}>
      <header className="fm-head">
        <div className="fm-title">
          <span className="emblem" aria-hidden>
            <Icon name="fishermen" size={30} className="draw" />
          </span>
          <div>
            <h1>{t("title")}</h1>
            <p>{t("sub")}</p>
          </div>
        </div>
        <div className="fm-langs" role="radiogroup" aria-label="Language">
          {FM_LANGS.map((l) => (
            <button key={l.code} role="radio" aria-checked={lang === l.code} className={`fm-lang ${lang === l.code ? "on" : ""}`} lang={l.code} onClick={() => setFmLang(l.code)}>
              {l.label}
            </button>
          ))}
        </div>
      </header>

      {!online && <p className="fm-replay fm-offline">{t("offline")}</p>}
      {replay && ev && <p className="fm-replay">{t("replay", { event: ev.title.split(" — ")[0] })}</p>}

      <div className="fm-controls">
        <label className="fm-harbour">
          <span>{t("harbour")}</span>
          <select
            value={port?.id ?? ""}
            onChange={(e) => {
              setPortId(e.target.value);
              save("orca.harbour", e.target.value);
            }}
          >
            {ports.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </label>
        <div className="fm-when" role="radiogroup" aria-label="When">
          {(["now", "tomorrow"] as const).map((w) => (
            <button key={w} role="radio" aria-checked={when === w} className={when === w ? "on" : ""} onClick={() => setWhen(w)}>
              {t(w)}
            </button>
          ))}
        </div>
      </div>

      <section className={`fm-verdict ${verdict ? `v-${verdict}` : "v-wait"}`} aria-live="polite">
        {!d ? (
          <p className="fm-wait">
            <span className="sonar" aria-hidden /> {risk.error ?? t("loading")}
          </p>
        ) : (
          <>
            <VerdictGlyph v={verdict!} />
            <div className="fm-verdict-text">
              <h2>{t(verdict!)}</h2>
              <p>{t(`${verdict}Line`)}</p>
              {why && <p className="fm-why">{why}</p>}
            </div>
            <button className="fm-listen" onClick={speak} aria-pressed={speaking} aria-busy={fetching}>
              {fetching ? <span className="mic-busy" /> : <Icon name={speaking ? "stop" : "speaker"} size={26} />}
              {t(speaking ? "stop" : "listen")}
            </button>
          </>
        )}
      </section>

      {d && (
        <div className="fm-grid">
          <section className="fm-card">
            <h3>
              <Icon name="replay" size={22} /> {t("bestTime")}
            </h3>
            {best && verdict !== "no" ? <p className="fm-big mono">{hhmm(best.start)} – {hhmm(best.end)}</p> : <p className="fm-mid">{t("noBest")}</p>}
            {turn ? (
              <p className="fm-back">
                {t("backBy")} <b className="mono">{hhmm(turn.time)}</b>
                {turn.cause && <> · {t(`r_${turn.cause.variable}`)}</>}
              </p>
            ) : (
              <p className="fm-note">{t("steady")}</p>
            )}
          </section>

          <section className="fm-card">
            <h3>
              <Icon name="conditions" size={22} /> {t("sea")}
            </h3>
            <div className="fm-sea">
              <div className={`lv-${(waves?.level ?? "INSUFFICIENT_DATA").toLowerCase()}`}>
                <span>{t("waves")}</span>
                <b>{waves ? t(seaWord(waves.level)) : "—"}</b>
                <small className="mono">{waves ? `${waves.value.toFixed(1)} m` : ""}</small>
              </div>
              <div className={`lv-${(wind?.level ?? "INSUFFICIENT_DATA").toLowerCase()}`}>
                <span>{t("wind")}</span>
                <b>{wind ? t(windWord(wind.level)) : "—"}</b>
                <small className="mono">{wind ? `${Math.round(wind.value)} km/h` : ""}</small>
              </div>
            </div>
          </section>

          <section className={`fm-card ${warnings.length ? "fm-alert" : ""}`}>
            <h3>
              <Icon name="alerts" size={22} /> {t("warnings")}
            </h3>
            {warnings.length ? (
              <>
                <p className="fm-big">{warnings.length}</p>
                <ul className="fm-warn-list">
                  {warnings.slice(0, 2).map((w) => (
                    <li key={w.id} lang="en">
                      {w.event}
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="fm-mid">{t("noWarn")}</p>
            )}
          </section>

          <section className="fm-card">
            <h3>
              <Icon name="zones" size={22} /> {t("zone")}
            </h3>
            {zone ? (
              <div className="fm-zone">
                <svg viewBox="-30 -30 60 60" className="fm-compass" aria-hidden>
                  <circle r="27" />
                  <text y="-17">N</text>
                  <g style={{ transform: `rotate(${zone.bearing_deg}deg)` }} className="fm-needle">
                    <path d="M0 -22 L6 4 L0 0 L-6 4 Z" />
                  </g>
                </svg>
                <div>
                  <p className="fm-mid">{t("away", { d: Math.round(zone.distance_km), dir: t(`d_${dir8(zone.compass)}`) })}</p>
                  <p className="fm-note" lang="en">
                    {zone.zone.name}
                  </p>
                </div>
              </div>
            ) : (
              <p className="fm-mid">{pfz.loading ? "…" : t("zoneNone")}</p>
            )}
          </section>
        </div>
      )}

      <div className="fm-actions">
        <button className="fm-ask" onClick={() => go("ask")}>
          <Icon name="mic" size={24} /> {t("askVoice")}
        </button>
      </div>

      <section className="fm-card fm-subscribe">
        <h3>
          <Icon name="alerts" size={22} /> {t("alertsTitle")}
        </h3>
        <p className="fm-note">{t("alertsSub", { harbour: port?.name ?? "" })}</p>
        {subState === "done" ? (
          <p className="fm-mid fm-ok">{t("subscribed")}</p>
        ) : (
          <form
            className="fm-sub-form"
            onSubmit={async (e) => {
              e.preventDefault();
              const clean = phone.replace(/[\s-]/g, "");
              const full = clean.startsWith("+") ? clean : `+91${clean.replace(/^0/, "")}`;
              if (!/^\+[1-9]\d{7,14}$/.test(full) || !port) return setSubState("bad");
              setSubState("sending");
              try {
                await api.subscribe({ phone: full, channel, harbour_id: port.id, language: lang });
                setSubState("done");
              } catch {
                setSubState("bad");
              }
            }}
          >
            <label>
              <span>{t("phone")}</span>
              <input type="tel" inputMode="tel" autoComplete="tel" placeholder="+91 98765 43210" value={phone} onChange={(e) => setPhone(e.target.value)} />
            </label>
            <div className="fm-when" role="radiogroup" aria-label="Channel">
              {(["sms", "whatsapp"] as const).map((c) => (
                <button type="button" key={c} role="radio" aria-checked={channel === c} className={channel === c ? "on" : ""} onClick={() => setChannel(c)}>
                  {t(c)}
                </button>
              ))}
            </div>
            <button type="submit" className="fm-ask" disabled={subState === "sending"}>
              {t("subscribe")}
            </button>
            {subState === "bad" && <p className="fm-note fm-bad">{t("badPhone")}</p>}
          </form>
        )}
      </section>

      <section className="fm-sos" aria-label={t("emergency")}>
        <h3>{t("emergency")}</h3>
        <div>
          <a href="tel:1554" className="fm-call">
            <b>1554</b> {t("coastGuard")}
          </a>
          <a href="tel:112" className="fm-call">
            <b>112</b> {t("emergency112")}
          </a>
        </div>
      </section>

      <p className="fm-disclaimer">{t("disclaimer")}</p>
    </main>
  );
}
