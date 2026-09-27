"""Potential Fishing Zone discovery (guide §22) — spatial, not a chatbot answer.

PFZ products → normalize geometry + validity → keep valid → distance/bearing from
the user → check geofence overlap → nearest viable zone + evidence.

Providers:
  INCOISPFZProvider — INCOIS PFZ GeoServer WFS (official, experimental: endpoint
                      and schema not yet verified from this project's network)
  DemoPFZProvider   — zones on the simulated scenario's chlorophyll fronts,
                      labelled DEMO and never presented as an INCOIS advisory"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta

import httpx
from pydantic import BaseModel, Field

from .geo.geofences import GeofenceIndex
from .geo.geometry import (
    bearing_deg,
    compass16,
    destination_point,
    distance_to_line_km,
    distance_to_polygon_km,
    haversine_km,
)
from .models import AdapterHealth, DataType, Evidence
from .scenario import DEMO_PFZ_CENTRES, SCENARIO_SOURCE, Scenario
from .timeutil import UTC, ensure_utc, ist_midnight

INCOIS_PFZ_WFS = "https://incois.gov.in/geoserver/PFZ_Automation/ows"


class PFZZone(BaseModel):
    id: str
    name: str
    source: str
    data_type: DataType
    geometry: str  # polygon | line
    coordinates: list[tuple[float, float]]
    centroid: tuple[float, float]
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    attributes: dict = Field(default_factory=dict)
    reference: str | None = None
    retrieved_at: datetime

    def distance_km(self, lat: float, lon: float) -> float:
        if self.geometry == "polygon":
            return distance_to_polygon_km(lat, lon, self.coordinates)
        return distance_to_line_km(lat, lon, self.coordinates)

    def to_evidence(self) -> Evidence:
        return Evidence(
            id=f"pfz:{self.id}",
            source=self.source,
            product="Potential Fishing Zone",
            data_type=self.data_type,
            variable="pfz",
            value=self.name,
            lat=self.centroid[0],
            lon=self.centroid[1],
            valid_time=self.valid_from,
            retrieved_at=self.retrieved_at,
            reference=self.reference,
            processing=f"valid {self.valid_from} to {self.valid_until}",
        )


class PFZCandidate(BaseModel):
    zone: PFZZone
    distance_km: float
    bearing_deg: float
    compass: str
    viable: bool
    issues: list[str] = Field(default_factory=list)


class PFZProvider(ABC):
    name = "pfz"
    mode = "live"

    def __init__(self) -> None:
        self._health = AdapterHealth(name=self.name, mode=self.mode)

    @abstractmethod
    async def zones(self, now: datetime) -> list[PFZZone]: ...

    def health(self) -> AdapterHealth:
        return self._health.model_copy()


class DemoPFZProvider(PFZProvider):
    name = "demo-pfz"
    mode = "replay"

    def __init__(self, scenario: Scenario) -> None:
        super().__init__()
        self.scenario = scenario

    async def zones(self, now: datetime) -> list[PFZZone]:
        now = ensure_utc(now)
        valid_from = ist_midnight(self.scenario.anchor, -1) + timedelta(hours=6)  # 06:00 IST scenario day 0
        valid_until = self.scenario.anchor + timedelta(hours=18)  # 18:00 IST scenario day 1
        out = []
        for zid, lat, lon, label in DEMO_PFZ_CENTRES:
            ring = [destination_point(lat, lon, b, 12.0) for b in range(0, 361, 30)]
            f = self.scenario.fields(lat, lon, now)
            out.append(
                PFZZone(
                    id=zid,
                    name=f"{label} (DEMO)",
                    source=f"{SCENARIO_SOURCE} — DEMO zone, NOT an INCOIS PFZ advisory",
                    data_type=DataType.SIMULATED,
                    geometry="polygon",
                    coordinates=[(round(a, 4), round(b, 4)) for a, b in ring],
                    centroid=(lat, lon),
                    valid_from=valid_from,
                    valid_until=valid_until,
                    attributes={
                        "basis": "simulated chlorophyll front",
                        "sea_surface_temperature_c": f["sea_surface_temperature"],
                        "chlorophyll_mg_m3": f["chlorophyll"],
                    },
                    reference="docs/DATA_SOURCES.md#simulated-scenario",
                    retrieved_at=now,
                )
            )
        self._health.status, self._health.last_success = "ok", datetime.now(UTC)
        return out


class INCOISPFZProvider(PFZProvider):
    """Official INCOIS PFZ lines via GeoServer WFS. Experimental: the endpoint was
    not reachable from the development network, so the schema is parsed generically."""

    name = "incois-pfz"
    mode = "live"

    def __init__(self, client: httpx.AsyncClient | None = None, timeout_s: float = 15.0) -> None:
        super().__init__()
        self._client = client
        self._timeout = timeout_s

    async def zones(self, now: datetime) -> list[PFZZone]:
        started = time.perf_counter()
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        params = {
            "service": "WFS",
            "version": "1.0.0",
            "request": "GetFeature",
            "typeName": "PFZ_Automation:pfzlines",
            "outputFormat": "application/json",
        }
        try:
            resp = await client.get(INCOIS_PFZ_WFS, params=params)
            resp.raise_for_status()
            zones = parse_pfz_geojson(resp.json(), datetime.now(UTC))
        except Exception as exc:
            self._health.status = "unavailable"
            self._health.last_error = f"{type(exc).__name__}: {exc}"[:300]
            self._health.last_latency_ms = round((time.perf_counter() - started) * 1000, 1)
            raise
        finally:
            if self._client is None:
                await client.aclose()
        self._health.status, self._health.last_success = "ok", datetime.now(UTC)
        self._health.last_latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return zones


def parse_pfz_geojson(fc: dict, retrieved_at: datetime) -> list[PFZZone]:
    out = []
    for i, feat in enumerate(fc.get("features", [])):
        geom = feat.get("geometry") or {}
        gtype, coords = geom.get("type"), geom.get("coordinates") or []
        if gtype == "LineString":
            lines = [coords]
        elif gtype == "MultiLineString":
            lines = coords
        elif gtype == "Polygon":
            lines = [coords[0]] if coords else []
        else:
            continue
        props = feat.get("properties") or {}
        for j, line in enumerate(lines):
            pts = [(float(c[1]), float(c[0])) for c in line]
            if len(pts) < 2:
                continue
            mid = pts[len(pts) // 2]
            out.append(
                PFZZone(
                    id=str(feat.get("id") or f"incois-{i}-{j}"),
                    name=str(props.get("name") or props.get("sector") or f"INCOIS PFZ line {i + 1}"),
                    source="INCOIS PFZ (GeoServer WFS)",
                    data_type=DataType.OFFICIAL_ADVISORY,
                    geometry="polygon" if gtype == "Polygon" else "line",
                    coordinates=pts,
                    centroid=mid,
                    attributes={k: v for k, v in props.items() if isinstance(v, (str, int, float))},
                    reference=INCOIS_PFZ_WFS,
                    retrieved_at=retrieved_at,
                )
            )
    return out


# A zone further than this from the boat is not a fishing trip: it is on another coast or days away.
# ORCA's working assumption for a long day trip (small mechanised boats); validate with fisheries departments.
MAX_TRIP_KM = 150.0


def rank_zones(
    lat: float,
    lon: float,
    zones: list[PFZZone],
    geofences: GeofenceIndex,
    now: datetime,
    limit: int = 5,
) -> list[PFZCandidate]:
    now = ensure_utc(now)
    out: list[PFZCandidate] = []
    for z in zones:
        if z.valid_until and z.valid_until < now:
            continue
        issues: list[str] = []
        c_lat, c_lon = z.centroid
        gstatus = geofences.check(c_lat, c_lon, now)
        for hit in gstatus.hits:
            if hit.relation in ("inside", "beyond"):
                issues.append(f"{hit.relation} {hit.name}")
            elif hit.relation == "approaching":
                issues.append(f"{hit.distance_km:.1f} km from {hit.name}")
        if z.valid_from and z.valid_from > now:
            issues.append("not yet valid")
        distance = round(min(z.distance_km(lat, lon), haversine_km(lat, lon, c_lat, c_lon)), 1)
        if distance > MAX_TRIP_KM:
            issues.append(f"{distance:.0f} km away — beyond a fishing trip's range ({MAX_TRIP_KM:.0f} km)")
        viable = gstatus.status not in ("inside_restricted", "beyond_boundary", "inside_protected") and not (
            z.valid_from and z.valid_from > now
        ) and distance <= MAX_TRIP_KM
        b = bearing_deg(lat, lon, c_lat, c_lon)
        out.append(
            PFZCandidate(
                zone=z,
                distance_km=distance,
                bearing_deg=round(b),
                compass=compass16(b),
                viable=viable,
                issues=issues,
            )
        )
    out.sort(key=lambda c: (not c.viable, c.distance_km))
    return out[:limit]
