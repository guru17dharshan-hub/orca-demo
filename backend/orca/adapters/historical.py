"""Historical adapters: real archived data served through the same interface as live sources.

HistoricalMarineAdapter  — NOAA GFS 0.5° + GFS-Wave 0.25° runs exactly as issued, NOAA OISST
                           sea-surface temperature and NOAA-20 VIIRS chlorophyll, as of the
                           replay clock (no look-ahead: see orca/historical/archive.py).
HistoricalCAPAdapter     — IMD CAP warnings as sent, from the WMO Alert Hub archive.
CycloneWatchAdapter      — cyclone centre found in the same GFS run (lowest sea-level pressure
                           over sea) and classified on IMD's wind scale. Labelled DERIVED, not
                           an IMD warning, and never scored by the risk engine."""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import numpy as np

from ..geo.land import is_land
from ..historical.archive import EventArchive, Sample, wind_from_uv
from ..historical.events import HistoricalEvent
from ..models import Advisory, DataType, MarineObservation
from ..timeutil import UTC, ensure_utc, hours_between
from ..variables import validate
from .advisories import AdvisoryAdapter, parse_cap
from .base import MarineDataAdapter, PointQuery, RawPayload

REFERENCE = "docs/DATA_SOURCES.md#historical-replay"
PROCESSING_VERSION = "historical-normalizer/1"
GFS_SOURCE = "NOAA GFS 0.5° (archived run)"
WAVE_SOURCE = "NOAA GFS-Wave 0.25° (archived run)"
SST_SOURCE = "NOAA OISST v2.1 (AVHRR satellite + in-situ)"
ISRO_SST_SOURCE = "ISRO INSAT-3DR/3D Imager — L3B daily SST (MOSDAC, SAC-ISRO)"
TIDE_SOURCE = "Open-Meteo Marine — sea level incl. tides (model prediction)"
CHL_SOURCE = "NOAA-20 VIIRS chlorophyll-a (NOAA STAR, OC3)"
CAP_ARCHIVE = "https://cap-sources.s3.amazonaws.com/in-imd-en/"

# Thunderstorm from model fields (GFS has no present-weather code). Lifted Index ≤ −3 °C is the
# usual 'thunderstorms probable' class; model convective rain must also be falling. Prototype
# derivation — validate against IMD lightning data before operational use.
THUNDER_LIFTED_INDEX = -3.0
THUNDER_CONVECTIVE_MM_H = 1.0
# Rain intensity classes (mm/h): light < 2.5 ≤ moderate < 7.6 ≤ heavy (AMS Glossary of Meteorology)
RAIN_CODES = ((7.6, 65), (2.5, 63), (0.5, 61))

ROUTE_VARIABLES = ("wave_height", "wind_speed", "weather_code", "visibility")


def derive_weather_code(lifted_index: float | None, convective_mm_h: float | None, rain_mm_h: float | None) -> int | None:
    if lifted_index is not None and convective_mm_h is not None:
        if lifted_index <= THUNDER_LIFTED_INDEX and convective_mm_h >= THUNDER_CONVECTIVE_MM_H:
            return 95
    if rain_mm_h is not None:
        for threshold, code in RAIN_CODES:
            if rain_mm_h >= threshold:
                return code
    return None


class ReplayContext:
    """Which event is loaded and what time it is in the replay (shared by all historical adapters)."""

    def __init__(self, event: HistoricalEvent, clock: Callable[[], datetime], root: Path | None = None) -> None:
        self.clock = clock
        self._root = root
        self.set_event(event)

    def set_event(self, event: HistoricalEvent) -> None:
        self.event = event
        self.archive = EventArchive(event, self._root) if self._root else EventArchive(event)

    def as_of(self) -> datetime:
        return ensure_utc(self.clock())


