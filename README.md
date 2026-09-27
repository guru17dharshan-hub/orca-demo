# ORCA — Marine EcOsystem Reasoning with Collaborative Agents

Smart India Hackathon 2026 · Problem Statement **26176** · ISRO / Department of Space · Software · Space Technology

ORCA is an agentic, multilingual decision-support platform for fishermen and marine stakeholders. A user asks a
question in their own language — *"Is it safe to go fishing tomorrow at 6 AM from 15.2, 72.8?"*, *"कल सुबह गोवा से
समुद्र में जाना सुरक्षित है?"*, *"அருகிலுள்ள மீன்பிடி மண்டலம் எங்கே?"* — and ORCA plans the task, runs specialist
agents in parallel, evaluates the sea **hour by hour across the trip**, checks official warnings and boundaries, and
answers with a decision, the time it changes, the reason, a safer route when needed, and the evidence behind every
number.

```
SENSE → PREDICT → REASON → DECIDE → EXPLAIN
weather · ocean · warnings · GIS   forecast windows   agents + spatial/temporal   risk · route · alert · geofence   evidence · map · timeline · your language
```

**Core rule:** the LLM plans and explains; deterministic, versioned code decides safety. The LLM can never change a
risk level, invent a value or cite evidence that was not supplied (see *Verdict lock* below).

---

## What it does

| Official requirement (PS 26176) | ORCA |
|---|---|
| Natural-language, multi-turn conversation | Rule-based intent + entity extraction with LLM fallback; structured conversation state ("is it safe **there**?" → the zone from the previous answer) |
| Detect the user's language, reply in it (Indian languages) | Script-based detection for 10+ languages (native digits too); full replies in English, Hindi, Tamil, Telugu, Malayalam; any language via the LLM explainer |
| Discover, retrieve, integrate satellite / marine / met / GIS data | **Historical replay of real archived data**: NOAA GFS and GFS-Wave runs exactly as issued, NOAA OISST satellite SST, NOAA-20 VIIRS chlorophyll, and IMD's own CAP warnings from the WMO Alert Hub archive. Live adapters (Open-Meteo, IMD CAP feed, INCOIS PFZ) plug into the same interface |
| Spatial, temporal, contextual reasoning | Hour-by-hour **risk trajectory** with change points and go-windows; point-in-polygon for warnings/geofences; nearest viable PFZ; time-dependent routing |
| Explainable, evidence-based recommendations with maps/charts | Every value carries source, product, data type, valid time and reference; "Why?" drawer; rule table; map layers; risk timeline; exposure bars |
| Proactive alerts (weather, waves, lightning, cyclone) | Alert engine re-evaluates watched locations vs previous state and pushes via SSE; new official warnings covering a point raise an alert |
| Geofencing notifications | Maritime boundary (side-of-line test), restricted/protected/seasonal areas, approach warnings, vessel tracking alerts |
| Route optimisation and safe navigation | **Time-dependent A\***: risk evaluated at the boat's arrival time on each leg; explicit cost function; forbidden land/restricted/boundary/SEVERE; compared with the direct line |
| Collaborative agents | Planner → parallel specialists (data, weather, ocean, geospatial, risk, alerts, PFZ, route, EO, analytics) → explanation; full trace per request |

Full mapping with file references: [`docs/REQUIREMENTS_MAPPING.md`](docs/REQUIREMENTS_MAPPING.md).

---

## Quick start

Requirements: Python 3.11+, Node 20+.

```bash
# 1. backend
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest                                                  # 112 tests

# 2. web UI (build once; FastAPI serves web/dist)
cd ../web
npm install
npm run build

# 3. run (historical replay is the default: the archived data ships in backend/data/historical/)
cd ../backend
uvicorn orca.api:app --port 8000
# open http://localhost:8000
```

To re-download the archive or add an event: `pip install -r requirements-data.txt`, add the event to
`orca/historical/events.py`, then `python scripts/historical/fetch.py <event-id>` (about a minute per event).
`python scripts/historical/backtest.py` re-runs the backtest.

Development with hot reload: run `uvicorn orca.api:app --reload --port 8000` in `backend/` and `npm run dev` in `web/`
(Vite proxies `/api` to port 8000), then open http://localhost:5173.

Docker: `docker compose up --build` → http://localhost:8000.

