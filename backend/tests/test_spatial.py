import asyncio
from datetime import datetime, timedelta

import httpx
import pytest

from orca.adapters.advisories import ScenarioAdvisoryAdapter
from orca.data_service import DataService
from orca.geo.geofences import GeofenceIndex
from orca.geo.geometry import bearing_deg, compass16, haversine_km, point_in_polygon
from orca.geo.land import is_land
from orca.geo.ports import find_port_in_text, snap_to_sea
from orca.geo.regulations import fishing_ban_notice
from orca.pfz import DemoPFZProvider, INCOISPFZProvider, parse_pfz_geojson, rank_zones
from orca.risk import RiskLevel
from orca.route import plan_route
from orca.timeutil import UTC

from .conftest import NOW, ist, tomorrow

GEOFENCES = GeofenceIndex()
T = tomorrow(6)


# --- geometry ---------------------------------------------------------------------
def test_haversine_and_bearing():
    assert haversine_km(15.0, 73.0, 16.0, 73.0) == pytest.approx(111.2, abs=0.5)
    assert compass16(bearing_deg(15.0, 73.0, 16.0, 74.0)) in ("NE", "NNE")


def test_point_in_polygon_square():
    sq = [(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]
    assert point_in_polygon(0.5, 0.5, sq) and not point_in_polygon(1.5, 0.5, sq)


def test_fast_land_mask():
    assert is_land(15.4, 74.0) and not is_land(15.2, 72.8)


# --- geofencing -------------------------------------------------------------------
@pytest.mark.parametrize(
    "lat,lon,status",
    [
        (9.60, 79.20, "clear"),  # Palk Bay, Indian side
        (9.55, 79.40, "approaching"),  # within 5 NM of the boundary
        (9.40, 79.60, "beyond_boundary"),  # Sri Lankan side
        (8.30, 79.20, "beyond_boundary"),  # Gulf of Mannar, far side
        (15.25, 73.20, "inside_restricted"),  # fictional demo zone
        (16.05, 73.45, "inside_protected"),  # Malvan (approximate)
        (15.20, 72.80, "clear"),
    ],
)
def test_geofence_status(lat, lon, status):
    assert GEOFENCES.check(lat, lon, T).status == status


def test_boundary_hit_carries_accuracy_label():
    s = GEOFENCES.check(9.40, 79.60, T)
    assert s.hard_constraints
    assert s.hits[0].accuracy == "official"  # turning points checked against the 1974/1976 treaty texts
    assert "treaty" in s.hits[0].authority.lower() or "agreement" in s.hits[0].authority.lower()


def test_bay_of_bengal_boundaries_have_the_right_home_side():
    assert GEOFENCES.check(10.3, 81.8, T).status == "beyond_boundary"  # Sri Lankan side of the 1976 line
    assert GEOFENCES.check(11.8, 81.8, T).status in ("clear", "approaching")
    assert GEOFENCES.check(20.0, 89.8, T).status == "beyond_boundary"  # east of the 2014 India–Bangladesh line
    assert GEOFENCES.check(21.3, 88.2, T).status in ("clear", "approaching")  # off Digha


def test_seasonal_sensitive_zone_only_active_in_season():
    in_zone = (20.60, 87.05)
    assert GEOFENCES.check(*in_zone, T).status == "clear"  # September: not nesting season
    assert GEOFENCES.check(*in_zone, datetime(2026, 1, 10, tzinfo=UTC)).status == "inside_protected"


def test_advisory_area_is_dynamic_hazard_zone(scenario):
    advisories = scenario.advisories(NOW + timedelta(hours=4))
    s = GEOFENCES.check(15.2, 72.8, T, advisories)
    assert any(h.kind == "hazard" and h.relation == "inside" for h in s.hits)
    assert not s.hard_constraints  # hazards are scored by the risk engine, not constraints


def test_fishing_ban_notice():
    west = fishing_ban_notice(15.2, 72.8, datetime(2026, 6, 15, tzinfo=UTC))
    assert west.in_effect and "west" in west.id
    assert not fishing_ban_notice(15.2, 72.8, T).in_effect
    assert fishing_ban_notice(13.1, 80.4, datetime(2026, 5, 1, tzinfo=UTC)).in_effect  # east coast


def test_port_lookup_in_regional_scripts():
    assert find_port_in_text("is it safe near ராமேஸ்வரம்?").id == "rameswaram"
    assert find_port_in_text("రేపు కాకినాడ దగ్గర").id == "kakinada"
    assert find_port_in_text("मुंबई से कल सुबह").id == "mumbai"
    assert find_port_in_text("sea near Vizag tomorrow").id == "visakhapatnam"
    lat, lon = snap_to_sea(15.41, 73.79)
    assert not is_land(lat, lon)


# --- PFZ ----------------------------------------------------------------------------
def test_demo_pfz_nearest_and_labelled(scenario):
    zones = asyncio.run(DemoPFZProvider(scenario).zones(NOW))
    assert all("NOT an INCOIS" in z.source and z.data_type.value == "simulated" for z in zones)
    ranked = rank_zones(15.41, 73.79, zones, GEOFENCES, NOW)
    assert ranked[0].zone.id == "DEMO-PFZ-01"
    assert ranked[0].viable and ranked[0].distance_km < 50
    assert ranked == sorted(ranked, key=lambda c: (not c.viable, c.distance_km))


def test_pfz_near_boundary_is_flagged(scenario):
    zones = asyncio.run(DemoPFZProvider(scenario).zones(NOW))
    ranked = rank_zones(9.29, 79.31, zones, GEOFENCES, NOW, limit=10)
    palk = next(c for c in ranked if c.zone.id == "DEMO-PFZ-10")
    assert any("maritime boundary" in issue for issue in palk.issues)


def test_expired_pfz_is_dropped(scenario):
    zones = asyncio.run(DemoPFZProvider(scenario).zones(NOW))
    assert rank_zones(15.41, 73.79, zones, GEOFENCES, NOW + timedelta(days=3)) == []


def test_incois_pfz_geojson_parsing_and_failure():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {"id": "pfz.1", "geometry": {"type": "LineString", "coordinates": [[72.5, 15.0], [72.7, 15.2], [72.9, 15.4]]},
             "properties": {"sector": "Goa"}},
            {"id": "pfz.2", "geometry": {"type": "Point", "coordinates": [72.5, 15.0]}},
        ],
    }
    zones = parse_pfz_geojson(fc, NOW)
    assert len(zones) == 1 and zones[0].data_type.value == "official_advisory" and zones[0].centroid == (15.2, 72.7)
    failing = INCOISPFZProvider(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(failing.zones(NOW))
    assert failing.health().status == "unavailable"


# --- routing ------------------------------------------------------------------------
@pytest.fixture
def svc(replay):
    return DataService("replay", replay, replay, [], [ScenarioAdvisoryAdapter(replay.scenario)], clock=lambda: NOW)


GOA = snap_to_sea(15.41, 73.79)


@pytest.mark.parametrize("hour", [6, 11])  # 06:00 thunderstorm over the destination · 11:00 wind rising on arrival
def test_route_never_recommends_high_risk(svc, scenario, hour):
    r = asyncio.run(plan_route(svc, GEOFENCES, GOA, (15.45, 73.35), tomorrow(hour), 8.0, scenario.advisories(NOW)))
    assert r.simulated
    assert r.direct.level_hours.get("HIGH", 0) > 0
    assert not r.direct.feasible and "Passes through HIGH conditions" in r.direct.violations
    assert r.recommended is None
    assert r.reasons[0].startswith("No route found")


def test_route_calm_trip_keeps_direct_line(svc, scenario):
    r = asyncio.run(plan_route(svc, GEOFENCES, GOA, (15.45, 73.35), ist(24, 14), 8.0, scenario.advisories(NOW)))
    assert r.direct.feasible and r.recommended.distance_km == r.direct.distance_km
    assert all(not is_land(w.lat, w.lon) for w in r.recommended.waypoints[1:-1])


def test_route_avoids_restricted_area(svc, scenario):
    r = asyncio.run(plan_route(svc, GEOFENCES, GOA, (15.05, 72.55), ist(24, 14), 8.0, scenario.advisories(NOW)))
    assert not r.direct.feasible and any("restricted" in v for v in r.direct.violations)
    rec = r.recommended
    assert rec and rec.feasible
    for a, b in zip(rec.waypoints, rec.waypoints[1:]):
        assert not GEOFENCES.segment_violations((a.lat, a.lon), (b.lat, b.lon), ist(24, 14))


def test_route_refuses_when_only_severe_paths_exist(svc, scenario):
    r = asyncio.run(plan_route(svc, GEOFENCES, GOA, (15.05, 72.55), tomorrow(4), 8.0, scenario.advisories(NOW)))
    assert r.recommended is None
    assert r.direct.max_level == RiskLevel.SEVERE
    assert r.reasons[0].startswith("No route found")


def test_route_never_crosses_maritime_boundary(svc):
    # Rameswaram towards a point beyond the boundary: every path must cross it, so no plan is offered.
    start = snap_to_sea(9.29, 79.31)
    r = asyncio.run(plan_route(svc, GEOFENCES, start, (9.45, 79.75), tomorrow(6), 8.0, []))
    assert any("boundary" in v for v in r.direct.violations)
    assert r.recommended is None
    assert r.reasons[0].startswith("Destination: Beyond India–Sri Lanka maritime boundary")


class _FixedField:
    """Risk field stub: LOW south of 15.05°N, `far_level` north of it."""

    def __init__(self, far_level):
        self.far_level = far_level

    def level_at(self, lat, lon, t):
        return (RiskLevel.LOW, "wave_height") if lat < 15.05 else (self.far_level, None)


@pytest.mark.parametrize("far_level,expected", [
    (RiskLevel.INSUFFICIENT_DATA, RiskLevel.INSUFFICIENT_DATA),  # a data gap is never reported as LOW
    (RiskLevel.LOW, RiskLevel.LOW),
])
def test_route_max_level_does_not_hide_data_gaps(far_level, expected):
    from orca.route.planner import RoutePlanner

    planner = RoutePlanner(GeofenceIndex([]), _FixedField(far_level), tomorrow(6), 15.0)
    plan = planner.summarize("direct", [(15.0, 72.5), (15.5, 72.5)], [], True)
    assert plan.max_level == expected


def test_zone_beyond_trip_range_is_not_viable(scenario):
    zones = asyncio.run(DemoPFZProvider(scenario).zones(NOW))
    near = rank_zones(15.4, 73.7, zones, GEOFENCES, NOW, limit=20)  # the demo zones are off Goa
    assert any(c.viable for c in near)
    far = rank_zones(11.6, 92.7, zones, GEOFENCES, NOW, limit=20)  # off Port Blair every demo zone is across the sea
    assert far and not any(c.viable for c in far)
    assert all("beyond a fishing trip's range" in " ".join(c.issues) for c in far)
