"""Data catalogue + discovery (the 'marine data discovery' agent's knowledge).

Before any data is fetched, ORCA asks: which datasets can answer this question, here and now?
Each source is checked for its area, its time span and whether it had been published at the moment of the
question (historical replay: no look-ahead). ISRO's own archive is searched live through MOSDAC's public
OpenSearch (no login), so ORCA can say which ISRO satellite products exist for the place and date even when
they are not in the local archive yet.

Result: one row per source (covers / outside / not yet published / not downloaded / found on MOSDAC) plus
plain gaps ("the loaded replay does not cover this place"), which the explanation states to the user."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

import httpx
from pydantic import BaseModel, Field

from .timeutil import ensure_utc

MOSDAC_SEARCH = "https://mosdac.gov.in/apios/datasets.json"
# ISRO products relevant to marine questions, searched on MOSDAC (dataset ids as listed by MOSDAC).
ISRO_PRODUCTS = {
    "3RIMG_L3B_SST_DLY": "INSAT-3DR Imager daily SST",
    "3DIMG_L3B_SST_DLY": "INSAT-3D Imager daily SST",
    "3RIMG_L2B_SST": "INSAT-3DR Imager half-hourly SST",
}
INDIA_WATERS = (0.0, 26.5, 60.0, 98.0)  # lat_min, lat_max, lon_min, lon_max: IMD / INCOIS service area


class SourceStatus(BaseModel):
    id: str
    name: str
    agency: str
    variables: list[str]
    status: str  # covers | outside | not_published | not_downloaded | found_on_mosdac | not_found | unchecked
    detail: str = ""


class Discovery(BaseModel):
    lat: float
    lon: float
    mode: str
    sources: list[SourceStatus]
    gaps: list[str] = Field(default_factory=list)
    isro: list[dict] = Field(default_factory=list)

    def covering(self) -> list[SourceStatus]:
        return [s for s in self.sources if s.status == "covers"]


def _in(box: tuple[float, float, float, float], lat: float, lon: float) -> bool:
    return box[0] <= lat <= box[1] and box[2] <= lon <= box[3]


class MosdacCatalog:
    """Live, public search of ISRO's MOSDAC archive (cached; a slow or failing portal never blocks an answer)."""

    def __init__(self, timeout_s: float = 6.0) -> None:
        self.enabled = os.getenv("ORCA_MOSDAC_SEARCH", "1") != "0"
        self.timeout_s = timeout_s
        self._cache: dict[tuple[str, str, str], list[dict] | None] = {}

    async def granules(self, dataset: str, day: datetime, lat: float, lon: float) -> list[dict] | None:
        """Granules of a dataset on that day covering the point; None if the search could not be done."""
        if not self.enabled:
            return None
        key = (dataset, f"{day:%Y-%m-%d}", f"{lat:.1f},{lon:.1f}")
        if key in self._cache:
            return self._cache[key]
        params = {"datasetId": dataset, "startTime": f"{day:%Y-%m-%d}", "endTime": f"{day:%Y-%m-%d}",
                  "boundingBox": f"{lon - 0.5},{lat - 0.5},{lon + 0.5},{lat + 0.5}", "count": 50}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s, headers={"User-Agent": "ORCA/1"}) as client:
                r = await client.get(MOSDAC_SEARCH, params=params)
            entries = (r.json().get("entries") or []) if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            entries = None
        self._cache[key] = entries
        return entries


MOSDAC = MosdacCatalog()


