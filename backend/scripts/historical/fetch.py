"""Download real archived marine data for ORCA's historical replay (public sources, no login).

  GFS 0.5° atmosphere + GFS-Wave 0.25° forecasts, exactly as NOAA issued them
      https://noaa-gfs-bdp-pds.s3.amazonaws.com                  (NOAA Open Data Dissemination)
  NOAA OISST v2.1 daily sea-surface temperature (AVHRR satellite + in-situ blend) and its anomaly
      https://noaa-cdr-sea-surface-temp-optimum-interpolation-pds.s3.amazonaws.com
  VIIRS NOAA-20 chlorophyll-a (NOAA STAR ocean colour, OC3, archive starts 2022)
      https://noaa-jpss.s3.amazonaws.com/NOAA20/VIIRS/NOAA20_VIIRS_OC_GLOBAL_CHLOR-A_ops/
  IMD warnings as issued (CAP 1.2 XML), archived by the WMO Alert Hub since 2019
      https://cap-sources.s3.amazonaws.com/in-imd-en/

Only each event's region is kept, as compact int16 arrays, so the app runs offline:
backend/data/historical/<event>/{gfs_atmos,gfs_wave,sst,chl}.npz, cap/*.xml, meta.json

    pip install -r requirements-data.txt
    python scripts/historical/fetch.py                  # every event in orca/historical/events.py
    python scripts/historical/fetch.py tauktae-2021     # one event
    python scripts/historical/fetch.py --only cap       # one product (gfs, sst, chl, cap)
"""

from __future__ import annotations

import io
import json
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orca.historical.events import EVENTS, LEADS_H, HistoricalEvent  # noqa: E402
from orca.historical.store import ATMOS_FIELDS, FILL, WAVE_FIELDS, encode  # noqa: E402
from orca.timeutil import UTC  # noqa: E402

OUT = ROOT / "data" / "historical"
GFS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
OISST = "https://noaa-cdr-sea-surface-temp-optimum-interpolation-pds.s3.amazonaws.com"
JPSS = "https://noaa-jpss.s3.amazonaws.com"
CAP = "https://cap-sources.s3.amazonaws.com"
CHL_PREFIX = "NOAA20/VIIRS/NOAA20_VIIRS_OC_GLOBAL_CHLOR-A_ops"
CHL_TILE = "YY"  # NOAA STAR tile code for 60–120°E, 0–45°N (checked against each file's et_affine)
CHL_BLOCK = 6  # 0.0075° pixels averaged 6×6 -> 0.045° (~5 km) grid

DECODE_LOCK = threading.Lock()  # ecCodes and HDF5 are not safe to call from several threads
client = httpx.Client(timeout=httpx.Timeout(90.0, connect=30.0), follow_redirects=True)


def get(url: str, headers: dict | None = None, attempts: int = 4) -> httpx.Response | None:
    for i in range(attempts):
        try:
            r = client.get(url, headers=headers)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r
        except httpx.HTTPError as exc:
            if i == attempts - 1:
                print(f"  ! {url}: {exc}")
                return None
            time.sleep(2**i)
    return None


def last_modified(url: str) -> datetime | None:
    try:
        r = client.head(url)
        if r.status_code == 200 and "last-modified" in r.headers:
            return parsedate_to_datetime(r.headers["last-modified"]).astimezone(UTC)
    except httpx.HTTPError:
        pass
    return None


# ---------------------------------------------------------------- GRIB (GFS / GFS-Wave)
def idx_ranges(idx_text: str, wanted: list[tuple[str, str]]) -> dict[tuple[str, str], tuple[int, int | None]]:
    """Byte ranges of the wanted (VAR, LEVEL) messages; instantaneous values only (no ave/acc)."""
    lines = [ln.split(":") for ln in idx_text.strip().splitlines()]
    offsets = [int(p[1]) for p in lines]
    out = {}
    for i, p in enumerate(lines):
        key, fcst = (p[3], p[4]), p[5] if len(p) > 5 else ""
        if key in wanted and key not in out and "ave" not in fcst and "acc" not in fcst:
            out[key] = (offsets[i], offsets[i + 1] - 1 if i + 1 < len(offsets) else None)
    return out


