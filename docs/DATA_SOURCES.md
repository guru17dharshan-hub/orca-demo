# Data sources

Every value ORCA uses is normalized into a `MarineObservation` (or `Advisory`) carrying `source`, `product`,
`data_type` (`forecast | observation | historical | derived | simulated | official_advisory`), valid time, retrieval
time and a reference. Status below is what was **verified during development**, not what a source claims.

| Source | Adapter | Variables | Access | Status |
|---|---|---|---|---|
| Open-Meteo Weather API | `adapters/open_meteo.py` | wind, gusts, direction, WMO weather code, visibility, rain, CAPE | keyless HTTPS, multi-point batches | Implemented against the documented response format and tested with recorded-shape fixtures. **Blocked by the development network**, so not yet exercised live — run `ORCA_DATA_MODE=live` on your network once to confirm. |
| Open-Meteo Marine API | `adapters/open_meteo.py` | significant wave height, swell, period, SST, ocean current, sea level incl. tide | keyless HTTPS | Same as above. Sea level is a model value including tide, not a tide table. |
| IMD CAP alerts (via WMO Alert Hub mirror) | `adapters/advisories.py` | official warnings: event, severity, urgency, certainty, onset/expiry, **area polygons** | `https://cap-sources.s3.amazonaws.com/in-imd-en/rss.xml` (public domain) | **Verified live** (Sept 2026). A real alert is stored as a test fixture (`backend/tests/fixtures/imd_cap_2026-09-23.xml`). Point-in-polygon decides whether a location is inside a warning. |
| INCOIS PFZ GeoServer WFS | `pfz.py` (`INCOISPFZProvider`) | PFZ lines/areas | `https://incois.gov.in/geoserver/PFZ_Automation/ows` (`PFZ_Automation:pfzlines`) | **Experimental**: endpoint not reachable from the development network; parsed as generic GeoJSON. If it fails, `auto` mode shows DEMO zones (labelled). |
| GLOBE 1-km land mask | `geo/land.py` | land/sea | bundled with `global-land-mask` | Offline; used for routing and harbour snapping. |
| Geofence layers | `geo/data/geofences.json` | boundary, protected, sensitive, restricted | bundled | See *Geofences* below — not authoritative yet. |
| **NOAA GFS 0.5° runs (archive)** | `adapters/historical.py` | 10 m wind, gusts, visibility, rain rate, convective rain, lifted index, sea-level pressure | byte-range GRIB2 from `s3://noaa-gfs-bdp-pds` (NOAA Open Data, public) | **Downloaded and verified** for three events (see *Historical replay*). |
| **NOAA GFS-Wave 0.25° runs (archive)** | `adapters/historical.py` | significant wave height, peak period | same bucket | **Downloaded and verified.** |
| **NOAA OISST v2.1** | `historical/archive.py` | daily SST and anomaly vs 1971–2000 (AVHRR satellite + in-situ) | NetCDF, `s3://noaa-cdr-sea-surface-temp-optimum-interpolation-pds` | **Downloaded and verified.** |
| **NOAA-20 VIIRS chlorophyll-a** | `historical/archive.py` | OC3 chlorophyll, NOAA STAR | HDF4 swaths, `s3://noaa-jpss/NOAA20/VIIRS/NOAA20_VIIRS_OC_GLOBAL_CHLOR-A_ops/` | **Downloaded and verified**; archive starts 2022, so Tauktae (2021) has none. |
| **IMD CAP archive** | `adapters/historical.py` | IMD warnings exactly as sent, with polygons | `s3://cap-sources/in-imd-en/` (WMO Alert Hub keeps every message since 2019) | **Downloaded and verified**: 22 messages for Tauktae, 2 for Michaung, 18 for January 2024. |
| **ECMWF ERA5 (ARCO)** | `scripts/historical/backtest.py` | hourly waves and wind | Zarr on `gs://gcp-public-data-arco-era5` (Google public data) | Used only as the backtest's independent truth. |
| Simulated scenario | `scenario.py`, `adapters/replay.py` | all of the above + chlorophyll + 60-day history | offline | Synthetic, always labelled SIMULATED. Kept for tests and `replay` mode. |

## Historical replay

`ORCA_DATA_MODE=historical` (the default when the archive is present) serves **real archived data** replayed as of a
past moment. `scripts/historical/fetch.py` downloads each event's region and stores it compactly
(`backend/data/historical/<event>/`, int16 arrays, about 1.7–4.8 MB per event; raw downloads are not kept).

