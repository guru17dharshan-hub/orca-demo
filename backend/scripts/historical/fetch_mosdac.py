"""Download ISRO's own satellite sea-surface temperature for ORCA's historical replay.

Source: MOSDAC (Meteorological & Oceanographic Satellite Data Archival Centre, SAC-ISRO), the same
download API as MOSDAC's official mdapi.py client:
    search   GET  https://mosdac.gov.in/apios/datasets.json   (public, no login)
    token    POST https://mosdac.gov.in/download_api/gettoken  {"username", "password"}
    download GET  https://mosdac.gov.in/download_api/download?id=<record id>   (Bearer token)

Products: INSAT-3DR Imager L3B daily SST (3RIMG_L3B_SST_DLY), falling back to INSAT-3D (3DIMG_L3B_SST_DLY).
File layout per the INSAT-3D Data Products Format Document: HDF5 with 2-D 'Latitude'/'Longitude' datasets
(int16, scale_factor/add_offset/_FillValue) and 'SST' (float32, _FillValue); optional 'SST_QFLAGS'
(3 = high confidence). Pixels are averaged onto a regular 0.05° grid over the event's region and saved as
backend/data/historical/<event>/isro_sst.npz, so the app itself never needs an HDF5 library.

    pip install -r requirements-data.txt                  # h5py
    set MOSDAC_USERNAME / MOSDAC_PASSWORD                 # free account: https://mosdac.gov.in/signup/
    python scripts/historical/fetch_mosdac.py             # every event
    python scripts/historical/fetch_mosdac.py michaung-2023
    python scripts/historical/fetch_mosdac.py --inspect some_file.h5   # print a file's structure
"""

from __future__ import annotations

import io
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orca.historical.events import EVENTS, HistoricalEvent  # noqa: E402
from orca.timeutil import UTC  # noqa: E402

OUT = ROOT / "data" / "historical"
SEARCH = "https://mosdac.gov.in/apios/datasets.json"
TOKEN = "https://mosdac.gov.in/download_api/gettoken"
DOWNLOAD = "https://mosdac.gov.in/download_api/download"
LOGOUT = "https://mosdac.gov.in/download_api/logout"
DATASETS = ("3RIMG_L3B_SST_DLY", "3DIMG_L3B_SST_DLY")  # INSAT-3DR first, INSAT-3D as fallback
GRID = 0.05
FILL = -32768
HIGH_CONFIDENCE = 3
HEADERS = {"User-Agent": "ORCA-historical-replay/1 (+https://github.com/sam-creates-projects/orca-demo)"}


# ------------------------------------------------------------------------------ search (public)
def search(dataset: str, start: date, end: date, bbox: tuple[float, float, float, float]) -> list[dict]:
    """All granules of a dataset between two dates (MOSDAC OpenSearch, no login)."""
    lat_min, lat_max, lon_min, lon_max = bbox
    out, index = [], 1
    while True:
        r = httpx.get(SEARCH, headers=HEADERS, timeout=60, params={
            "datasetId": dataset, "startTime": start.isoformat(), "endTime": end.isoformat(),
            "boundingBox": f"{lon_min},{lat_min},{lon_max},{lat_max}", "startIndex": index, "count": 100,
        })
        r.raise_for_status()
        entries = r.json().get("entries") or []
        out += entries
        if len(entries) < 100:
            return out
        index += 100


def granule_day(identifier: str) -> date:
    """3RIMG_02DEC2023_0015_L3B_SST_DLY_V02R00.h5 -> 2023-12-02."""
    return datetime.strptime(identifier.split("_")[1], "%d%b%Y").date()


def pick_daily(entries: list[dict]) -> dict[date, dict]:
    """One granule per day: the highest product version (V02R00 over V01R00)."""
    best: dict[date, dict] = {}
    for e in entries:
        d = granule_day(e["identifier"])
        if d not in best or e["identifier"] > best[d]["identifier"]:
            best[d] = e
    return best


# ------------------------------------------------------------------------------ HDF5 -> regular grid
def _scaled(ds) -> np.ndarray:
    """Read a dataset applying _FillValue, scale_factor and add_offset (CF conventions)."""
    a = np.asarray(ds[()]).astype(np.float64)
    attrs = {k: (v[0] if hasattr(v, "__len__") and not isinstance(v, (str, bytes)) and len(v) == 1 else v) for k, v in ds.attrs.items()}
    fill = attrs.get("_FillValue")
    mask = np.zeros(a.shape, bool) if fill is None else (np.asarray(ds[()]) == fill)
    a = a * float(attrs.get("scale_factor", 1.0)) + float(attrs.get("add_offset", 0.0))
    a[mask] = np.nan
    return np.squeeze(a)


def _find(h5, *names: str):
    lower = {k.lower(): k for k in h5.keys()}
    for n in names:
        if n.lower() in lower:
            return h5[lower[n.lower()]]
    raise KeyError(f"none of {names} in file; datasets are {list(h5.keys())}")