def decode_grib(data: bytes, region: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    import eccodes

    gid = eccodes.codes_new_from_message(data)
    try:
        ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
        lat1 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
        lat2 = eccodes.codes_get(gid, "latitudeOfLastGridPointInDegrees")
        lon1 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
        di = eccodes.codes_get(gid, "iDirectionIncrementInDegrees")
        dj = eccodes.codes_get(gid, "jDirectionIncrementInDegrees")
        missing = eccodes.codes_get(gid, "missingValue")
        values = eccodes.codes_get_values(gid).reshape(nj, ni)
    finally:
        eccodes.codes_release(gid)
    values = np.where(values == missing, np.nan, values)
    lats = lat1 + np.arange(nj) * (dj if lat2 > lat1 else -dj)
    lons = lon1 + np.arange(ni) * di
    lat_min, lat_max, lon_min, lon_max = region
    rows = np.where((lats >= lat_min - 1e-6) & (lats <= lat_max + 1e-6))[0]
    cols = np.where((lons >= lon_min - 1e-6) & (lons <= lon_max + 1e-6))[0]
    sub = values[np.ix_(rows, cols)]
    order = np.argsort(lats[rows])
    return lats[rows][order], lons[cols], sub[order]


def gfs_url(kind: str, cycle: datetime, lead: int) -> str:
    base = f"{GFS}/gfs.{cycle:%Y%m%d}/{cycle:%H}"
    if kind == "atmos":
        return f"{base}/atmos/gfs.t{cycle:%H}z.pgrb2.0p50.f{lead:03d}"
    return f"{base}/wave/gridded/gfswave.t{cycle:%H}z.global.0p25.f{lead:03d}.grib2"


def fetch_gfs(event: HistoricalEvent, kind: str) -> dict:
    fields = ATMOS_FIELDS if kind == "atmos" else WAVE_FIELDS
    wanted = [(f.grib_var, f.grib_level) for f in fields]
    cycles, leads = event.cycles, list(LEADS_H)
    grid: dict = {}
    arrays: dict[str, np.ndarray] = {}
    missing: list[str] = []

    def job(ci: int, li: int) -> None:
        url = gfs_url(kind, cycles[ci], leads[li])
        idx = get(url + ".idx")
        if idx is None:
            missing.append(url)
            return
        ranges = idx_ranges(idx.text, wanted)
        for f in fields:
            rng = ranges.get((f.grib_var, f.grib_level))
            if rng is None:
                continue  # e.g. no precipitation rate in the f000 analysis
            start, end = rng
            r = get(url, headers={"Range": f"bytes={start}-{'' if end is None else end}"})
            if r is None:
                missing.append(f"{url}#{f.name}")
                continue
            with DECODE_LOCK:
                lats, lons, sub = decode_grib(r.content, event.region)
            if not grid:
                grid.update(lat=lats, lon=lons)
                for g in fields:
                    arrays[g.name] = np.full((len(cycles), len(leads), len(lats), len(lons)), FILL, dtype=np.int16)
            arrays[f.name][ci, li] = encode(sub, f)

    jobs = [(ci, li) for ci in range(len(cycles)) for li in range(len(leads))]
    # decode the first file alone so the grid exists before threads write into the arrays
    job(*jobs[0])
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda a: job(*a), jobs[1:]))

    # when each cycle was actually published (S3 upload time of its last lead file)
    with ThreadPoolExecutor(max_workers=16) as pool:
        published = list(pool.map(lambda c: last_modified(gfs_url(kind, c, leads[-1])), cycles))
    np.savez_compressed(
        OUT / event.id / f"gfs_{kind}.npz",
        lat=grid["lat"].astype(np.float32),
        lon=grid["lon"].astype(np.float32),
        cycles=np.array([c.isoformat() for c in cycles]),
        published=np.array([p.isoformat() if p else "" for p in published]),
        leads=np.array(leads, dtype=np.int16),
        **arrays,
    )
    return {
        "files": len(jobs),
        "missing": len(missing),
        "grid": f"{len(grid['lat'])}x{len(grid['lon'])}",
        "cycles": [c.isoformat() for c in cycles],
        "source": gfs_url(kind, cycles[0], 0).replace(GFS, "s3://noaa-gfs-bdp-pds"),
    }


