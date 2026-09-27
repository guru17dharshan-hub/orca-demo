"""Read access to one event's archived data, with the no-look-ahead rule built in.

For any 'as of' moment ORCA may only use what had been published by then:
  - GFS / GFS-Wave: the latest run whose files were on NOAA's server (S3 upload
    time) at the as-of moment. Past hours come from the run that covered them.
  - OISST: the latest day released by then (one-day release lag).
  - Chlorophyll: the multi-day composite, once its last day has passed.
  - ISRO INSAT-3DR/3D daily SST (MOSDAC L3B): a day's product once its 24 h window has closed plus a margin.
  - IMD CAP warnings: only those already sent.
  - Tides: astronomical predictions, known in advance, so any hour may be used.

Every grid answers only inside its own area: a point outside an event's archive gets no value (and the
risk engine says it cannot confirm), never the nearest edge cell's value from somewhere else.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import cached_property
from pathlib import Path

import numpy as np

from ..timeutil import UTC, ensure_utc
from .events import HistoricalEvent
from .store import FIELDS, decode

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "historical"
PUBLISH_FALLBACK_H = 4.0  # if the upload time is unknown, assume a run is out 4 h after its start
MAX_LEAD_H = 48
SST_RELEASE_LAG = timedelta(days=1, hours=12)
# MOSDAC L3B daily SST for day D spans D 00:15 -> D+1 00:15 UTC; its release time is not recorded, so ORCA
# assumes it is on the portal 6 h after the window closes (conservative).
ISRO_SST_RELEASE_LAG = timedelta(days=1, hours=6, minutes=15)
TIDE_PORT_RADIUS_KM = 60.0  # tides are kept at the harbours; farther offshore ORCA does not interpolate them


def inside(lats: np.ndarray, lons: np.ndarray, lat: float, lon: float) -> bool:
    """Is the point within the grid, allowing half a cell beyond the outer centres?"""
    dlat = abs(float(lats[1] - lats[0])) if len(lats) > 1 else 0.0
    dlon = abs(float(lons[1] - lons[0])) if len(lons) > 1 else 0.0
    return (min(lats) - dlat / 2 <= lat <= max(lats) + dlat / 2) and (min(lons) - dlon / 2 <= lon <= max(lons) + dlon / 2)


@dataclass(frozen=True)
class Sample:
    value: float | None
    cycle: datetime
    lead_h: float
    published: datetime
    grid_lat: float
    grid_lon: float


class GridStack:
    """GFS fields for all runs: arrays [run, lead, lat, lon]."""

    def __init__(self, path: Path) -> None:
        self.npz = np.load(path)
        self.lat = self.npz["lat"].astype(float)
        self.lon = self.npz["lon"].astype(float)
        self.cycles = [datetime.fromisoformat(str(c)) for c in self.npz["cycles"]]
        self.published = [
            datetime.fromisoformat(str(p)) if str(p) else c + timedelta(hours=PUBLISH_FALLBACK_H)
            for p, c in zip(self.npz["published"], self.cycles)
        ]
        self.leads = [int(h) for h in self.npz["leads"]]
        self.step = self.leads[1] - self.leads[0]
        self._fields: dict[str, np.ndarray] = {}
        self._nearest_valid: np.ndarray | None = None

    def field(self, name: str) -> np.ndarray:
        if name not in self._fields:
            self._fields[name] = decode(self.npz[name], FIELDS[name]).astype(np.float32)
        return self._fields[name]

    def has(self, name: str) -> bool:
        return name in self.npz.files

    def covers(self, lat: float, lon: float) -> bool:
        return inside(self.lat, self.lon, lat, lon)

    def run_for(self, as_of: datetime, valid: datetime) -> int | None:
        """Latest run published by as_of that starts at or before the valid time."""
        best = None
        for i, (c, p) in enumerate(zip(self.cycles, self.published)):
            if p <= as_of and c <= valid:
                best = i
        return best

    def index(self, lat: float, lon: float, sea_field: str | None = None) -> tuple[int, int]:
        i = int(np.clip(round((lat - self.lat[0]) / (self.lat[1] - self.lat[0])), 0, len(self.lat) - 1))
        j = int(np.clip(round((lon - self.lon[0]) / (self.lon[1] - self.lon[0])), 0, len(self.lon) - 1))
        if sea_field is None:
            return i, j
        nv = self.nearest_valid(sea_field)
        return int(nv[i, j, 0]), int(nv[i, j, 1])

    def nearest_valid(self, name: str) -> np.ndarray:
        """For every cell, the nearest cell with data (wave models have no values over land/coast)."""
        if self._nearest_valid is None:
            valid = ~np.isnan(self.field(name)[0, 0])
            vi, vj = np.nonzero(valid)
            out = np.zeros(valid.shape + (2,), dtype=np.int32)
            for i in range(valid.shape[0]):
                for j in range(valid.shape[1]):
                    if valid[i, j]:
                        out[i, j] = (i, j)
                    else:
                        k = int(np.argmin((vi - i) ** 2 + (vj - j) ** 2))
                        out[i, j] = (vi[k], vj[k])
            self._nearest_valid = out
        return self._nearest_valid

    def sample(self, name: str, lat: float, lon: float, valid: datetime, as_of: datetime, sea: bool = False) -> Sample | None:
        if not self.covers(lat, lon):
            return None
        run = self.run_for(as_of, valid)
        if run is None:
            return None
        lead = (valid - self.cycles[run]).total_seconds() / 3600
        if lead > MAX_LEAD_H:
            return None
        i, j = self.index(lat, lon, name if sea else None)
        k = min(int(lead // self.step), len(self.leads) - 2)
        w = (lead - self.leads[k]) / self.step
        arr = self.field(name)
        a, b = arr[run, k, i, j], arr[run, k + 1, i, j]
        if np.isnan(a) and np.isnan(b):
            value = None
        elif np.isnan(a) or np.isnan(b):
            value = float(b if np.isnan(a) else a)
        else:
            value = float((1 - w) * a + w * b)
        return Sample(value, self.cycles[run], lead, self.published[run], float(self.lat[i]), float(self.lon[j]))

    def grid(self, name: str, valid: datetime, as_of: datetime) -> tuple[np.ndarray, datetime, float] | None:
        """Whole-region field at one valid time (for map layers and cyclone tracking)."""
        run = self.run_for(as_of, valid)
        if run is None:
            return None
        lead = (valid - self.cycles[run]).total_seconds() / 3600
        if lead > MAX_LEAD_H:
            return None
        k = min(int(lead // self.step), len(self.leads) - 2)
        w = (lead - self.leads[k]) / self.step
        arr = self.field(name)
        g = (1 - w) * arr[run, k] + w * arr[run, k + 1]
        return g, self.cycles[run], lead


class EventArchive:
    def __init__(self, event: HistoricalEvent, root: Path = DATA_DIR) -> None:
        self.event = event
        self.dir = root / event.id

    @property
    def available(self) -> bool:
        return (self.dir / "gfs_wave.npz").exists() and (self.dir / "gfs_atmos.npz").exists()

    def covers(self, lat: float, lon: float) -> bool:
        """Is the point inside this event's archived area (the wave grid, the smallest one)?"""
        return self.available and self.wave.covers(lat, lon)

    @cached_property
    def wave(self) -> GridStack:
        return GridStack(self.dir / "gfs_wave.npz")

    @cached_property
    def atmos(self) -> GridStack:
        return GridStack(self.dir / "gfs_atmos.npz")

    @cached_property
    def meta(self) -> dict:
        p = self.dir / "meta.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    # ---- sea-surface temperature (NOAA OISST v2.1)
    @cached_property
    def _sst(self) -> dict | None:
        p = self.dir / "sst.npz"
        if not p.exists():
            return None
        z = np.load(p)
        return {
            "lat": z["lat"].astype(float),
            "lon": z["lon"].astype(float),
            "days": [date.fromisoformat(str(d)) for d in z["days"]],
            "sst": np.where(z["sst"] == -32768, np.nan, z["sst"] / 100.0),
            "anom": np.where(z["anom"] == -32768, np.nan, z["anom"] / 100.0),
        }

    def sst_day_index(self, as_of: datetime) -> int | None:
        s = self._sst
        if s is None:
            return None
        released = [i for i, d in enumerate(s["days"]) if datetime(d.year, d.month, d.day, tzinfo=UTC) + SST_RELEASE_LAG <= as_of]
        return released[-1] if released else None

    def sst_at(self, lat: float, lon: float, as_of: datetime) -> tuple[float | None, float | None, date] | None:
        s, k = self._sst, self.sst_day_index(as_of)
        if s is None or k is None or not inside(s["lat"], s["lon"], lat, lon):
            return None
        i, j = self._nearest_sea(s["sst"][k], s["lat"], s["lon"], lat, lon)
        if i is None:
            return None
        v, a = s["sst"][k, i, j], s["anom"][k, i, j]
        return (None if np.isnan(v) else float(v)), (None if np.isnan(a) else float(a)), s["days"][k]

    def sst_series(self, lat: float, lon: float, as_of: datetime) -> list[tuple[date, float, float]]:
        s, k = self._sst, self.sst_day_index(as_of)
        if s is None or k is None or not inside(s["lat"], s["lon"], lat, lon):
            return []
        i, j = self._nearest_sea(s["sst"][k], s["lat"], s["lon"], lat, lon)
        if i is None:
            return []
        return [
            (s["days"][d], float(s["sst"][d, i, j]), float(s["anom"][d, i, j]))
            for d in range(k + 1)
            if not np.isnan(s["sst"][d, i, j])
        ]

    def sst_grid(self, as_of: datetime) -> tuple[np.ndarray, np.ndarray, np.ndarray, date] | None:
        s, k = self._sst, self.sst_day_index(as_of)
        if s is None or k is None:
            return None
        return s["lat"], s["lon"], s["sst"][k], s["days"][k]

    # ---- chlorophyll-a composite (NOAA-20 VIIRS)
    @cached_property
    def _chl(self) -> dict | None:
        p = self.dir / "chl.npz"
        if not p.exists():
            return None
        z = np.load(p)
        log = np.where(z["log10_chl"] == -32768, np.nan, z["log10_chl"] / 1000.0)
        return {
            "lat": z["lat"].astype(float),
            "lon": z["lon"].astype(float),
            "chl": np.power(10.0, log),
            "days": [date.fromisoformat(str(d)) for d in z["days"]],
        }

    def chl_available(self, as_of: datetime) -> bool:
        c = self._chl
        if c is None:
            return False
        last = c["days"][-1]
        return datetime(last.year, last.month, last.day, tzinfo=UTC) + timedelta(days=1) <= as_of

    def chl_at(self, lat: float, lon: float, as_of: datetime, radius_cells: int = 2) -> float | None:
        from ..geo.land import is_land

        if not self.chl_available(as_of) or is_land(lat, lon):  # inland lakes and reservoirs also show chlorophyll
            return None
        c = self._chl
        if not inside(c["lat"], c["lon"], lat, lon):
            return None
        i = int(np.clip(round((lat - c["lat"][0]) / (c["lat"][1] - c["lat"][0])), 0, len(c["lat"]) - 1))
        j = int(np.clip(round((lon - c["lon"][0]) / (c["lon"][1] - c["lon"][0])), 0, len(c["lon"]) - 1))
        win = c["chl"][max(0, i - radius_cells) : i + radius_cells + 1, max(0, j - radius_cells) : j + radius_cells + 1]
        if np.all(np.isnan(win)):
            return None
        return float(np.nanmedian(win))

    def chl_grid(self, as_of: datetime) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[date]] | None:
        if not self.chl_available(as_of):
            return None
        c = self._chl
        return c["lat"], c["lon"], c["chl"], c["days"]

    # ---- sea-surface temperature from ISRO (INSAT-3DR / INSAT-3D Imager, MOSDAC L3B daily)
    @cached_property
    def _isro_sst(self) -> dict | None:
        p = self.dir / "isro_sst.npz"
        if not p.exists():
            return None
        z = np.load(p)
        return {
            "lat": z["lat"].astype(float),
            "lon": z["lon"].astype(float),
            "days": [date.fromisoformat(str(d)) for d in z["days"]],
            "sst": np.where(z["sst"] == -32768, np.nan, z["sst"] / 100.0),
            "files": [str(f) for f in z["files"]],
        }

    def isro_sst_at(self, lat: float, lon: float, as_of: datetime) -> tuple[float, date, str] | None:
        """Latest released ISRO daily SST at the point: (deg C, day, MOSDAC file name). Cloud gaps give None."""
        s = self._isro_sst
        if s is None or not inside(s["lat"], s["lon"], lat, lon):
            return None
        released = [k for k, d in enumerate(s["days"]) if datetime(d.year, d.month, d.day, tzinfo=UTC) + ISRO_SST_RELEASE_LAG <= as_of]
        for k in reversed(released[-2:]):  # the latest product, else the day before if clouds hid the point
            i, j = self._nearest_sea(s["sst"][k], s["lat"], s["lon"], lat, lon)
            if i is not None:
                return float(s["sst"][k, i, j]), s["days"][k], s["files"][k]
        return None

    # ---- tides (predicted sea level at the harbours)
    @cached_property
    def _tide(self) -> dict | None:
        p = self.dir / "tide.npz"
        if not p.exists():
            return None
        z = np.load(p)
        return {
            "names": [str(n) for n in z["names"]],
            "lat": z["lat"].astype(float),
            "lon": z["lon"].astype(float),
            "times": [datetime.fromisoformat(str(t)) for t in z["times"]],
            "level": np.where(z["level_mm"] == -32768, np.nan, z["level_mm"] / 1000.0),
            "method": str(z["method"]) if "method" in z.files else "Open-Meteo sea_level_height_msl",
        }

    @property
    def tide_method(self) -> str | None:
        return self._tide["method"] if self._tide else None

    def tide_at(self, lat: float, lon: float, t: datetime) -> tuple[float, str] | None:
        """Predicted sea level (m, relative to mean sea level) at the nearest harbour within 60 km."""
        from ..geo.geometry import haversine_km

        s = self._tide
        if s is None:
            return None
        dists = [haversine_km(lat, lon, a, b) for a, b in zip(s["lat"], s["lon"])]
        k = int(np.argmin(dists))
        if dists[k] > TIDE_PORT_RADIUS_KM:
            return None
        times, t = s["times"], ensure_utc(t)
        if not times or t < times[0] or t > times[-1]:
            return None
        nxt = next((n for n, x in enumerate(times) if x > t), len(times) - 1)
        i = max(0, min(len(times) - 2, nxt - 1))
        span = (times[i + 1] - times[i]).total_seconds()
        w = 0.0 if span <= 0 else min(1.0, (t - times[i]).total_seconds() / span)
        a, b = s["level"][i, k], s["level"][i + 1, k]
        if np.isnan(a) or np.isnan(b):
            return None
        return float((1 - w) * a + w * b), s["names"][k]

    @staticmethod
    def _nearest_sea(field: np.ndarray, lats: np.ndarray, lons: np.ndarray, lat: float, lon: float) -> tuple[int | None, int | None]:
        i, j = int(np.argmin(np.abs(lats - lat))), int(np.argmin(np.abs(lons - lon)))
        for r in range(0, 5):
            win = field[max(0, i - r) : i + r + 1, max(0, j - r) : j + r + 1]
            if np.any(~np.isnan(win)):
                ii, jj = np.nonzero(~np.isnan(win))
                d = (ii + max(0, i - r) - i) ** 2 + (jj + max(0, j - r) - j) ** 2
                k = int(np.argmin(d))
                return int(ii[k] + max(0, i - r)), int(jj[k] + max(0, j - r))
        return None, None


def wind_from_uv(u: float, v: float) -> tuple[float, float]:
    """(speed km/h, direction the wind blows FROM in degrees)."""
    speed = math.hypot(u, v) * 3.6
    direction = (math.degrees(math.atan2(-u, -v)) + 360) % 360
    return speed, direction


def as_utc(dt: datetime) -> datetime:
    return ensure_utc(dt)