class HistoricalMarineAdapter(MarineDataAdapter):
    name = "noaa-archive"
    mode = "historical"

    def __init__(self, ctx: ReplayContext) -> None:
        super().__init__()
        self.ctx = ctx

    def capabilities(self) -> list[str]:
        return [
            "wave_height", "wave_period", "wind_speed", "wind_direction", "wind_gusts", "visibility",
            "precipitation", "weather_code", "sea_surface_temperature", "chlorophyll", "sea_level",
        ]

    async def fetch(self, query: PointQuery) -> RawPayload:
        as_of = self.ctx.as_of()
        rows = [self._row(query.lat, query.lon, t, as_of, query.variables) for t in hours_between(query.start, query.end)]
        return RawPayload(
            adapter=self.name,
            fetched_at=datetime.now(UTC),
            request={"lat": query.lat, "lon": query.lon, "as_of": as_of.isoformat(), "event": self.ctx.event.id},
            payload={"lat": query.lat, "lon": query.lon, "rows": rows, "as_of": as_of},
            reference=REFERENCE,
        )

    def normalize(self, raw: RawPayload) -> list[MarineObservation]:
        lat, lon, as_of = raw.payload["lat"], raw.payload["lon"], raw.payload["as_of"]
        out: list[MarineObservation] = []
        for row in raw.payload["rows"]:
            t: datetime = row["time"]
            for var, cell in row["values"].items():
                out.append(self._observation(var, cell, lat, lon, t, as_of))
        return out

    def values_at(self, lat: float, lon: float, t: datetime, variables: tuple[str, ...]) -> dict[str, MarineObservation]:
        as_of = self.ctx.as_of()
        row = self._row(lat, lon, ensure_utc(t), as_of, variables)
        return {var: self._observation(var, cell, lat, lon, row["time"], as_of) for var, cell in row["values"].items()}

    async def observe_many(self, points, start, end):
        return {p: await self.observe(PointQuery(p[0], p[1], start, end, ROUTE_VARIABLES)) for p in points}

    # ---- internals
    def _row(self, lat: float, lon: float, t: datetime, as_of: datetime, variables) -> dict:
        arc = self.ctx.archive
        want = set(variables or self.capabilities())
        values: dict[str, dict] = {}

        def put(var: str, value: float | None, source: str, s: Sample | None, resolution: str, dtype: DataType, product: str) -> None:
            if var in want:
                values[var] = {"value": value, "source": source, "sample": s, "resolution": resolution, "dtype": dtype, "product": product}

        def run_type(s: Sample) -> DataType:
            return DataType.FORECAST if t > as_of else DataType.HISTORICAL

        def run_product(kind: str, s: Sample) -> str:
            return f"{kind} run {s.cycle:%Y-%m-%d %HZ} (on NOAA's server {s.published:%d %b %H:%M} UTC)"

        if want & {"wave_height", "wave_period"}:
            for var, fname in (("wave_height", "htsgw"), ("wave_period", "perpw")):
                s = arc.wave.sample(fname, lat, lon, t, as_of, sea=True)
                if s is not None:
                    put(var, s.value, WAVE_SOURCE, s, "0.25° grid (~28 km), nearest sea cell", run_type(s), run_product("GFS-Wave", s))
        if want & {"wind_speed", "wind_direction", "wind_gusts", "visibility", "precipitation", "weather_code"}:
            su = arc.atmos.sample("ugrd10", lat, lon, t, as_of)
            sv = arc.atmos.sample("vgrd10", lat, lon, t, as_of)
            if su is not None and sv is not None and su.value is not None and sv.value is not None:
                speed, direction = wind_from_uv(su.value, sv.value)
                put("wind_speed", speed, GFS_SOURCE, su, "0.5° grid (~55 km)", run_type(su), run_product("GFS", su))
                put("wind_direction", direction, GFS_SOURCE, su, "0.5° grid (~55 km)", run_type(su), run_product("GFS", su))
            for var, fname, factor in (("wind_gusts", "gust", 3.6), ("visibility", "vis", 1.0), ("precipitation", "prate", 1.0)):
                s = arc.atmos.sample(fname, lat, lon, t, as_of)
                if s is not None and (s.value is not None or var != "precipitation"):
                    put(var, None if s.value is None else s.value * factor, GFS_SOURCE, s, "0.5° grid (~55 km)", run_type(s), run_product("GFS", s))
            if "weather_code" in want:
                li = arc.atmos.sample("lftx", lat, lon, t, as_of)
                cp = arc.atmos.sample("cprat", lat, lon, t, as_of)
                pr = arc.atmos.sample("prate", lat, lon, t, as_of)
                if li is not None:
                    code = derive_weather_code(li.value, cp.value if cp else None, pr.value if pr else None)
                    put("weather_code", code, GFS_SOURCE + " — derived", li, "0.5° grid (~55 km)", DataType.DERIVED,
                        f"derived from GFS run {li.cycle:%Y-%m-%d %HZ}: lifted index + convective rain (thunderstorm), rain rate (AMS classes)")
        if "sea_level" in want:
            tide = arc.tide_at(lat, lon, t)
            if tide is not None:
                harmonic = (arc.tide_method or "").startswith("harmonic")
                put("sea_level", tide[0], TIDE_SOURCE + (" — harmonic prediction" if harmonic else ""), None,
                    f"harbour point: {tide[1]}", DataType.DERIVED if harmonic else DataType.FORECAST,
                    f"predicted sea level at {tide[1]}: {arc.tide_method} (tides are known in advance)")
        isro = arc.isro_sst_at(lat, lon, as_of) if "sea_surface_temperature" in want else None
        if isro is not None:  # ISRO's own satellite first; NOAA's blended analysis where clouds hid the sea
            put("sea_surface_temperature", isro[0], ISRO_SST_SOURCE, None, "0.05° daily (regridded from 4 km pixels)",
                DataType.OBSERVATION, f"MOSDAC {isro[2]} (daily composite for {isro[1]:%d %b %Y})")
        elif "sea_surface_temperature" in want:
            got = arc.sst_at(lat, lon, as_of)
            if got is not None:
                put("sea_surface_temperature", got[0], SST_SOURCE, None, "0.25° daily", DataType.OBSERVATION,
                    f"OISST daily analysis for {got[2]:%d %b %Y} (anomaly vs 1971–2000: {'n/a' if got[1] is None else f'{got[1]:+.1f} °C'})")
        if "chlorophyll" in want:
            chl = arc.chl_at(lat, lon, as_of)
            if chl is not None:
                days = arc.chl_grid(as_of)[3]
                put("chlorophyll", chl, CHL_SOURCE, None, "0.045° (~5 km) multi-day composite", DataType.OBSERVATION,
                    f"VIIRS composite {days[0]:%d}–{days[-1]:%d %b %Y} (geometric mean of cloud-free passes)")
        return {"time": t, "values": values}

    def _observation(self, var: str, cell: dict, lat: float, lon: float, t: datetime, as_of: datetime) -> MarineObservation:
        from ..variables import VARIABLES

        value, flag = validate(var, cell["value"])
        s: Sample | None = cell["sample"]
        tag = f"{s.cycle:%Y%m%d%H}" if s else "obs"
        spec = VARIABLES[var]
        return MarineObservation(
            id=f"hist:{var}:{lat:.3f},{lon:.3f}:{t:%Y%m%dT%H}:{tag}",
            source=cell["source"],
            source_product=cell["product"],
            lat=lat,
            lon=lon,
            retrieved_at=s.published if s else as_of,
            valid_time=t,
            data_type=cell["dtype"],
            variable=var,
            value=value,
            unit=spec.unit,
            quality_flag=flag,
            spatial_resolution=cell["resolution"],
            processing_version=PROCESSING_VERSION,
            reference=REFERENCE,
        )


