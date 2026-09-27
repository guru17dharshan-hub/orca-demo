"""Geofencing (guide §20): point-in-polygon, approach warnings, boundary side tests.

Vessel position → inside restricted area?  → immediate warning (hard constraint)
               → beyond maritime boundary? → immediate warning (hard constraint)
               → within warn distance?     → early warning
Official advisory areas (IMD CAP / simulated) are treated as dynamic hazard zones."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from ..models import Advisory
from ..timeutil import IST, ensure_utc
from .geometry import (
    distance_to_line_km,
    distance_to_polygon_km,
    point_in_polygon,
    segment_crosses_line,
    segment_intersects_polygon,
)

DATA_FILE = Path(__file__).parent / "data" / "geofences.json"


class GeofenceFeature(BaseModel):
    id: str
    name: str
    kind: str  # boundary_line | restricted | protected_area | sensitive | hazard
    geometry: str  # line | polygon
    coordinates: list[tuple[float, float]]
    authority: str
    reference: str | None = None
    accuracy: str  # official | approximate | unverified-transcription | fictional-demo | official-advisory | simulated
    accuracy_note: str = ""
    warn_distance_km: float = 5.0
    rule: str = ""
    home_side: str | None = None  # for boundary lines: which side is home waters
    active_months: list[int] | None = None

    def active_on(self, t: datetime) -> bool:
        return self.active_months is None or ensure_utc(t).astimezone(IST).month in self.active_months


class GeofenceHit(BaseModel):
    feature_id: str
    name: str
    kind: str
    relation: str  # inside | beyond | approaching
    distance_km: float
    rule: str
    accuracy: str
    authority: str


class GeofenceStatus(BaseModel):
    lat: float
    lon: float
    status: str  # clear | approaching | inside_protected | inside_restricted | beyond_boundary
    hits: list[GeofenceHit] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)


SEVERITY_ORDER = ["clear", "approaching", "inside_protected", "inside_restricted", "beyond_boundary"]


def load_features(path: Path = DATA_FILE) -> list[GeofenceFeature]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [GeofenceFeature(**f) for f in data["features"]]


def advisory_features(advisories: list[Advisory]) -> list[GeofenceFeature]:
    out = []
    for adv in advisories:
        for i, poly in enumerate(adv.polygons):
            out.append(
                GeofenceFeature(
                    id=f"{adv.id}#{i}",
                    name=adv.headline or adv.event,
                    kind="hazard",
                    geometry="polygon",
                    coordinates=poly,
                    authority=adv.source,
                    reference=adv.reference,
                    accuracy="official-advisory" if adv.data_type.value == "official_advisory" else "simulated",
                    warn_distance_km=10.0,
                    rule=f"{adv.event} ({adv.severity}) in effect",
                )
            )
    return out


def _beyond_line(lat: float, lon: float, feature: GeofenceFeature) -> bool:
    """Side test against the nearest boundary segment. Lines are ordered north→south,
    so for home_side='west' the far side is where the 2-D cross product is positive."""
    coords = feature.coordinates
    lats = [c[0] for c in coords]
    if not (min(lats) - 0.3 <= lat <= max(lats) + 0.3) or distance_to_line_km(lat, lon, coords) > 80:
        return False
    best, best_d = None, float("inf")
    for a, b in zip(coords, coords[1:]):
        d = distance_to_line_km(lat, lon, [a, b])
        if d < best_d:
            best, best_d = (a, b), d
    (a_lat, a_lon), (b_lat, b_lon) = best
    cross = (b_lon - a_lon) * (lat - a_lat) - (b_lat - a_lat) * (lon - a_lon)
    return cross > 0 if feature.home_side == "west" else cross < 0


class GeofenceIndex:
    def __init__(self, features: list[GeofenceFeature] | None = None) -> None:
        self.static = features if features is not None else load_features()

    def features(self, advisories: list[Advisory] | None = None) -> list[GeofenceFeature]:
        return self.static + advisory_features(advisories or [])

    def check(self, lat: float, lon: float, t: datetime, advisories: list[Advisory] | None = None) -> GeofenceStatus:
        hits: list[GeofenceHit] = []
        constraints: list[str] = []
        status = "clear"

        def bump(new: str) -> None:
            nonlocal status
            if SEVERITY_ORDER.index(new) > SEVERITY_ORDER.index(status):
                status = new

        for f in self.features(advisories):
            if not f.active_on(t):
                continue
            base = dict(feature_id=f.id, name=f.name, kind=f.kind, rule=f.rule, accuracy=f.accuracy, authority=f.authority)
            if f.geometry == "line":
                dist = distance_to_line_km(lat, lon, f.coordinates)
                if _beyond_line(lat, lon, f):
                    hits.append(GeofenceHit(relation="beyond", distance_km=round(dist, 2), **base))
                    constraints.append(f"Beyond {f.name}: {f.rule}")
                    bump("beyond_boundary")
                elif dist <= f.warn_distance_km:
                    hits.append(GeofenceHit(relation="approaching", distance_km=round(dist, 2), **base))
                    bump("approaching")
                continue
            dist = distance_to_polygon_km(lat, lon, f.coordinates)
            if dist == 0.0:
                hits.append(GeofenceHit(relation="inside", distance_km=0.0, **base))
                if f.kind == "restricted":
                    constraints.append(f"Inside {f.name}: {f.rule}")
                    bump("inside_restricted")
                elif f.kind in ("protected_area", "sensitive"):
                    constraints.append(f"Inside {f.name}: {f.rule}")
                    bump("inside_protected")
                # hazard areas are scored by the risk engine via advisories, not constraints
            elif dist <= f.warn_distance_km and f.kind != "hazard":
                hits.append(GeofenceHit(relation="approaching", distance_km=round(dist, 2), **base))
                bump("approaching")
        return GeofenceStatus(lat=lat, lon=lon, status=status, hits=hits, hard_constraints=constraints)

    def segment_violations(self, a: tuple[float, float], b: tuple[float, float], t: datetime) -> list[GeofenceFeature]:
        """Static features a straight segment would violate (restricted/protected areas entered, boundary crossed)."""
        out = []
        for f in self.static:
            if not f.active_on(t):
                continue
            if f.geometry == "line":
                if segment_crosses_line(a, b, f.coordinates):
                    out.append(f)
            elif f.kind in ("restricted", "protected_area", "sensitive") and segment_intersects_polygon(a, b, f.coordinates):
                out.append(f)
        return out

    def forbidden_point(self, lat: float, lon: float, t: datetime) -> GeofenceFeature | None:
        for f in self.static:
            if not f.active_on(t):
                continue
            if f.geometry == "polygon" and f.kind in ("restricted", "protected_area", "sensitive"):
                if point_in_polygon(lat, lon, f.coordinates):
                    return f
            elif f.geometry == "line" and _beyond_line(lat, lon, f):
                return f
        return None