| Event | Replay window | Default moment | Place | Products |
|---|---|---|---|---|
| Cyclone Tauktae, Arabian Sea | 13–18 May 2021 | 14 May 2021, 21:30 IST | off Mormugao, Goa | GFS, GFS-Wave, OISST, 22 IMD CAP messages |
| Cyclone Michaung, Bay of Bengal | 1–6 Dec 2023 | 2 Dec 2023, 21:30 IST | off Chennai | + VIIRS chlorophyll (27–29 Nov) |
| Fishing-season week, Arabian Sea | 15–19 Jan 2024 | 16 Jan 2024, 21:30 IST | off Mormugao, Goa | + VIIRS chlorophyll (13–15 Jan) |

**No look-ahead.** At the replay moment ORCA uses only what had been published:

- **GFS / GFS-Wave**: the latest 00Z/12Z run whose files were on NOAA's server by then (the S3 upload time is recorded
  per run, e.g. the 14 May 2021 12Z wave run landed at 15:43 UTC). Hours after the replay moment are that run's
  forecast (`data_type=forecast`); earlier hours come from the run that covered them (`historical`). Leads 0–48 h,
  every 3 h, interpolated to the hour.
- **OISST**: the latest day released, assuming a one-and-a-half-day release lag.
- **Chlorophyll**: a three-day composite (geometric mean of cloud-free passes, 0.045°), usable the day after its last day.
- **IMD warnings**: only messages already sent.

**Derived values** (labelled `derived`, never presented as observed):

- *Thunderstorm*: GFS has no present-weather code. ORCA sets WMO code 95 when the lifted index is ≤ −3 °C (the usual
  "thunderstorms probable" class) **and** model convective rain is ≥ 1 mm/h. Rain codes 61/63/65 follow the AMS
  rain-rate classes (2.5 and 7.6 mm/h). Prototype derivation; validate against IMD lightning data.
- *Cyclone watch*: the lowest sea-level pressure over sea that is a closed low (≥ 4 hPa below a 400–700 km ring),
  classified on IMD's wind scale by the strongest 10 m wind within 250 km. Shown on maps and in answers as "not an IMD
  warning"; **not scored** by the risk engine (the winds and waves themselves are).

Not available in the replay: tide (sea level) and currents. Answers say so instead of inventing them.

## Fishing zones from satellite data

`historical/pfz.py` reproduces the idea behind INCOIS PFZ advisories with public data: an **SST front** (OISST gradient
in the top 10% of the region's sea cells) that is also **productive** (VIIRS chlorophyll in the top quarter, when the
archive has it). Connected cells form a zone; cells within 10 km of land are skipped because satellite chlorophyll is
unreliable in turbid near-shore (Case-2) water, and inland lakes are masked out. Thresholds are percentiles, so no
absolute value is assumed. Zones are labelled "candidate zones computed by ORCA — not INCOIS advisories".

## Backtest

`scripts/historical/backtest.py` scores ORCA's evening verdict (06:00–12:00 IST next morning, decided from the GFS run
published by 21:30 IST) against ECMWF ERA5 at 12 west-coast harbours, 1 May–15 June 2021 (Cyclone Tauktae and the
monsoon onset), with ORCA's own rule set (waves + wind). Results (`data/backtest/results.json`, served at
`/api/backtest`):

| | ORCA forecast | Persistence ("tomorrow = this evening") |
|---|---|---|
| Harbour-mornings | 552 | 552 |
| Dangerous (HIGH/SEVERE) in ERA5 | 48 | 48 |
| Caught | 42 (87.5%) | 37 (77%) |
| Missed | 6 | 11 |
| False alarms (share of danger calls) | 29% | 8% |
| Exact level | 82% | 90% |

ORCA catches more dangerous mornings at the cost of more false alarms, which is the right side to err on for a
safety tool. Persistence gets the exact level right more often (90% vs 82%) because most mornings simply repeat the
evening; it is the changes, above all the onset of danger, that the forecast adds. Warnings, thunderstorms and visibility are not part of the test (no independent truth for them), and ERA5
is itself a reanalysis, not a buoy record.

## Simulated scenario

`scenario.py` — **synthetic data**, used in `replay` mode and as the `auto`-mode fallback so the full pipeline can be
demonstrated and tested without network access.

