"""Predicted tides at the harbours for ORCA's historical replay (public, no login).

Tides are astronomical: they are predicted years ahead, so using them in a replay does not look ahead.
Source: Open-Meteo Marine API, hourly `sea_level_height_msl` (sea level relative to mean sea level,
including tides, from its ocean model) at each harbour's sea point inside the event's region.
Saved as backend/data/historical/<event>/tide.npz (names, lat, lon, times, level_mm int16).

    python scripts/historical/fetch_tides.py                 # every event
    python scripts/historical/fetch_tides.py michaung-2023
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orca.geo.ports import PORTS, snap_to_sea  # noqa: E402
from orca.historical.events import EVENTS, HistoricalEvent  # noqa: E402
from orca.timeutil import UTC  # noqa: E402

OUT = ROOT / "data" / "historical"
API = "https://marine-api.open-meteo.com/v1/marine"
FILL = -32768
MARGIN = 0.5  # degrees: harbours just outside the region still serve boats inside it


# Harmonic fallback for dates before Open-Meteo's sea-level record: fit the main constituents on a recent
# 90-day series at the same point, then predict. Periods in hours (Schureman). 90 days separates these six
# (Rayleigh criterion); K2/P1 need half a year and are left out. Nodal corrections are ignored (a few %).
CONSTITUENTS_H = {"M2": 12.4206012, "S2": 12.0, "N2": 12.65834751, "K1": 23.93447213, "O1": 25.81933871, "Q1": 26.868350}
FIT_PERIOD = ("2023-01-01", "2023-03-31")


def _design(hours: np.ndarray) -> np.ndarray:
    cols = [np.ones_like(hours)]
    for period in CONSTITUENTS_H.values():
        w = 2 * np.pi / period
        cols += [np.cos(w * hours), np.sin(w * hours)]
    return np.stack(cols, axis=1)


def harmonic_predict(fit_times: list[datetime], fit_levels: np.ndarray, times: list[datetime]) -> np.ndarray:
    """Least-squares tidal fit on one series, evaluated at other times (same epoch for both)."""
    epoch = datetime(2000, 1, 1, tzinfo=UTC)
    h_fit = np.array([(t - epoch).total_seconds() / 3600 for t in fit_times])
    ok = np.isfinite(fit_levels)
    coef, *_ = np.linalg.lstsq(_design(h_fit[ok]), fit_levels[ok], rcond=None)
    h = np.array([(t - epoch).total_seconds() / 3600 for t in times])
    return _design(h) @ coef


def _series(points, start: str, end: str) -> tuple[list[datetime], np.ndarray]:
    r = httpx.get(API, timeout=180, params={
        "latitude": ",".join(f"{a:.4f}" for a, _ in points), "longitude": ",".join(f"{b:.4f}" for _, b in points),
        "hourly": "sea_level_height_msl", "start_date": start, "end_date": end, "timezone": "GMT",
    })
    r.raise_for_status()
    body = r.json()
    body = body if isinstance(body, list) else [body]
    times = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in body[0]["hourly"]["time"]]
    levels = np.array([[np.nan if v is None else v for v in b["hourly"]["sea_level_height_msl"]] for b in body]).T
    return times, levels


def fetch_event(event: HistoricalEvent) -> dict:
    lat_min, lat_max, lon_min, lon_max = event.region
    ports = [p for p in PORTS if lat_min - MARGIN <= p.lat <= lat_max + MARGIN and lon_min - MARGIN <= p.lon <= lon_max + MARGIN]
    if not ports:
        return {"harbours": 0}
    points = [snap_to_sea(p.lat, p.lon) for p in ports]
    start = (event.replay_start - timedelta(days=1)).date()
    end = (event.replay_end + timedelta(days=2)).date()
    times, series = _series(points, start.isoformat(), end.isoformat())
    method = "Open-Meteo sea_level_height_msl"
    if np.isfinite(series).mean() < 0.5:  # before the record starts: harmonic prediction from a 2023 fit
        fit_times, fit = _series(points, *FIT_PERIOD)
        series = np.stack([harmonic_predict(fit_times, fit[:, k], times) for k in range(len(points))], axis=1)
        method = f"harmonic prediction ({', '.join(CONSTITUENTS_H)}) fitted to Open-Meteo sea level {FIT_PERIOD[0]}..{FIT_PERIOD[1]}"
    levels = np.where(np.isfinite(series), np.round(series * 1000), FILL).astype(np.int16)
    np.savez_compressed(
        OUT / event.id / "tide.npz",
        names=np.array([p.name for p in ports]),
        lat=np.array([a for a, _ in points], np.float32),
        lon=np.array([b for _, b in points], np.float32),
        times=np.array([t.isoformat() for t in times]),
        level_mm=levels,
        method=np.array(method),
    )
    valid = levels != FILL
    return {
        "harbours": len(ports),
        "hours": len(times),
        "coverage_pct": round(float(valid.mean() * 100), 1),
        "source": "Open-Meteo Marine API, hourly sea_level_height_msl (tide + mean sea level, model prediction)",
        "method": method,
        "retrieved_at": datetime.now(UTC).isoformat(),
    }


if __name__ == "__main__":
    for eid in sys.argv[1:] or list(EVENTS):
        result = fetch_event(EVENTS[eid])
        meta_path = OUT / eid / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"event": eid, "products": {}}
        meta["products"]["tide"] = result
        meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
        print(f"[{eid}] tides: {result}", flush=True)