Static preview (no server needed): with the backend running, `node scripts/record-demo.cjs http://localhost:8000`
in `web/` drives the real UI through every page and event and saves each API response; `npm run build:demo` then writes
`web/dist-demo/orca.html` plus `demo-data.json` and `land-india.png`. The preview replays those answers, so it only
answers the recorded questions.

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ORCA_DATA_MODE` | `historical` | `historical` = real archived data replayed as of a past moment, no look-ahead (default when the archive is present) · `live` = live sources only (failures surface as missing data, never faked) · `replay` = simulated scenario only · `auto` = live first, fall back to the labelled scenario (60 s circuit breaker) |
| `ORCA_REPLAY_EVENT` | `tauktae-2021` | Event loaded at start in historical mode (`tauktae-2021`, `michaung-2023`, `calm-jan-2024`); switch at runtime from the Time Machine page |
| `ORCA_LLM_PROVIDER` | `auto` | `auto` uses Claude when `ANTHROPIC_API_KEY` is set, otherwise template explanations · `anthropic` · `none` |
| `ANTHROPIC_API_KEY` | — | Enables the LLM explainer and intent fallback (install `anthropic`, included in `requirements.txt`) |
| `ORCA_ANTHROPIC_MODEL` | `claude-opus-5` | Model for explanations |
| `ORCA_LLM_EFFORT` | `low` | Explanations are short rewrites of structured evidence |
| `ORCA_ALERT_INTERVAL_S` | `300` | Background re-evaluation period for watched locations (0 = off) |
| `MOSDAC_USERNAME` / `MOSDAC_PASSWORD` | unset | ISRO MOSDAC login, only for `scripts/historical/fetch_mosdac.py` (INSAT-3DR/3D L3B daily SST into the replay archive). ORCA prefers ISRO SST over NOAA OISST wherever it has a cloud-free value |
| `ORCA_MOSDAC_SEARCH` | `1` | The catalogue agent searches ISRO MOSDAC (public, no login) for satellite products covering each question; `0` turns it off |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_SMS_FROM` / `TWILIO_WHATSAPP_FROM` | unset | SMS/WhatsApp alerts to subscribed phones; without them messages are kept in the `/api/outbox` demo outbox |
| `ORCA_ADMIN_TOKEN` | unset | When set, the shared clock, replay-event and re-evaluate endpoints require header `X-Orca-Admin-Token`; in the UI run `localStorage.setItem("orca.adminToken", "<token>")` once. Set it whenever the server is reachable by others |
| `GROQ_API_KEY` / `ORCA_GROQ_MODEL` | unset / `openai/gpt-oss-120b` | Groq LLM (used first by `ORCA_LLM_PROVIDER=auto`) |
| `GEMINI_API_KEY` / `ORCA_GEMINI_MODEL` | unset / `gemini-2.5-flash` | Gemini LLM; with both keys, `auto` tries Groq then Gemini (e.g. when Groq's free tier is rate-limited) |
| `ORCA_GEMINI_STT_MODEL` / `ORCA_WHISPER_MODEL` | `gemini-2.5-flash` / `whisper-large-v3` | Voice input: the chat mic uploads the recording to `/api/transcribe`, which transcribes it in the speaker's language and script (Gemini first — exact on Hindi/Tamil/Malayalam in tests — then Whisper on Groq). The transcript lands in the question box for the user to check. Without either key the browser's own recognizer is used (Chrome/Edge only) |

Without an API key ORCA is fully functional: answers come from deterministic multilingual templates.

---

## Historical replay and backtest

The mentors' guidance was to work on historical data until live access exists. ORCA ships three real events, with the
data exactly as it was published at the time (details and the no-look-ahead rules in
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md#historical-replay)):

| Event | What it shows |
|---|---|
| **Cyclone Tauktae**, Arabian Sea, May 2021 | On the evening of 14 May, ORCA says HIGH for boats off Goa next morning, cites IMD's real fishermen warnings, finds no safe route, and tracks the cyclone north along the coast |
| **Cyclone Michaung**, Bay of Bengal, Dec 2023 | Tamil and Telugu questions, a model cyclone watch before IMD's first warning, satellite chlorophyll zones |
| **Fishing-season week**, Arabian Sea, Jan 2024 | Calm seas, LOW verdicts, and fishing zones from real temperature fronts and chlorophyll |

**Backtest** (1 May–15 June 2021, 12 west-coast harbours, 552 harbour-mornings, ERA5 as truth): ORCA's evening
forecast caught 42 of 48 dangerous mornings (87.5%) against 77% for "tomorrow will be like this evening", with more
false alarms (29% vs 8%). The Time Machine page shows every harbour-morning.

## The web app

One page per feature, all in the navigation bar: **Bridge** (verdict, live chart of wind, waves, warnings and the
cyclone), **Ask ORCA**, **Sea Safety**, **Fishing Zones**, **Safe Route**, **Conditions**, **Alerts**,
**Boundaries**, **Time Machine**, **How it decided** and **Data & Rules**. The chart draws its own offline coastline
(GLOBE land mask), animated wind streaks from the GFS run, and colour fields for waves, sea temperature and
chlorophyll. Day and Night (ECDIS-style) themes; works on phones.

## Demo in 3 minutes

Step-by-step script: [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md).

1. **Bridge** (Tauktae, 14 May 2021, 21:30 IST): the verdict for tomorrow morning off Goa is **HIGH**, with the IMD
   warning that decides it; the chart shows the cyclone, its forecast track and the wind spiralling around it.
2. **Sea Safety**: hour-by-hour table; every cell names the factor and its level.
3. **Safe Route**: no safe route exists, and the page says so; the direct line passes through SEVERE seas.
4. **Alerts**: IMD's real warnings in the order they were sent; **Watch** Goa, **Fast-forward 6 h** twice, and ORCA
   warns as the forecast worsens.
5. **Time Machine**: switch to **Michaung**, ask in Tamil on **Ask ORCA**; then show the backtest grid.
6. **Fishing-season week**: **Fishing Zones** with chlorophyll; **How it decided** for the plan and evidence.

---

## Architecture (short)

```
Web / PWA (React, Leaflet)  ── REST + SSE ──  FastAPI
                                                │
                           Intent agent (language, intents, entities; LLM fallback)
                                                │
                           Planner (context rules, dependency-ordered tasks)
                                                │
          ┌──────────────┬──────────────┬───────┴──────┬──────────────┬─────────────┐
     data discovery   weather agent   ocean agent   PFZ agent   EO agent   analytics agent
          │  (adapters: Open-Meteo · IMD CAP · INCOIS PFZ · replay scenario)
     Marine State (normalized observations + advisories, provenance on every value)
          │
     geospatial engine · risk engine (versioned rules, hourly trajectory) · route engine (time-dependent A*) · alert engine
          │
     Explanation agent (Claude, verdict lock) or multilingual templates
          │
     answer + structured cards + map features + evidence + trace
```

Details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Data sources and their verified status:
[`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md).

### Verdict lock

When an LLM is configured, it receives a compact evidence packet and must return JSON. The answer is **discarded** and
replaced by the deterministic template if the LLM's risk level differs from the engine's, it cites an unknown evidence
id, it contains any number that is not in the packet (native-script digits included), or it calls conditions "safe"
when the engine says HIGH / SEVERE / cannot confirm. The UI never parses answer text; it renders structured fields.

---

## Honesty and limitations

- **Historical replay is labelled** in the navigation bar and in every answer, with the event and the replay moment.
  Values derived from model fields (thunderstorm, cyclone watch) are tagged DERIVED; fishing zones are candidates
  computed from satellite data, not INCOIS advisories. The simulated scenario remains for tests and `replay` mode.
- **Risk thresholds** are anchored to WMO sea-state, Beaufort, marine visibility terms and CAP severity, but their
  mapping to ORCA levels is a prototype policy (`orca-rules-0.2.1`) that must be validated with INCOIS/IMD.
- **Geofence layers are not authoritative yet**: the India–Sri Lanka boundary points are an unverified transcription of
  the 1974/1976 agreements; protected areas are approximate envelopes. Each feature shows its accuracy label.
- **INCOIS PFZ** WFS integration is written but was not reachable from the development network; verify the endpoint.
- Tide and currents are not in the replay archive. No ML forecasting yet; forecasts come from NOAA GFS (replay) or
  Open-Meteo (live). See *Next steps* in
  [`docs/REQUIREMENTS_MAPPING.md`](docs/REQUIREMENTS_MAPPING.md#next-steps).

Decision support only — always follow official IMD/INCOIS advisories.

## Repository layout

```
backend/orca/
  adapters/      Open-Meteo, IMD CAP, historical archive, replay (MarineDataAdapter: fetch → normalize → health)
  historical/    event catalogue, archive reader (no look-ahead), satellite fishing zones, map layers
  agents/        intent, planner, specialists, orchestrator, explanation, context
  geo/           geometry, land mask, geofences (+ data/geofences.json), ports gazetteer, regulations
  risk/          rules.py (the only place thresholds live), engine.py (hourly trajectory)
  route/         time-dependent risk-aware A*
  i18n/          language detection, response templates
  llm/           provider abstraction (Claude / none / scripted)
  pfz.py alerts.py api.py data_service.py scenario.py state.py trace.py
backend/data/    historical/<event>/ archived NOAA + IMD data (≈10 MB), backtest/results.json
backend/scripts/ historical/fetch.py, historical/backtest.py, make_land_png.py
backend/tests/   112 tests (engine, adapters, spatial, agents, API, historical replay)
web/src/         React UI: pages/ (one per feature), ui/ (chart, dial, charts, navbar), store, router
docs/            architecture, requirement mapping, data sources, demo script
```