def read_l3b_sst(blob: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(lat, lon, sst °C) pixel arrays of an INSAT L3B SST file; low-confidence pixels removed."""
    import h5py

    with h5py.File(io.BytesIO(blob), "r") as h5:
        lat = _scaled(_find(h5, "Latitude", "lat"))
        lon = _scaled(_find(h5, "Longitude", "lon"))
        sst = _scaled(_find(h5, "SST"))
        if "SST_QFLAGS" in h5:
            flags = np.squeeze(np.asarray(h5["SST_QFLAGS"][()]))
            if flags.shape == sst.shape:
                sst[flags != HIGH_CONFIDENCE] = np.nan
    if lat.ndim == 1 and lon.ndim == 1 and sst.ndim == 2:  # gridded variant: 1-D axes
        lon, lat = np.meshgrid(lon, lat)
    if np.nanmedian(sst) > 200:  # Kelvin
        sst = sst - 273.15
    return lat, lon, sst


def regrid(lat: np.ndarray, lon: np.ndarray, value: np.ndarray, bbox: tuple[float, float, float, float], step: float = GRID):
    """Average pixels into regular cells over the bbox; returns (lat centres, lon centres, grid)."""
    lat_min, lat_max, lon_min, lon_max = bbox
    lats = np.arange(lat_min + step / 2, lat_max, step)
    lons = np.arange(lon_min + step / 2, lon_max, step)
    ok = np.isfinite(lat) & np.isfinite(lon) & np.isfinite(value) & (value > -5) & (value < 40)
    ok &= (lat >= lat_min) & (lat < lat_max) & (lon >= lon_min) & (lon < lon_max)
    i = ((lat[ok] - lat_min) / step).astype(int).clip(0, len(lats) - 1)
    j = ((lon[ok] - lon_min) / step).astype(int).clip(0, len(lons) - 1)
    flat = i * len(lons) + j
    total = np.bincount(flat, weights=value[ok], minlength=len(lats) * len(lons))
    count = np.bincount(flat, minlength=len(lats) * len(lons))
    grid = np.where(count > 0, total / np.maximum(count, 1), np.nan).reshape(len(lats), len(lons))
    return lats, lons, grid


# ------------------------------------------------------------------------------ download (login)
def token(client: httpx.Client) -> str:
    user, password = os.getenv("MOSDAC_USERNAME"), os.getenv("MOSDAC_PASSWORD")
    if not user or not password:
        sys.exit("Set MOSDAC_USERNAME and MOSDAC_PASSWORD (free account: https://mosdac.gov.in/signup/)")
    r = client.post(TOKEN, json={"username": user, "password": password})
    if r.status_code in (400, 401):
        sys.exit(f"MOSDAC login failed: {r.text[:200]}")
    r.raise_for_status()
    return r.json()["access_token"]


def fetch_event(event: HistoricalEvent, client: httpx.Client, bearer: str) -> dict:
    start = (event.replay_start - timedelta(days=3)).date()
    end = event.replay_end.date()
    days: dict[date, dict] = {}
    used_ds = None
    for ds in DATASETS:
        days = pick_daily(search(ds, start, end, event.region))
        if days:
            used_ds = ds
            break
    if not days:
        return {"granules": 0, "note": "no INSAT L3B daily SST found on MOSDAC for this period"}
    grids, files, kept = [], [], []
    lats = lons = None
    for d in sorted(days):
        e = days[d]
        r = client.get(DOWNLOAD, params={"id": e["id"]}, headers={"Authorization": f"Bearer {bearer}"}, timeout=300)
        if r.status_code != 200:
            print(f"  {e['identifier']}: HTTP {r.status_code} {r.text[:120]}", flush=True)
            continue
        lat, lon, sst = read_l3b_sst(r.content)
        lats, lons, g = regrid(lat, lon, sst, event.region)
        grids.append(g)
        files.append(e["identifier"])
        kept.append(d)
        print(f"  {e['identifier']}: {np.isfinite(g).mean() * 100:.0f}% of cells cloud-free", flush=True)
    if not grids:
        return {"granules": 0, "note": "downloads failed"}
    stack = np.stack(grids)
    np.savez_compressed(
        OUT / event.id / "isro_sst.npz",
        lat=lats.astype(np.float32),
        lon=lons.astype(np.float32),
        sst=np.where(np.isnan(stack), FILL, np.round(stack * 100)).astype(np.int16),
        days=np.array([d.isoformat() for d in kept]),
        files=np.array(files),
    )
    return {
        "dataset": used_ds,
        "granules": len(files),
        "grid": f"{len(lats)}x{len(lons)} at {GRID}°",
        "coverage_pct": round(float(np.isfinite(stack).mean() * 100), 1),
        "source": "MOSDAC, SAC-ISRO (https://mosdac.gov.in) — INSAT Imager L3B daily SST",
        "retrieved_at": datetime.now(UTC).isoformat(),
    }


def inspect(path: str) -> None:
    import h5py

    def show(name, obj):
        if isinstance(obj, h5py.Dataset):
            print(f"{name}: shape={obj.shape} dtype={obj.dtype} attrs={dict(obj.attrs)}")

    with h5py.File(path, "r") as h5:
        print("root attrs:", {k: h5.attrs[k] for k in list(h5.attrs)[:40]})
        h5.visititems(show)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--inspect"]:
        inspect(args[1])
        sys.exit()
    with httpx.Client(headers=HEADERS, timeout=120, follow_redirects=True) as client:
        bearer = token(client)
        try:
            for eid in args or list(EVENTS):
                event = EVENTS[eid]
                print(f"[{eid}] ISRO INSAT SST (MOSDAC) …", flush=True)
                result = fetch_event(event, client, bearer)
                meta_path = OUT / eid / "meta.json"
                meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"event": eid, "products": {}}
                meta["products"]["isro_sst"] = result
                meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
                print(f"[{eid}] {result}", flush=True)
        finally:
            client.post(LOGOUT, json={"username": os.getenv("MOSDAC_USERNAME", "")})  # as mdapi.py does