- **Story:** a depression over the east-central Arabian Sea moves north-east towards the Goa–Konkan coast. "Scenario
  day 1" is always *tomorrow* (IST) relative to server start, so "tomorrow morning" questions are meaningful.
- **Fields:** a parametric wind field (gale edge ~290 km from the centre), fetch-limited wind waves
  (Hs ≈ 0.0165·U²) plus swell, a thunderstorm rain band, a short-lived coastal thunderstorm cell off Goa
  (05:00–10:30 IST day 1, to demonstrate time-dependent routing), coastal upwelling SST, chlorophyll fronts at the
  DEMO PFZ positions, semi-diurnal tides with larger amplitude in the Gulf of Khambhat, and a simulated marine heatwave
  off Kerala in the last 21 days (for "why did productivity decline?").
- **Simulated advisory:** a "Depression — squally weather" polygon issued at 14:00 IST on day 0, valid from 10:00 IST day 1.
  Its source string says *"simulated — NOT an IMD bulletin"*.
- **Golden journey:** at 15.2°N 72.8°E the risk is LOW at dawn, MODERATE by ~08:30 IST and HIGH from ~09:30 IST
  (guide §18), which the tests assert.

Nothing from the scenario may be presented as real INCOIS/IMD/ISRO data. The UI shows a SIMULATED badge and each
evidence item is tagged.

## Geofences

| Feature | Accuracy label | Notes |
|---|---|---|
| India–Sri Lanka maritime boundary (Palk Strait & Gulf of Mannar) | `unverified-transcription` | Turning points from the 1974 and 1976 agreements, transcribed for this prototype and **not yet checked** against the treaty text or NHO charts. Bay of Bengal segment not included. |
| Gulf of Mannar Marine National Park | `approximate` | Hand-drawn envelope around the island chain. Replace with WDPA / notified boundary. |
| Malvan Marine Sanctuary | `approximate` | As above. |
| Marine National Park, Gulf of Kachchh | `approximate` | As above. |
| Gahirmatha (olive ridley nesting season, Nov–May) | `approximate` | Seasonal; State sets exact dates. |
| DEMO restricted exercise area off Goa | `fictional-demo` | Does not exist. Demonstrates restricted-area warnings and route avoidance. |
| Active warning areas (IMD CAP / simulated) | `official-advisory` / `simulated` | Dynamic hazard zones; scored by the risk engine, not treated as hard constraints. |

The annual fishing-ban periods (east coast 15 Apr – 14 Jun, west coast 1 Jun – 31 Jul) are implemented in
`geo/regulations.py`; verify against the current year's Department of Fisheries notification.

**Before operational use:** replace these with authoritative layers (NHO / MoES / Marine Regions EEZ, WDPA or State
notifications) and set their accuracy to `official`.

## Risk thresholds

`risk/rules.py` is the only place thresholds live. Version `orca-rules-0.2.1`:

| Factor | LOW | MODERATE | HIGH | SEVERE | Reference |
|---|---|---|---|---|---|
| Significant wave height | < 1.25 m | 1.25–2.5 m | 2.5–4 m | ≥ 4 m | WMO sea-state code 3700 |
| Sustained wind (10 m) | < 29 km/h | 29–39 | 39–62 | ≥ 62 | Beaufort scale (≤4, 5, 6–7, ≥8) |
| Visibility | ≥ 3.7 km | 1–3.7 km | < 1 km | — | Marine-forecast visibility terms |
| Thunderstorm (WMO code 95/96/99) | — | — | HIGH | — | WMO code table 4677 |
| Official warning covering the point | Minor | Moderate | Severe | Extreme | CAP 1.2 severity from the issuing agency |

**Coastal warnings (new in 0.2.0).** IMD draws "along and off the coast" warning polygons coarsely: during Tauktae the
Maharashtra–Goa fishermen warning ended about 40 km off the Goa coast, so a boat just off Goa was technically outside
it. Official warnings whose area or text names a coast now also cover points within **50 km** of their polygon.
Model-derived cyclone watches are shown but never scored.

Gusts, currents, swell, rain and CAPE are shown as evidence but **not scored** until citable thresholds are adopted.
Missing wave height or wind → `INSUFFICIENT_DATA` ("cannot confirm"), never "safe". The mapping from scales to levels is
a prototype policy to be validated with INCOIS/IMD.
