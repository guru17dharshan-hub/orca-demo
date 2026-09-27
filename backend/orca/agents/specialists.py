"""Specialist agents (guide §16). Each returns (value, summary, sources) so the
orchestrator can record exactly what every agent contributed.

kind = 'tool-agent'          retrieves/normalizes data, returns evidence, never decides safety
kind = 'deterministic-engine' applies versioned rules/algorithms (risk, geofence, route)"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..data_service import DataStatus
from ..geo.geofences import GeofenceStatus
from ..geo.geometry import haversine_km
from ..geo.land import near_land
from ..geo.ports import nearest_port, snap_to_sea
from ..geo.regulations import fishing_ban_notice
from ..models import Advisory
from ..pfz import PFZCandidate, rank_zones
from ..risk import RiskLevel, assess_window
from ..risk.engine import RiskDecision, assess_hour
from ..route import RouteComparison, plan_route
from ..services import Services
from ..state import MarineState, advisories_at_point
from ..timeutil import IST, ensure_utc, floor_hour, hours_between
from ..variables import WEATHER_CODE_TEXT


@dataclass
class AgentResult:
    value: Any
    summary: str
    sources: list[str] = field(default_factory=list)


def _sources(state: MarineState) -> list[str]:
    return sorted({f"{s['source']} — {s['product']} ({s['data_type']})" for s in state.sources()})


# ---- data discovery ---------------------------------------------------------------
async def catalog_agent(svc: Services, lat: float, lon: float, start: datetime, end: datetime) -> AgentResult:
    """Which datasets can answer here and now (area, time span, published yet), incl. a live ISRO MOSDAC search."""
    from ..catalog import discover

    d = await discover(svc, lat, lon, start, end)
    covering = [s.name for s in d.covering()]
    summary = f"{len(covering)} of {len(d.sources)} sources cover {lat:.2f}N {lon:.2f}E"
    if d.isro:
        summary += "; ISRO MOSDAC: " + ", ".join(f"{i['granules']} {i['dataset']}" for i in d.isro)
    if d.gaps:
        summary += "; gaps: " + ", ".join(d.gaps)
    return AgentResult(d, summary, [f"{s.agency}: {s.name} [{s.status}]" for s in d.sources])


async def discover_marine_data(svc: Services, lat: float, lon: float, start: datetime, end: datetime) -> AgentResult:
    state, status = await svc.data.marine_state(lat, lon, floor_hour(start), end)
    summary = f"{len(state.observations)} observations for {len(state.times())} hours from {status.marine_source}"
    if status.fallback_reason:
        summary += f" (live source failed: {status.fallback_reason[:80]})"
    summary += f"; {len(state.advisories)} active advisories"
    return AgentResult((state, status), summary, _sources(state) + [f"advisories: {', '.join(status.advisory_sources) or 'none'}"])


# ---- weather & ocean ------------------------------------------------------------------
def _in_window(state: MarineState, start: datetime, end: datetime) -> list[datetime]:
    return [t for t in state.times() if floor_hour(start) <= t <= end]


def weather_agent(state: MarineState, start: datetime, end: datetime) -> AgentResult:
    hours = _in_window(state, start, end)

    def series(var: str) -> list[float]:
        return [v for t in hours if (v := state.value(t, var)) is not None]

    wind, gusts, vis, rain = series("wind_speed"), series("wind_gusts"), series("visibility"), series("precipitation")
    codes = [int(v) for v in series("weather_code")]
    thunder_hours = [t for t in hours if (c := state.value(t, "weather_code")) is not None and int(c) in (95, 96, 99)]
    value = {
        "max_wind_kmh": max(wind) if wind else None,
        "max_gust_kmh": max(gusts) if gusts else None,
        "min_visibility_m": min(vis) if vis else None,
        "total_rain_mm": round(sum(rain), 1) if rain else None,
        "weather": sorted({WEATHER_CODE_TEXT.get(c, f"code {c}") for c in codes}),
        "thunderstorm_hours": thunder_hours,
    }
    summary = f"max wind {value['max_wind_kmh']} km/h, gusts {value['max_gust_kmh']} km/h, thunderstorm hours {len(thunder_hours)}"
    return AgentResult(value, summary, _sources(state))


def _tide_extremes(state: MarineState, hours: list[datetime]) -> list[dict]:
    series = [(t, v) for t in hours if (v := state.value(t, "sea_level")) is not None]
    out = []
    for (t0, v0), (t1, v1), (t2, v2) in zip(series, series[1:], series[2:]):
        if v1 > v0 and v1 >= v2:
            out.append({"type": "high", "time": t1, "sea_level_m": v1})
        elif v1 < v0 and v1 <= v2:
            out.append({"type": "low", "time": t1, "sea_level_m": v1})
    return out


def ocean_agent(state: MarineState, start: datetime, end: datetime) -> AgentResult:
    hours = _in_window(state, start, end)

    def series(var: str) -> list[float]:
        return [v for t in hours if (v := state.value(t, var)) is not None]

    waves, swell, sst, cur = series("wave_height"), series("swell_wave_height"), series("sea_surface_temperature"), series("current_speed")
    first = hours[0] if hours else None
    value = {
        "now": {
            "time": first,
            "wave_height_m": state.value(first, "wave_height") if first else None,
            "wind_kmh": state.value(first, "wind_speed") if first else None,
            "sst_c": state.value(first, "sea_surface_temperature") if first else None,
            "sea_level_m": state.value(first, "sea_level") if first else None,
            "current_kmh": state.value(first, "current_speed") if first else None,
        },
        "max_wave_m": max(waves) if waves else None,
        "max_swell_m": max(swell) if swell else None,
        "sst_range_c": [min(sst), max(sst)] if sst else None,
        "max_current_kmh": max(cur) if cur else None,
        "tides": _tide_extremes(state, state.times()),
    }
    summary = f"max waves {value['max_wave_m']} m, SST {value['sst_range_c']}, {len(value['tides'])} tide turning points"
    return AgentResult(value, summary, _sources(state))


# ---- risk / geo / alerts ------------------------------------------------------------
def risk_engine(state: MarineState, start: datetime, end: datetime, now: datetime, constraints: list[str]) -> AgentResult:
    decision = assess_window(state, start, end, now, hard_constraints=constraints)
    changes = ", ".join(f"{c.to_level.value}@{ensure_utc(c.time).astimezone(IST):%H:%M}" for c in decision.change_points) or "none"
    return AgentResult(decision, f"{decision.risk_level.value} over {len(decision.hours)} h; changes: {changes}", [decision.rule_version])


def geospatial_engine(svc: Services, lat: float, lon: float, t: datetime, advisories: list[Advisory]) -> AgentResult:
    status: GeofenceStatus = svc.geofences.check(lat, lon, t, advisories)
    ban = fishing_ban_notice(lat, lon, t)
    value = {"geofence": status, "regulations": [ban] if ban else []}
    summary = f"{status.status}; {len(status.hits)} geofence hits" + (f"; fishing ban in effect" if ban and ban.in_effect else "")
    return AgentResult(value, summary, sorted({h.authority for h in status.hits}))


def alert_query(state: MarineState, start: datetime, end: datetime) -> AgentResult:
    covering = {a.id: a for t in [start, *hours_between(start, end)] for a in state.advisories_at(t)}
    containing = {a.id: a for a in state.advisories_containing_point()}
    nearby = [a for a in state.advisories if a.id not in containing]
    value = {"covering": list(covering.values()), "containing_point": list(containing.values()), "elsewhere": nearby}
    summary = f"{len(covering)} advisories cover this point in the window; {len(nearby)} elsewhere"
    return AgentResult(value, summary, sorted({a.source for a in state.advisories}))


# ---- PFZ -----------------------------------------------------------------------------
async def pfz_agent(svc: Services, lat: float, lon: float, now: datetime, marine_source: str, limit: int = 5) -> AgentResult:
    zones, provider, note = [], None, None
    if svc.pfz_live is not None and svc.mode != "replay":
        try:
            zones = await svc.pfz_live.zones(now)
            provider = svc.pfz_live.name
        except Exception as exc:  # official feed unreachable
            note = f"INCOIS PFZ feed unavailable ({type(exc).__name__})"
    if not zones and marine_source == "historical" and svc.pfz_historical is not None:
        # historical replay: candidate zones computed from the archived satellite SST and chlorophyll
        zones = await svc.pfz_historical.zones(now)
        provider = svc.pfz_historical.name
    elif not zones and (marine_source == "replay" or svc.mode in ("replay", "auto")):
        # auto mode: the official feed failed or is empty -> clearly-labelled DEMO zones
        zones = await svc.pfz_demo.zones(now)
        provider = svc.pfz_demo.name
    candidates: list[PFZCandidate] = rank_zones(lat, lon, zones, svc.geofences, now, limit=limit)
    value = {"candidates": candidates, "provider": provider, "note": note}
    top = candidates[0] if candidates else None
    summary = (
        f"{len(candidates)} zones from {provider}; nearest viable {top.zone.id} at {top.distance_km} km {top.compass}"
        if top else f"no zones ({note or 'none available'})"
    )
    return AgentResult(value, summary, [provider or "none"])


# ---- route -----------------------------------------------------------------------------
async def route_engine(
    svc: Services, start: tuple[float, float], end: tuple[float, float], departure: datetime, speed_knots: float,
    advisories: list[Advisory], marine_source: str,
) -> AgentResult:
    start = snap_to_sea(*start)
    comparison: RouteComparison = await plan_route(
        svc.data, svc.geofences, start, end, departure, speed_knots, advisories, marine_source if marine_source != "none" else "live"
    )
    rec = comparison.recommended
    summary = (
        f"recommended {rec.distance_km} km / {rec.duration_h} h, max {rec.max_level.value if rec.max_level else 'n/a'}; "
        f"direct {'feasible' if comparison.direct.feasible else 'infeasible'}"
        if rec else "no feasible route"
    )
    return AgentResult(comparison, summary, ["ORCA route engine (time-dependent A*)"])


# ---- EO: chlorophyll / SST hotspots --------------------------------------------------------
async def eo_hotspots(svc: Services, lat: float, lon: float, now: datetime, marine_source: str, radius_deg: float = 1.5) -> AgentResult:
    step = 0.25
    pts = [
        (round(lat + i * step, 3), round(lon + j * step, 3))
        for i in range(-int(radius_deg / step), int(radius_deg / step) + 1)
        for j in range(-int(radius_deg / step), int(radius_deg / step) + 1)
    ]
    t = floor_hour(now)
    lookup = await svc.data.route_values(pts, t, t, marine_source, variables=("sea_surface_temperature", "chlorophyll"))
    cells = {}
    for p in pts:
        vals = lookup(p[0], p[1], t)
        sst = vals.get("sea_surface_temperature")
        chl = vals.get("chlorophyll")
        if sst is not None and sst.value is not None:
            if marine_source == "historical" and near_land(p[0], p[1], 10.0):
                continue  # satellite chlorophyll is unreliable in turbid near-shore water
            cells[p] = {"sst": sst.value, "chl": chl.value if chl is not None else None, "data_type": sst.data_type.value}
    if not cells:
        return AgentResult({"available": False, "hotspots": [], "reason": "no SST data"}, "no SST data", [])
    if all(c["chl"] is None for c in cells.values()):
        reason = ("no satellite chlorophyll archive for this date (NOAA-20 VIIRS archive starts 2022)"
                  if marine_source == "historical" else "chlorophyll source not connected in live mode")
        return AgentResult({"available": False, "hotspots": [], "reason": reason}, "chlorophyll unavailable",
                           ["NOAA OISST (SST only)" if marine_source == "historical" else "Open-Meteo marine (SST only)"])
    # SST front strength: max absolute SST difference to 4-neighbours per 10 km
    for (a, b), c in cells.items():
        diffs = []
        for da, db in ((step, 0), (-step, 0), (0, step), (0, -step)):
            n = cells.get((round(a + da, 3), round(b + db, 3)))
            if n:
                diffs.append(abs(c["sst"] - n["sst"]) / (haversine_km(a, b, a + da, b + db) / 10))
        c["front"] = max(diffs) if diffs else 0.0
    chl_values = sorted(c["chl"] for c in cells.values() if c["chl"] is not None)
    chl_p90 = chl_values[int(0.9 * (len(chl_values) - 1))]
    front_p90 = sorted(c["front"] for c in cells.values())[int(0.9 * (len(cells) - 1))]
    ranked = sorted(
        ({"lat": a, "lon": b, **c, "sst_front": c["front"] >= front_p90 and c["front"] > 0} for (a, b), c in cells.items() if c["chl"] is not None and c["chl"] >= chl_p90),
        key=lambda x: -x["chl"],
    )[:6]
    for h in ranked:
        h["chl"], h["sst"], h["front"] = round(h["chl"], 2), round(h["sst"], 2), round(h["front"], 3)
    value = {
        "available": True,
        "hotspots": ranked,
        "method": "cells in the top 10% of chlorophyll within the scanned area; 'sst_front' marks the strongest 10% SST "
        "gradients (fronts are a standard PFZ indicator). Relative ranking — no absolute thresholds are assumed."
        + (" Cells within 10 km of land are skipped: satellite chlorophyll is unreliable in turbid near-shore water."
           if marine_source == "historical" else ""),
        "grid_step_deg": step,
        "chl_p90": round(chl_p90, 2),
        "data_type": next(iter(cells.values()))["data_type"],
    }
    return AgentResult(value, f"{len(ranked)} hotspot cells (chl ≥ {chl_p90:.2f} mg/m³)", [value["data_type"]])


# ---- ocean analytics: productivity change -------------------------------------------
def productivity_agent(svc: Services, lat: float, lon: float, marine_source: str) -> AgentResult:
    if marine_source == "historical" and svc.replay is not None:
        return historical_productivity(svc, lat, lon)
    if marine_source != "replay":
        return AgentResult({"available": False, "reason": "historical satellite series (SST/chlorophyll) not connected in live mode"},
                           "history unavailable", [])
    history = svc.scenario.daily_history(lat, lon, days=60)
    if len(history) < 30:
        return AgentResult({"available": False, "reason": "location is on land or outside the scenario"}, "no history", [])
    recent, earlier = history[-21:], history[:-21]
    sst_r = statistics.mean(h["sea_surface_temperature"] for h in recent)
    sst_e = statistics.mean(h["sea_surface_temperature"] for h in earlier)
    chl_r = statistics.mean(h["chlorophyll"] for h in recent)
    chl_e = statistics.mean(h["chlorophyll"] for h in earlier)
    value = {
        "available": True,
        "period_days": {"recent": 21, "earlier": len(earlier)},
        "sst_recent_c": round(sst_r, 2), "sst_earlier_c": round(sst_e, 2), "sst_change_c": round(sst_r - sst_e, 2),
        "chl_recent": round(chl_r, 3), "chl_earlier": round(chl_e, 3), "chl_change_pct": round((chl_r - chl_e) / chl_e * 100, 1),
        "series": history,
        "data_type": "simulated",
        "caveat": "Correlation only: fishing pressure, monsoon timing and market factors are not assessed.",
    }
    return AgentResult(value, f"SST {value['sst_change_c']:+.2f} °C, chlorophyll {value['chl_change_pct']:+.1f}% (last 21 d vs earlier)", ["ORCA simulated scenario (history)"])


def historical_productivity(svc: Services, lat: float, lon: float) -> AgentResult:
    """Real satellite SST record (NOAA OISST): recent vs earlier weeks, and the anomaly against the
    1971–2000 climatology. Warmer-than-normal, nutrient-poor surface water is a well-known cause
    of lower plankton and catches; the chlorophyll composite is reported where it exists."""
    arc = svc.replay.archive
    now = svc.clock()
    series = arc.sst_series(lat, lon, now)
    if len(series) < 21:
        return AgentResult({"available": False, "reason": "not enough archived SST days at this location"}, "no history", [])
    recent, earlier = series[-14:], series[:-14]
    sst_r = statistics.mean(s[1] for s in recent)
    sst_e = statistics.mean(s[1] for s in earlier)
    anom_r = statistics.mean(s[2] for s in recent)
    chl = arc.chl_at(lat, lon, now, radius_cells=6)
    value = {
        "available": True,
        "period_days": {"recent": len(recent), "earlier": len(earlier)},
        "sst_recent_c": round(sst_r, 2), "sst_earlier_c": round(sst_e, 2), "sst_change_c": round(sst_r - sst_e, 2),
        "sst_anomaly_c": round(anom_r, 2),
        "chl_recent": round(chl, 3) if chl is not None else None,
        "chl_earlier": None, "chl_change_pct": None,
        "series": [{"date": d.isoformat(), "sea_surface_temperature": round(v, 2), "anomaly": round(a, 2)} for d, v, a in series],
        "data_type": "observation",
        "source": "NOAA OISST v2.1 daily (AVHRR satellite + in-situ), anomaly vs 1971–2000",
        "caveat": "Correlation only: fishing pressure, monsoon timing and market factors are not assessed. "
                  "One chlorophyll composite exists per event, so no chlorophyll trend is claimed.",
    }
    return AgentResult(value, f"SST {value['sst_change_c']:+.2f} °C (last 14 d vs earlier), anomaly {anom_r:+.2f} °C",
                       ["NOAA OISST v2.1"])


# ---- zones to avoid ----------------------------------------------------------------------
async def avoid_agent(svc: Services, lat: float, lon: float, start: datetime, end: datetime, pfz: list[PFZCandidate],
                      advisories: list[Advisory], marine_source: str) -> AgentResult:
    items = []
    t_mid = start + (end - start) / 2
    lookup = await svc.data.route_values(
        [c.zone.centroid for c in pfz], floor_hour(start), end, marine_source
    ) if pfz else None
    for c in pfz:
        reasons = [i for i in c.issues if not i.startswith("not yet")]
        worst = None
        if lookup:
            for t in hours_between(start, end):
                vals = lookup(c.zone.centroid[0], c.zone.centroid[1], t)
                h = assess_hour(t, vals, advisories_at_point(*c.zone.centroid, t, advisories))
                if h.level in (RiskLevel.HIGH, RiskLevel.SEVERE) and (
                    worst is None or (h.level == RiskLevel.SEVERE and worst[0] != RiskLevel.SEVERE)
                ):
                    worst = (h.level, t, h.dominant.variable if h.dominant else None)
        if worst:
            reasons.append(f"{worst[0].value} risk from {ensure_utc(worst[1]).astimezone(IST):%H:%M} IST ({worst[2]})")
        if reasons:
            items.append({"type": "pfz", "id": c.zone.id, "name": c.zone.name, "lat": c.zone.centroid[0], "lon": c.zone.centroid[1], "reasons": reasons})
    for f in svc.geofences.static:
        if not f.active_on(t_mid) or f.kind == "boundary_line":
            continue
        d = min(haversine_km(lat, lon, p[0], p[1]) for p in f.coordinates)
        if d <= 150:
            items.append({"type": f.kind, "id": f.id, "name": f.name, "reasons": [f.rule, f"accuracy: {f.accuracy}"], "distance_km": round(d, 1)})
    for a in advisories:
        items.append({"type": "hazard", "id": a.id, "name": a.headline or a.event, "reasons": [f"{a.severity} — {a.source}"]})
    return AgentResult({"items": items}, f"{len(items)} zones/areas to avoid", [])


def nearest_harbour(lat: float, lon: float) -> tuple[str, tuple[float, float], float]:
    port, dist = nearest_port(lat, lon)
    return port.name, snap_to_sea(port.lat, port.lon), dist


def summarize_decision(decision: RiskDecision) -> dict:
    """Compact, UI-friendly view of a RiskDecision (the full object is also returned)."""
    return {
        "risk_level": decision.risk_level.value,
        "valid_from": decision.valid_from,
        "valid_until": decision.valid_until,
        "timeline": [
            {
                "time": h.time,
                "level": h.level.value,
                "dominant": h.dominant.variable if h.dominant else None,
                "factors": {f.variable: {"value": f.value, "unit": f.unit, "level": f.level.value, "label": f.label} for f in h.factors},
                "missing": h.missing,
            }
            for h in decision.hours
        ],
        "change_points": [
            {"time": c.time, "from": c.from_level.value, "to": c.to_level.value, "direction": c.direction,
             "cause": c.cause.model_dump(mode="json") if c.cause else None}
            for c in decision.change_points
        ],
        "go_windows": [w.model_dump(mode="json") for w in decision.go_windows],
        "caution_windows": [w.model_dump(mode="json") for w in decision.caution_windows],
        "key_factors": [f.model_dump(mode="json") for f in decision.key_factors],
        "advisories": decision.advisories,
        "uncertainty": decision.uncertainty,
        "hard_constraints": decision.hard_constraints,
        "rule_version": decision.rule_version,
        "evidence_ids": decision.evidence_ids,
    }


def window_label(start: datetime, end: datetime) -> str:
    s, e = ensure_utc(start).astimezone(IST), ensure_utc(end).astimezone(IST)
    if s.date() == e.date():
        return f"{s:%d %b %H:%M}–{e:%H:%M} IST"
    return f"{s:%d %b %H:%M} – {e:%d %b %H:%M} IST"


def ist_label(dt: datetime) -> str:
    return f"{ensure_utc(dt).astimezone(IST):%d %b %Y, %H:%M} IST"


def departure_default(now: datetime) -> datetime:
    return floor_hour(ensure_utc(now)) + timedelta(hours=1)


__all__ = [
    "AgentResult", "DataStatus", "alert_query", "avoid_agent", "discover_marine_data", "eo_hotspots",
    "geospatial_engine", "nearest_harbour", "ocean_agent", "pfz_agent", "productivity_agent", "risk_engine",
    "route_engine", "summarize_decision", "weather_agent", "window_label",
]