class HistoricalCAPAdapter(AdvisoryAdapter):
    """IMD CAP alerts as issued. Only alerts already sent at the replay moment are visible."""

    name = "imd-cap-archive"
    mode = "historical"

    def __init__(self, ctx: ReplayContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._cache: dict[str, list[Advisory]] = {}

    def all_alerts(self) -> list[Advisory]:
        eid = self.ctx.event.id
        if eid not in self._cache:
            folder = self.ctx.archive.dir / "cap"
            out = []
            for f in sorted(folder.glob("*.xml")) if folder.exists() else []:
                adv = parse_cap(f.read_text(encoding="utf-8", errors="replace"), CAP_ARCHIVE + f.name, datetime.now(UTC))
                if adv is None:
                    continue
                adv = adv.model_copy(update={"retrieved_at": adv.sent or adv.onset or datetime.now(UTC),
                                             "source": "IMD (CAP, archived by WMO Alert Hub)"})
                out.append(adv)
            self._cache[eid] = out
        return self._cache[eid]

    async def fetch_advisories(self, now: datetime) -> list[Advisory]:
        started = time.perf_counter()
        now = ensure_utc(now)
        out = [a for a in self.all_alerts() if (a.sent or a.onset or now) <= now and (a.expires is None or a.expires > now)]
        self._ok(started)
        return out


# IMD classification of cyclonic disturbances by maximum sustained surface wind (knots)
IMD_SCALE = (
    (120, "Super Cyclonic Storm", "Extreme"),
    (90, "Extremely Severe Cyclonic Storm", "Extreme"),
    (64, "Very Severe Cyclonic Storm", "Extreme"),
    (48, "Severe Cyclonic Storm", "Severe"),
    (34, "Cyclonic Storm", "Severe"),
    (28, "Deep Depression", "Moderate"),
    (17, "Depression", "Moderate"),
)
IMD_SCALE_REFERENCE = "IMD classification of cyclonic disturbances (maximum sustained surface wind)"


def classify_imd(max_wind_kmh: float) -> tuple[str, str] | None:
    kt = max_wind_kmh / 1.852
    for threshold, name, severity in IMD_SCALE:
        if kt >= threshold:
            return name, severity
    return None


def find_cyclone(archive: EventArchive, valid: datetime, as_of: datetime) -> dict | None:
    """Cyclone centre in the run available at as_of: the lowest sea-level pressure over sea that is
    a closed low (≥ 4 hPa below its surroundings), with its maximum wind within 250 km."""
    atm = archive.atmos
    got = atm.grid("prmsl", valid, as_of)
    if got is None:
        return None
    p, cycle, lead = got
    u = atm.grid("ugrd10", valid, as_of)[0]
    v = atm.grid("vgrd10", valid, as_of)[0]
    lat, lon = atm.lat, atm.lon
    sea = np.array([[not is_land(float(a), float(b)) for b in lon] for a in lat])
    masked = np.where(sea, p, np.nan)
    if np.all(np.isnan(masked)):
        return None
    i, j = np.unravel_index(np.nanargmin(masked), masked.shape)
    la, lo = float(lat[i]), float(lon[j])
    yy, xx = np.meshgrid(lat, lon, indexing="ij")
    dist = 111.2 * np.hypot(yy - la, (xx - lo) * math.cos(math.radians(la)))
    ring = p[(dist > 400) & (dist < 700)]
    if ring.size == 0 or np.nanmean(ring) - p[i, j] < 400:  # Pa
        return None
    speed = np.hypot(u, v) * 3.6
    vmax = float(np.nanmax(np.where(dist <= 250, speed, np.nan)))
    cls = classify_imd(vmax)
    if cls is None:
        return None
    return {"lat": la, "lon": lo, "pressure_hpa": round(float(p[i, j]) / 100), "max_wind_kmh": round(vmax),
            "category": cls[0], "severity": cls[1], "valid": valid, "cycle": cycle, "lead_h": lead}


def cyclone_track(archive: EventArchive, as_of: datetime, hours: int = 48, step: int = 6) -> list[dict]:
    as_of = ensure_utc(as_of)
    start = as_of.replace(minute=0, second=0, microsecond=0)
    out = []
    for h in range(0, hours + 1, step):
        c = find_cyclone(archive, start + timedelta(hours=h), as_of)
        if c is not None:
            out.append(c)
    return out


class CycloneWatchAdapter(AdvisoryAdapter):
    name = "orca-cyclone-watch"
    mode = "historical"

    def __init__(self, ctx: ReplayContext) -> None:
        super().__init__()
        self.ctx = ctx

    async def fetch_advisories(self, now: datetime) -> list[Advisory]:
        started = time.perf_counter()
        track = cyclone_track(self.ctx.archive, now, hours=36, step=6)
        self._ok(started)
        if not track:
            return []
        c0 = track[0]
        worst = max(track, key=lambda c: c["max_wind_kmh"])
        radius_km = 300.0
        circle = [
            (c0["lat"] + radius_km / 111.2 * math.cos(math.radians(a)),
             c0["lon"] + radius_km / (111.2 * math.cos(math.radians(c0["lat"]))) * math.sin(math.radians(a)))
            for a in range(0, 360, 20)
        ]
        heading = ""
        if len(track) > 1:
            dy, dx = track[-1]["lat"] - c0["lat"], (track[-1]["lon"] - c0["lon"]) * math.cos(math.radians(c0["lat"]))
            heading = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int(((math.degrees(math.atan2(dx, dy)) + 360) % 360 + 22.5) // 45) % 8]
        return [
            Advisory(
                id=f"cyclone-watch:{c0['cycle']:%Y%m%d%H}",
                source="ORCA cyclone watch (derived from NOAA GFS — not an IMD warning)",
                data_type=DataType.DERIVED,
                event="Cyclone watch (model-derived)",
                headline=f"{c0['category']} near {c0['lat']:.1f}°N {c0['lon']:.1f}°E, about {c0['max_wind_kmh']} km/h"
                + (f", moving {heading}" if heading else ""),
                description=(
                    f"GFS run {c0['cycle']:%Y-%m-%d %HZ}: centre pressure about {c0['pressure_hpa']} hPa. "
                    f"Strongest in the next 36 h: {worst['category']} ({worst['max_wind_kmh']} km/h). "
                    "Model-estimated winds at 0.5° understate the peak. Follow IMD bulletins."
                ),
                severity=c0["severity"],
                certainty="Possible",
                onset=now,
                expires=now + timedelta(hours=12),
                sent=c0["cycle"],
                area_desc=f"within {radius_km:.0f} km of the model cyclone centre",
                polygons=[circle],
                reference=IMD_SCALE_REFERENCE,
                retrieved_at=now,
            )
        ]