# ---------------------------------------------------------------- OISST
def fetch_sst(event: HistoricalEvent) -> dict:
    from netCDF4 import Dataset

    days = [event.sst_first_day + timedelta(days=i) for i in range((event.sst_last_day - event.sst_first_day).days + 1)]
    lat_min, lat_max, lon_min, lon_max = event.region

    def download(d: date):
        for suffix in ("", "_preliminary"):
            url = f"{OISST}/data/v2.1/avhrr/{d:%Y%m}/oisst-avhrr-v02r01.{d:%Y%m%d}{suffix}.nc"
            r = get(url)
            if r is not None:
                return url, r.content
        return None

    def one(got):
        if got is None:
            return None
        url, content = got
        with Dataset("oisst", memory=content) as ds:
            lat, lon = ds["lat"][:], ds["lon"][:]
            rows = np.where((lat >= lat_min) & (lat <= lat_max))[0]
            cols = np.where((lon >= lon_min) & (lon <= lon_max))[0]
            sst = np.ma.filled(ds["sst"][0, 0, rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1].astype(float), np.nan)
            anom = np.ma.filled(ds["anom"][0, 0, rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1].astype(float), np.nan)
            return lat[rows], lon[cols], sst, anom, url

    with ThreadPoolExecutor(max_workers=8) as pool:
        downloads = list(pool.map(download, days))
    results = [one(g) for g in downloads]  # parse sequentially: HDF5 is not thread-safe
    ok = [(d, r) for d, r in zip(days, results) if r is not None]
    lat, lon = ok[0][1][0], ok[0][1][1]
    sst = np.stack([np.round(r[2] * 100) for _, r in ok])
    anom = np.stack([np.round(r[3] * 100) for _, r in ok])
    np.savez_compressed(
        OUT / event.id / "sst.npz",
        lat=np.asarray(lat, dtype=np.float32),
        lon=np.asarray(lon, dtype=np.float32),
        days=np.array([d.isoformat() for d, _ in ok]),
        sst=np.where(np.isnan(sst), FILL, sst).astype(np.int16),
        anom=np.where(np.isnan(anom), FILL, anom).astype(np.int16),
    )
    return {"days": len(ok), "missing_days": len(days) - len(ok), "source": ok[0][1][4].replace(OISST, "s3://noaa-cdr-sea-surface-temp-optimum-interpolation-pds")}


# ---------------------------------------------------------------- VIIRS chlorophyll
def list_keys(prefix: str) -> list[str]:
    keys, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        r = client.get(JPSS + "/", params=params)
        r.raise_for_status()
        keys += re.findall(r"<Key>([^<]+)</Key>", r.text)
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", r.text)
        if not m:
            return keys
        token = m.group(1)


