"""ORCA Planner (guide §5, §14, §17): turns intent + entities + conversation
context into a dependency-ordered set of tasks for the specialist agents.

Resolution rules (guide §23):
  location: explicit coordinates > harbour named in the message > "there/it" or a
            safety follow-up → the zone chosen last turn > device GPS > context
  window:   explicit time words > previous window on a follow-up > now … +6 h"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from ..geo.ports import offshore_point
from ..services import Services
from ..timeutil import ensure_utc
from . import specialists as sp
from .context import ConversationState, Place
from .intent import ParsedMessage, TimeSpec, resolve_window

StepFn = Callable[[dict[str, Any]], Awaitable[sp.AgentResult]]

NEEDS_LOCATION = {"safety", "pfz", "conditions", "alerts", "hotspots", "route", "productivity", "avoid"}


@dataclass
class PlanStep:
    id: str
    agent: str
    kind: str
    action: str
    run: StepFn
    depends_on: list[str] = field(default_factory=list)
    optional: bool = False

    def describe(self) -> dict[str, Any]:
        return {"id": self.id, "agent": self.agent, "kind": self.kind, "action": self.action, "depends_on": self.depends_on}


@dataclass
class Plan:
    intents: list[str]
    place: Place | None
    window: tuple[datetime, datetime]
    steps: list[PlanStep]
    rationale: list[str]
    needs_location: bool = False
    speed_knots: float = 8.0
    safety_target: str = "place"  # place | top_pfz | selected_zone


def resolve_place(parsed: ParsedMessage, state: ConversationState, device: Place | None) -> tuple[Place | None, list[str]]:
    notes: list[str] = []
    if parsed.coordinates:
        lat, lon = parsed.coordinates
        notes.append("location from coordinates in the message")
        return Place(lat=lat, lon=lon, label=f"{lat:.2f}°N {lon:.2f}°E", source="coordinates"), notes
    if parsed.port:
        lat, lon = offshore_point(parsed.port.lat, parsed.port.lon)
        notes.append(f"location from harbour named in the message ({parsed.port.name}), evaluated ~10 km offshore")
        return Place(lat=lat, lon=lon, label=f"off {parsed.port.name}", source="port"), notes
    zone = state.selected_zone
    follow_up_on_zone = zone is not None and (
        parsed.refers_to_previous or ("pfz" in state.last_intents and parsed.primary_intent in ("safety", "conditions", "alerts"))
    )
    if follow_up_on_zone and parsed.primary_intent not in ("pfz", "avoid", "hotspots", "route"):
        notes.append(f"follow-up resolved to the zone from the previous answer ({zone.name})")
        return Place(lat=zone.lat, lon=zone.lon, label=zone.name, source="pfz"), notes
    if device:
        notes.append("location from the device")
        return device, notes
    if state.location:
        notes.append("location from conversation context")
        return state.location.model_copy(update={"source": "context"}), notes
    return None, notes


def resolve_time(parsed: ParsedMessage, state: ConversationState, now: datetime) -> tuple[tuple[datetime, datetime], list[str]]:
    if parsed.time.explicit:
        return resolve_window(parsed.time, now), ["time window from the message"]
    if state.window and state.window[1] > now and state.turns > 0:
        return state.window, ["time window carried over from the previous question"]
    return resolve_window(TimeSpec(), now), ["default window: now to +6 h"]


def build_plan(
    svc: Services,
    parsed: ParsedMessage,
    state: ConversationState,
    device: Place | None,
    now: datetime,
) -> Plan:
    now = ensure_utc(now)
    intents = parsed.intents or (["help"] if not parsed.text.strip() else ["safety"])
    place, place_notes = resolve_place(parsed, state, device)
    window, time_notes = resolve_time(parsed, state, now)
    speed = parsed.speed_knots or state.speed_knots
    rationale = [f"intents: {', '.join(intents)} (via {parsed.source})", *place_notes, *time_notes]
    steps: list[PlanStep] = []
    plan = Plan(intents=intents, place=place, window=window, steps=steps, rationale=rationale, speed_knots=speed)

    if any(i in NEEDS_LOCATION for i in intents) and place is None:
        plan.needs_location = True
        rationale.append("no location available → ask the user")
        return plan
    if place is None:
        return plan

    start, end = window
    wants = set(intents)
    safety_at_zone = "safety" in wants and "pfz" in wants and place.source != "pfz"

    def add(step: PlanStep) -> None:
        if all(s.id != step.id for s in steps):
            steps.append(step)

    def data_step(step_id: str, lat_lon: Callable[[dict], tuple[float, float]], s: datetime, e: datetime, deps=()) -> None:
        async def catalog_run(r: dict) -> sp.AgentResult:
            lat, lon = lat_lon(r)
            return await sp.catalog_agent(svc, lat, lon, s, e)

        async def run(r: dict) -> sp.AgentResult:
            lat, lon = lat_lon(r)
            return await sp.discover_marine_data(svc, lat, lon, s, e)
        add(PlanStep("catalog", "data-catalog-agent", "tool-agent", "discover which datasets cover this place and time (incl. ISRO MOSDAC search)",
                     catalog_run, list(deps)))
        add(PlanStep(step_id, "marine-data-agent", "tool-agent", "retrieve weather, ocean and advisory data for the window", run, [*deps, "catalog"]))

    here = lambda r: (place.lat, place.lon)  # noqa: E731

    if wants & {"pfz", "avoid", "route"} or safety_at_zone:
        async def pfz_run(r: dict) -> sp.AgentResult:
            return await sp.pfz_agent(svc, place.lat, place.lon, now, _marine_source(svc, r))
        add(PlanStep("pfz", "pfz-agent", "tool-agent", "find and rank valid Potential Fishing Zones", pfz_run))

    if "safety" in wants or "conditions" in wants or "alerts" in wants or "avoid" in wants or "route" in wants:
        if safety_at_zone:
            plan.safety_target = "top_pfz"
            rationale.append("safety evaluated at the nearest viable fishing zone")
            target = lambda r: _top_zone_latlon(r) or (place.lat, place.lon)  # noqa: E731
            data_step("data", target, start, end, deps=["pfz"])
        else:
            c_start, c_end = (now, now + timedelta(hours=6)) if wants == {"conditions"} and not parsed.time.explicit else (start, end)
            if wants == {"conditions"} and not parsed.time.explicit:
                plan.window = (c_start, c_end)
            data_step("data", here, c_start, max(c_end, end))

        async def weather_run(r):
            state_, _ = r["data"].value
            return sp.weather_agent(state_, *plan.window)

        async def ocean_run(r):
            state_, _ = r["data"].value
            return sp.ocean_agent(state_, *plan.window)

        async def geo_run(r):
            state_, _ = r["data"].value
            return sp.geospatial_engine(svc, state_.lat, state_.lon, plan.window[0], state_.advisories)

        async def risk_run(r):
            state_, _ = r["data"].value
            constraints = r["geo"].value["geofence"].hard_constraints if "geo" in r else []
            banned = [n for n in r["geo"].value["regulations"] if n.in_effect] if "geo" in r else []
            constraints = constraints + [f"{n.title} ({n.period}): {n.applies_to}" for n in banned]
            return sp.risk_engine(state_, *plan.window, now, constraints)

        async def alerts_run(r):
            state_, _ = r["data"].value
            return sp.alert_query(state_, *plan.window)

        add(PlanStep("weather", "weather-agent", "tool-agent", "summarize wind, gusts, thunderstorms, visibility", weather_run, ["data"]))
        add(PlanStep("ocean", "ocean-agent", "tool-agent", "summarize waves, swell, SST, currents and tides", ocean_run, ["data"]))
        add(PlanStep("geo", "geospatial-engine", "deterministic-engine", "geofence, boundary and fishing-ban checks", geo_run, ["data"]))
        add(PlanStep("risk", "risk-engine", "deterministic-engine", "hour-by-hour risk trajectory (versioned rules)", risk_run, ["data", "geo"]))
        if "alerts" in wants or "safety" in wants:
            add(PlanStep("alerts", "alert-engine", "deterministic-engine", "official/simulated warnings covering the point", alerts_run, ["data"]))

    if "route" in wants:
        async def route_run(r: dict) -> sp.AgentResult:
            state_, status = r["data"].value
            origin, destination = _route_endpoints(parsed, state, place, r)
            if destination is None:
                return sp.AgentResult(None, "no destination: no fishing zone or place found", [])
            departure = max(plan.window[0], now)
            return await sp.route_engine(svc, origin, destination, departure, speed, state_.advisories, status.marine_source)
        deps = ["data", "pfz"] if any(s.id == "pfz" for s in steps) else ["data"]
        add(PlanStep("route", "route-engine", "deterministic-engine", "time-dependent risk-aware route vs direct line", route_run, deps))

    if wants & {"hotspots", "productivity"} and all(s.id != "data" for s in steps):
        data_step("data", here, now, now + timedelta(hours=1))  # establishes which data source is live

    if "hotspots" in wants:
        async def eo_run(r: dict) -> sp.AgentResult:
            return await sp.eo_hotspots(svc, place.lat, place.lon, now, _marine_source(svc, r))
        add(PlanStep("eo", "eo-satellite-agent", "tool-agent", "scan chlorophyll and SST fronts around the location", eo_run, ["data"]))

    if "productivity" in wants:
        async def prod_run(r: dict) -> sp.AgentResult:
            return sp.productivity_agent(svc, place.lat, place.lon, _marine_source(svc, r))
        add(PlanStep("productivity", "ocean-analytics-agent", "tool-agent", "compare recent vs earlier SST and chlorophyll", prod_run, ["data"]))

    if "avoid" in wants:
        async def avoid_run(r: dict) -> sp.AgentResult:
            state_, status = r["data"].value
            cands = r["pfz"].value["candidates"] if "pfz" in r else []
            return await sp.avoid_agent(svc, place.lat, place.lon, *plan.window, cands, state_.advisories, status.marine_source)
        add(PlanStep("avoid", "risk-engine", "deterministic-engine", "zones to avoid: hazards, geofences, high-risk PFZs", avoid_run, ["data", "pfz"]))

    return plan


def _marine_source(svc: Services, results: dict) -> str:
    if "data" in results:
        return results["data"].value[1].marine_source
    return svc.offline_source or "live"


def _top_zone_latlon(results: dict) -> tuple[float, float] | None:
    pfz = results.get("pfz")
    if not pfz:
        return None
    viable = [c for c in pfz.value["candidates"] if c.viable]
    return viable[0].zone.centroid if viable else None


def _route_endpoints(parsed: ParsedMessage, state: ConversationState, place: Place, results: dict):
    """Origin: harbour named in the message, else nearest harbour to the user.
    Destination: coordinates in the message, else the zone from the previous answer, else nearest viable PFZ."""
    if parsed.port is not None:
        origin = (parsed.port.lat, parsed.port.lon)
    else:
        _, origin, _ = sp.nearest_harbour(place.lat, place.lon)
    if parsed.coordinates and parsed.port is not None:
        return origin, parsed.coordinates
    if state.selected_zone is not None and (parsed.refers_to_previous or "pfz" in state.last_intents):
        return origin, (state.selected_zone.lat, state.selected_zone.lon)
    top = _top_zone_latlon(results)
    return origin, top
