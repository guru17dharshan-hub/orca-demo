"""Backtest: would ORCA's evening verdict have matched what the sea actually did next morning?

For every evening in the season and every harbour below:
  forecast  — the NOAA GFS / GFS-Wave 12Z run that was on NOAA's server by 21:30 IST,
              read for 06:00–12:00 IST the next day (leads 12, 15 and 18 h)
  truth     — ECMWF ERA5 reanalysis for the same hours (independent of GFS; assimilates
              satellite altimeter wave heights and scatterometer winds)
Both are scored with ORCA's own rule set (waves + wind; the window takes the worst hour).
A persistence baseline ('tomorrow will be like right now') shows what the forecast adds.

    python scripts/historical/backtest.py            # download (cached) + evaluate
    python scripts/historical/backtest.py --eval     # re-score cached data only
Writes data/backtest/results.json (served at /api/backtest).
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from orca.geo.ports import PORTS, offshore_point  # noqa: E402
from orca.risk.rules import RANK, RULESET_VERSION, WAVE_RULE, WIND_RULE, RiskLevel  # noqa: E402
from orca.timeutil import UTC  # noqa: E402

OUT = ROOT / "data" / "backtest"
SEASON = (date(2021, 5, 1), date(2021, 6, 15))  # pre-monsoon, Cyclone Tauktae, monsoon onset
HARBOURS = ["veraval", "mumbai", "ratnagiri", "malvan", "goa", "karwar", "malpe", "mangaluru", "kozhikode", "kochi",
            "neendakara", "vizhinjam"]
LEADS = (12, 15, 18)  # from the 12Z run: 00, 03, 06 UTC next day = 05:30–11:30 IST
GFS = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
ERA5 = "https://storage.googleapis.com/gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
ERA5_VARS = {"swh": "significant_height_of_combined_wind_waves_and_swell", "u10": "10m_u_component_of_wind",
             "v10": "10m_v_component_of_wind"}

client = httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0))
LOCK = threading.Lock()


def get(url: str, headers: dict | None = None) -> bytes:
    for i in range(4):
        try:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            return r.content
        except httpx.HTTPError:
            if i == 3:
                raise
            time.sleep(2**i)
    raise RuntimeError(url)


def sites() -> list[dict]:
    out = []
    for pid in HARBOURS:
        p = next(x for x in PORTS if x.id == pid)
        lat, lon = offshore_point(p.lat, p.lon, 10.0)
        out.append({"id": pid, "name": p.name, "lat": round(lat, 3), "lon": round(lon, 3)})
    return out


def nearest_valid(values: np.ndarray, lats: np.ndarray, lons: np.ndarray, lat: float, lon: float) -> float | None:
    i, j = int(np.argmin(np.abs(lats - lat))), int(np.argmin(np.abs(lons - lon)))
    for r in range(0, 4):
        win = values[max(0, i - r): i + r + 1, max(0, j - r): j + r + 1]
        ok = ~np.isnan(win)
        if ok.any():
            ii, jj = np.nonzero(ok)
            k = int(np.argmin((ii + max(0, i - r) - i) ** 2 + (jj + max(0, j - r) - j) ** 2))
            return float(win[ii[k], jj[k]])
    return None


# ------------------------------------------------------------------ GFS forecasts
def gfs_fields(url: str, wanted: list[tuple[str, str]]) -> dict[tuple[str, str], tuple]:
    import eccodes

    idx = get(url + ".idx").decode()
    lines = [ln.split(":") for ln in idx.strip().splitlines()]
    offsets = [int(p[1]) for p in lines]
    out = {}
    for i, p in enumerate(lines):
        key = (p[3], p[4])
        if key in wanted and key not in out and "ave" not in p[5] and "acc" not in p[5]:
            end = offsets[i + 1] - 1 if i + 1 < len(offsets) else ""
            data = get(url, {"Range": f"bytes={offsets[i]}-{end}"})
            with LOCK:
                gid = eccodes.codes_new_from_message(data)
                ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
                lat1 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
                lon1 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
                di = eccodes.codes_get(gid, "iDirectionIncrementInDegrees")
                dj = eccodes.codes_get(gid, "jDirectionIncrementInDegrees")
                missing = eccodes.codes_get(gid, "missingValue")
                vals = eccodes.codes_get_values(gid).reshape(nj, ni)
                eccodes.codes_release(gid)
            vals = np.where(vals == missing, np.nan, vals)
            out[key] = (vals, lat1 - np.arange(nj) * dj, lon1 + np.arange(ni) * di)
    return out


def fetch_forecasts(days: list[date], site_list: list[dict]) -> dict:
    def one(d: date):
        cycle = datetime(d.year, d.month, d.day, 12, tzinfo=UTC)
        base = f"{GFS}/gfs.{d:%Y%m%d}/12"
        row = {}
        for lead in LEADS:
            wave = gfs_fields(f"{base}/wave/gridded/gfswave.t12z.global.0p25.f{lead:03d}.grib2", [("HTSGW", "surface")])
            atm = gfs_fields(f"{base}/atmos/gfs.t12z.pgrb2.0p50.f{lead:03d}", [("UGRD", "10 m above ground"), ("VGRD", "10 m above ground")])
            hs, hl, hn = wave[("HTSGW", "surface")]
            u, al, an = atm[("UGRD", "10 m above ground")]
            v = atm[("VGRD", "10 m above ground")][0]
            for s in site_list:
                wave_v = nearest_valid(hs, hl, hn, s["lat"], s["lon"])
                i, j = int(np.argmin(np.abs(al - s["lat"]))), int(np.argmin(np.abs(an - s["lon"])))
                row.setdefault(s["id"], []).append({"lead": lead, "wave": wave_v, "wind": float(math.hypot(u[i, j], v[i, j]) * 3.6)})
        return d.isoformat(), {"run": cycle.isoformat(), "sites": row}

    with ThreadPoolExecutor(max_workers=12) as pool:
        return dict(pool.map(one, days))


# ------------------------------------------------------------------ ERA5 truth
def era5_hour_index(t: datetime) -> int:
    return int((t - datetime(1900, 1, 1, tzinfo=UTC)).total_seconds() // 3600)


def fetch_truth(hours: list[datetime], site_list: list[dict]) -> dict:
    import numcodecs

    blosc = numcodecs.Blosc()
    lats = 90 - np.arange(721) * 0.25
    lons = np.arange(1440) * 0.25

    def one(args):
        t, short = args
        raw = get(f"{ERA5}/{ERA5_VARS[short]}/{era5_hour_index(t)}.0.0")
        a = np.frombuffer(blosc.decode(raw), dtype="<f4").reshape(721, 1440).astype(float)
        vals = {}
        for s in site_list:
            if short == "swh":
                vals[s["id"]] = nearest_valid(a, lats, lons, s["lat"], s["lon"])
            else:
                i, j = int(np.argmin(np.abs(lats - s["lat"]))), int(np.argmin(np.abs(lons - s["lon"])))
                vals[s["id"]] = float(a[i, j])
        return t.isoformat(), short, vals

    out: dict = {}
    jobs = [(t, v) for t in hours for v in ERA5_VARS]
    with ThreadPoolExecutor(max_workers=12) as pool:
        for iso, short, vals in pool.map(one, jobs):
            out.setdefault(iso, {})[short] = vals
    return out


# ------------------------------------------------------------------ scoring
def level(wave: float | None, wind: float | None) -> str:
    if wave is None or wind is None:
        return RiskLevel.INSUFFICIENT_DATA.value
    a, b = WAVE_RULE.classify(wave).level, WIND_RULE.classify(wind).level
    return (a if RANK[a] >= RANK[b] else b).value


def worst(levels: list[str]) -> str:
    known = [lv for lv in levels if lv != RiskLevel.INSUFFICIENT_DATA.value]
    return max(known, key=lambda lv: RANK[RiskLevel(lv)]) if known else RiskLevel.INSUFFICIENT_DATA.value


def evaluate(forecasts: dict, truth: dict, site_list: list[dict]) -> dict:
    order = ["LOW", "MODERATE", "HIGH", "SEVERE"]
    matrix = {f: {o: 0 for o in order} for f in order}
    persist = {f: {o: 0 for o in order} for f in order}
    rows = []
    for day, fc in sorted(forecasts.items()):
        d = date.fromisoformat(day)
        nxt = datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(days=1)
        obs_times = [nxt + timedelta(hours=h - 12) for h in LEADS]  # 00, 03, 06 UTC
        now_time = datetime(d.year, d.month, d.day, 15, tzinfo=UTC)  # 20:30 IST: what a fisherman sees that evening
        for s in site_list:
            fl = [level(x["wave"], x["wind"]) for x in fc["sites"][s["id"]]]
            ol = []
            peak_wave = peak_wind = 0.0
            for t in obs_times:
                tr = truth.get(t.isoformat(), {})
                w = (tr.get("swh") or {}).get(s["id"])
                u, v = (tr.get("u10") or {}).get(s["id"]), (tr.get("v10") or {}).get(s["id"])
                wind = math.hypot(u, v) * 3.6 if u is not None and v is not None else None
                ol.append(level(w, wind))
                peak_wave, peak_wind = max(peak_wave, w or 0), max(peak_wind, wind or 0)
            tr_now = truth.get(now_time.isoformat(), {})
            u, v = (tr_now.get("u10") or {}).get(s["id"]), (tr_now.get("v10") or {}).get(s["id"])
            pl = level((tr_now.get("swh") or {}).get(s["id"]), math.hypot(u, v) * 3.6 if u is not None and v is not None else None)
            f_lv, o_lv = worst(fl), worst(ol)
            if f_lv in order and o_lv in order:
                matrix[f_lv][o_lv] += 1
                if pl in order:
                    persist[pl][o_lv] += 1
            rows.append({"date": day, "site": s["id"], "forecast": f_lv, "observed": o_lv, "persistence": pl,
                         "fc_wave_max": round(max((x["wave"] or 0) for x in fc["sites"][s["id"]]), 2),
                         "obs_wave_max": round(peak_wave, 2), "obs_wind_max": round(peak_wind, 1)})

    def binary(m: dict) -> dict:
        danger = ("HIGH", "SEVERE")
        hits = sum(m[f][o] for f in danger for o in danger)
        misses = sum(m[f][o] for f in ("LOW", "MODERATE") for o in danger)
        false_alarms = sum(m[f][o] for f in danger for o in ("LOW", "MODERATE"))
        correct_neg = sum(m[f][o] for f in ("LOW", "MODERATE") for o in ("LOW", "MODERATE"))
        total = hits + misses + false_alarms + correct_neg
        exact = sum(m[x][x] for x in order)
        within_one = sum(m[f][o] for f in order for o in order if abs(order.index(f) - order.index(o)) <= 1)
        return {
            "site_days": total, "dangerous_observed": hits + misses, "hits": hits, "misses": misses,
            "false_alarms": false_alarms, "correct_negatives": correct_neg,
            "pod": round(hits / (hits + misses), 3) if hits + misses else None,
            "far": round(false_alarms / (hits + false_alarms), 3) if hits + false_alarms else None,
            "missed_danger_rate": round(misses / (hits + misses), 3) if hits + misses else None,
            "exact_level_accuracy": round(exact / total, 3) if total else None,
            "within_one_level": round(within_one / total, 3) if total else None,
        }

    return {"matrix": matrix, "persistence_matrix": persist, "scores": binary(matrix), "persistence_scores": binary(persist), "rows": rows}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / "raw.json"
    site_list = sites()
    days = [SEASON[0] + timedelta(days=i) for i in range((SEASON[1] - SEASON[0]).days + 1)]
    if "--eval" in sys.argv and cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8"))
    else:
        t0 = time.time()
        print(f"GFS forecasts for {len(days)} evenings × {len(site_list)} harbours …", flush=True)
        forecasts = fetch_forecasts(days, site_list)
        hours = sorted({datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(days=1, hours=h - 12) for d in days for h in LEADS}
                       | {datetime(d.year, d.month, d.day, 15, tzinfo=UTC) for d in days})
        print(f"ERA5 truth for {len(hours)} hours …", flush=True)
        truth = fetch_truth(hours, site_list)
        raw = {"forecasts": forecasts, "truth": truth, "download_seconds": round(time.time() - t0)}
        cache.write_text(json.dumps(raw), encoding="utf-8")
    result = evaluate(raw["forecasts"], raw["truth"], site_list)
    result.update({
        "generated_at": datetime.now(UTC).isoformat(),
        "rule_version": RULESET_VERSION,
        "season": {"from": SEASON[0].isoformat(), "to": SEASON[1].isoformat()},
        "window": "06:00–12:00 IST next morning (00, 03, 06 UTC), decided at 21:30 IST",
        "sites": site_list,
        "method": {
            "forecast": "NOAA GFS 0.5° winds + GFS-Wave 0.25° significant wave height from the 12Z run (on NOAA's server by "
                        "about 21:15 IST), leads 12/15/18 h; nearest sea cell to a point 10 km off each harbour.",
            "truth": "ECMWF ERA5 hourly reanalysis (0.25°, via Google's public ARCO-ERA5 store), same hours and points.",
            "scoring": f"ORCA rule set {RULESET_VERSION}: wave height (WMO sea state) and 10 m wind (Beaufort); a window "
                       "takes its worst hour. 'Dangerous' = HIGH or SEVERE.",
            "baseline": "Persistence: the level ERA5 shows at 20:30 IST that evening, used as tomorrow's forecast.",
            "limits": "Warnings, thunderstorms and visibility are not part of this test (no independent truth for them). "
                      "ERA5 is itself a model reanalysis, not a buoy measurement.",
        },
        "sources": [
            "s3://noaa-gfs-bdp-pds (NOAA Open Data Dissemination)",
            "gs://gcp-public-data-arco-era5 (ECMWF ERA5, Copernicus Climate Change Service)",
        ],
    })
    (OUT / "results.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    s, p = result["scores"], result["persistence_scores"]
    print(f"site-days {s['site_days']}: dangerous {s['dangerous_observed']}, POD {s['pod']} (persistence {p['pod']}), "
          f"FAR {s['far']} (persistence {p['far']}), missed {s['misses']}, exact {s['exact_level_accuracy']}, "
          f"within one level {s['within_one_level']}")


if __name__ == "__main__":
    main()