def fetch_chl(event: HistoricalEvent) -> dict:
    from pyhdf.SD import SD, SDC

    lat_min, lat_max, lon_min, lon_max = event.region
    granules: dict[str, str] = {}  # granule time -> key (prefer the V stream over the Q quick-look stream)
    for d in event.chl_days:
        for key in list_keys(f"{CHL_PREFIX}/{d:%Y/%m/%d}/"):
            m = re.search(r"VR1(\w)CW_C(\d{7})_(\d{6})_(\w\w)\d\d_edgemask_chlora\.hdf$", key)
            if not m or m.group(4) != CHL_TILE:
                continue
            hhmm = int(m.group(3)[:4])
            if not 330 <= hhmm <= 1130:  # daytime passes over the Indian seas (UTC)
                continue
            t = m.group(2) + m.group(3)
            if t not in granules or m.group(1) == "V":
                granules[t] = key

    tmp = OUT / event.id / "_granule.hdf"
    total = count = None
    lat_c = lon_c = None
    used = 0
    for key in sorted(granules.values()):
        r = get(f"{JPSS}/{key}")
        if r is None:
            continue
        tmp.write_bytes(r.content)
        try:
            f = SD(str(tmp), SDC.READ)
        except Exception:
            continue
        a = f.attributes()
        aff = a["et_affine"]
        lon0, dlon, lat0, dlat = aff[4], aff[2], aff[5], aff[1]  # lon = lon0 + dlon*col, lat = lat0 + dlat*row
        r0, r1 = int((lat_max - lat0) / dlat), int((lat_min - lat0) / dlat)
        c0, c1 = int((lon_min - lon0) / dlon), int((lon_max - lon0) / dlon)
        r1 = r0 + (r1 - r0) // CHL_BLOCK * CHL_BLOCK
        c1 = c0 + (c1 - c0) // CHL_BLOCK * CHL_BLOCK
        chl = f.select("chlor_a")[r0:r1, c0:c1].astype(np.float64)
        f.end()
        valid = (chl > 0.001) & (chl < 100)
        if not valid.any():
            continue
        logc = np.where(valid, np.log10(np.where(valid, chl, 1)), 0.0)
        shape = (chl.shape[0] // CHL_BLOCK, CHL_BLOCK, chl.shape[1] // CHL_BLOCK, CHL_BLOCK)
        s, n = logc.reshape(shape).sum(axis=(1, 3)), valid.reshape(shape).sum(axis=(1, 3))
        total = s if total is None else total + s
        count = n if count is None else count + n
        if lat_c is None:
            lat_c = lat0 + dlat * (r0 + (np.arange(shape[0]) + 0.5) * CHL_BLOCK - 0.5)
            lon_c = lon0 + dlon * (c0 + (np.arange(shape[2]) + 0.5) * CHL_BLOCK - 0.5)
        used += 1
    tmp.unlink(missing_ok=True)
    if total is None:
        return {"granules": 0}
    mean_log = np.where(count > 0, total / np.maximum(count, 1), np.nan)
    order = np.argsort(lat_c)
    np.savez_compressed(
        OUT / event.id / "chl.npz",
        lat=lat_c[order].astype(np.float32),
        lon=lon_c.astype(np.float32),
        log10_chl=np.where(np.isnan(mean_log), FILL, np.round(mean_log * 1000)).astype(np.int16)[order],
        pixels=np.minimum(count, 32767).astype(np.int16)[order],
        days=np.array([d.isoformat() for d in event.chl_days]),
    )
    return {
        "granules": used,
        "grid": f"{len(lat_c)}x{len(lon_c)} at {abs(dlat) * CHL_BLOCK:.3f}°",
        "coverage_pct": round(float((count > 0).mean() * 100), 1),
        "source": f"s3://noaa-jpss/{CHL_PREFIX}/ (tile {CHL_TILE})",
    }


# ---------------------------------------------------------------- IMD CAP warnings (WMO Alert Hub archive)
def fetch_cap(event: HistoricalEvent) -> dict:
    """Every IMD CAP alert sent from two days before the replay to its end, kept as the original XML."""
    folder = OUT / event.id / "cap"
    folder.mkdir(parents=True, exist_ok=True)
    first = (event.replay_start - timedelta(days=2)).date()
    days = [first + timedelta(days=i) for i in range((event.replay_end.date() - first).days + 1)]
    keys: list[str] = []
    for d in days:
        r = get(f"{CAP}/?list-type=2&prefix=in-imd-en/{d:%Y-%m-%d}&max-keys=1000")
        if r is not None:
            keys += re.findall(r"<Key>([^<]+\.xml)</Key>", r.text)
    for key in keys:
        r = get(f"{CAP}/{key}")
        if r is not None:
            (folder / key.split("/")[-1]).write_bytes(r.content)
    return {"alerts": len(keys), "source": "s3://cap-sources/in-imd-en/ (WMO Alert Hub archive of IMD CAP feed)"}


PRODUCTS = ("gfs", "sst", "chl", "cap")


def fetch_event(event: HistoricalEvent, only: tuple[str, ...] = PRODUCTS) -> None:
    (OUT / event.id).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    meta_path = OUT / event.id / "meta.json"
    meta: dict = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"event": event.id, "products": {}}
    meta.update(title=event.title, retrieved_at=datetime.now(UTC).isoformat())
    if "gfs" in only:
        for kind in ("wave", "atmos"):
            print(f"[{event.id}] GFS {kind} …", flush=True)
            meta["products"][f"gfs_{kind}"] = fetch_gfs(event, kind)
    if "sst" in only:
        print(f"[{event.id}] OISST …", flush=True)
        meta["products"]["sst"] = fetch_sst(event)
    if "chl" in only and event.chl_days:
        print(f"[{event.id}] VIIRS chlorophyll …", flush=True)
        meta["products"]["chl"] = fetch_chl(event)
    if "cap" in only:
        print(f"[{event.id}] IMD CAP archive …", flush=True)
        meta["products"]["cap"] = fetch_cap(event)
    meta["seconds"] = round(time.time() - t0)
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    size = sum(p.stat().st_size for p in (OUT / event.id).rglob("*") if p.is_file()) / 1e6
    print(f"[{event.id}] done in {meta['seconds']} s, {size:.1f} MB", flush=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    only = PRODUCTS
    if "--only" in args:
        i = args.index("--only")
        only = tuple(args[i + 1].split(","))
        del args[i : i + 2]
    for eid in args or list(EVENTS):
        fetch_event(EVENTS[eid], only)