async def discover(svc, lat: float, lon: float, start: datetime, end: datetime, mosdac: MosdacCatalog | None = None) -> Discovery:
    mosdac = mosdac or MOSDAC
    start, end = ensure_utc(start), ensure_utc(end)
    as_of = ensure_utc(svc.clock())
    sources: list[SourceStatus] = []
    gaps: list[str] = []

    if svc.replay is not None:  # historical replay: what the local archive holds, and what was published by then
        arc, ev = svc.replay.archive, svc.replay.event
        inside = arc.covers(lat, lon)
        area = f"{ev.region[0]:g}–{ev.region[1]:g}°N, {ev.region[2]:g}–{ev.region[3]:g}°E"
        where = "covers" if inside else "outside"
        sources.append(SourceStatus(id="gfs", name="NOAA GFS 0.5° + GFS-Wave 0.25° (runs as issued)", agency="NOAA",
                                    variables=["wind", "waves", "rain", "visibility", "thunderstorm"], status=where,
                                    detail=f"archive area {area}"))
        isro_file = (arc.dir / "isro_sst.npz").exists()
        isro_now = arc.isro_sst_at(lat, lon, as_of) if isro_file else None
        sources.append(SourceStatus(
            id="isro_sst", name="ISRO INSAT-3DR/3D daily SST (MOSDAC)", agency="ISRO", variables=["sea temperature"],
            status="covers" if isro_now else ("not_downloaded" if not isro_file else ("outside" if not inside else "not_published")),
            detail=(f"{isro_now[2]}" if isro_now else ("not in the local archive: run scripts/historical/fetch_mosdac.py" if not isro_file
                    else "no cloud-free pixel released yet"))))
        oisst = arc.sst_at(lat, lon, as_of)
        sources.append(SourceStatus(id="oisst", name="NOAA OISST v2.1 daily SST", agency="NOAA", variables=["sea temperature"],
                                    status="covers" if oisst else ("outside" if not inside else "not_published"),
                                    detail=f"analysis for {oisst[2]:%d %b %Y}" if oisst else ""))
        chl = arc.chl_available(as_of)
        sources.append(SourceStatus(id="chl", name="NOAA-20 VIIRS chlorophyll composite", agency="NOAA", variables=["chlorophyll"],
                                    status="covers" if chl and inside else ("outside" if not inside else "not_published"),
                                    detail="" if ev.chl_days else "VIIRS archive starts 2022"))
        tide = arc.tide_at(lat, lon, start)
        sources.append(SourceStatus(id="tide", name="Predicted tides at harbours", agency="Open-Meteo", variables=["tide"],
                                    status="covers" if tide else "outside",
                                    detail=f"nearest harbour {tide[1]}" if tide else "no harbour within 60 km"))
        sources.append(SourceStatus(id="imd_cap", name="IMD warnings as issued (CAP, WMO Alert Hub archive)", agency="IMD",
                                    variables=["official warnings"], status="covers" if _in(INDIA_WATERS, lat, lon) else "outside",
                                    detail="only warnings sent before the replay moment"))
        if not inside:
            gaps.append("outside_replay")
    else:
        sources.append(SourceStatus(id="open_meteo", name="Open-Meteo weather + marine forecast", agency="Open-Meteo",
                                    variables=["wind", "waves", "rain", "visibility", "tide", "sea temperature"], status="covers",
                                    detail="global; live forecast"))
        in_india = _in(INDIA_WATERS, lat, lon)
        sources.append(SourceStatus(id="imd_cap", name="IMD CAP warning feed", agency="IMD", variables=["official warnings"],
                                    status="covers" if in_india else "outside"))
        sources.append(SourceStatus(id="incois_pfz", name="INCOIS PFZ advisories", agency="INCOIS", variables=["fishing zones"],
                                    status="covers" if in_india and svc.pfz_live is not None else "outside"))

    # ISRO's archive, searched live: what exists for this place and day (even if not downloaded here)
    day = min(max(start, as_of - timedelta(days=1)), as_of)
    found = await asyncio.gather(*(mosdac.granules(ds, day, lat, lon) for ds in ISRO_PRODUCTS))
    isro = []
    for (ds, label), entries in zip(ISRO_PRODUCTS.items(), found):
        if entries is None:
            continue
        isro.append({"dataset": ds, "product": label, "granules": len(entries),
                     "example": entries[0]["identifier"] if entries else None, "day": f"{day:%Y-%m-%d}"})
    if isro:
        total = sum(i["granules"] for i in isro)
        sources.append(SourceStatus(id="mosdac", name="ISRO MOSDAC archive (live search)", agency="ISRO", variables=["sea temperature"],
                                    status="found_on_mosdac" if total else "not_found",
                                    detail=f"{total} INSAT SST granules on {day:%d %b %Y}"))
    elif mosdac.enabled:
        sources.append(SourceStatus(id="mosdac", name="ISRO MOSDAC archive (live search)", agency="ISRO", variables=["sea temperature"],
                                    status="unchecked", detail="portal did not answer in time"))
    mode = "historical" if svc.replay is not None else svc.mode
    return Discovery(lat=lat, lon=lon, mode=mode, sources=sources, gaps=gaps, isro=isro)

