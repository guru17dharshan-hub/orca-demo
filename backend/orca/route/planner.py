"""Risk-aware, time-dependent route planning (guide §21).

Cost function (explicit, so 'safer' has a defined meaning):
    edge_cost = travel_time_h × (1 + w[risk level at the time the boat reaches that edge])
    w: LOW 0 · MODERATE 1 · INSUFFICIENT_DATA 3 · HIGH and SEVERE = forbidden
Hard constraints (edge rejected): land, restricted/protected areas, crossing a
maritime boundary, HIGH or SEVERE risk at arrival time (HIGH is 'not advisable
for small fishing boats', so a recommended route never passes through it).

Weather is evaluated at the boat's estimated arrival time on each edge, so a
route can legitimately avoid a storm cell that is still passing — something a
static 'current conditions' map cannot do."""

from __future__ import annotations

import heapq
import math
from datetime import datetime, timedelta
from typing import Callable

from pydantic import BaseModel, Field

from ..geo.geofences import GeofenceIndex
from ..geo.geometry import KM_PER_NM, haversine_km
from ..geo.land import is_land
from ..models import Advisory, MarineObservation
from ..risk.engine import assess_hour
from ..risk.rules import RANK, RiskLevel
from ..state import advisories_at_point
from ..timeutil import IST, ensure_utc, floor_hour

RISK_WEIGHTS = {
    RiskLevel.LOW: 0.0,
    RiskLevel.MODERATE: 1.0,
    RiskLevel.INSUFFICIENT_DATA: 3.0,
}
FORBIDDEN_LEVELS = (RiskLevel.HIGH, RiskLevel.SEVERE)
COST_FUNCTION = (
    "edge cost = travel time × (1 + w), w by risk level at the boat's arrival time: LOW 0, MODERATE 1, "
    "INSUFFICIENT_DATA 3; HIGH and SEVERE conditions, land, restricted/protected areas and maritime-boundary "
    "crossings are forbidden"
)

ValuesFn = Callable[[float, float, datetime], dict[str, MarineObservation]]


class Waypoint(BaseModel):
    lat: float
    lon: float
    eta: datetime


class RouteSegment(BaseModel):
    start: tuple[float, float]
    end: tuple[float, float]
    eta_start: datetime
    eta_end: datetime
    distance_km: float
    level: RiskLevel
    dominant: str | None = None


class RiskSpan(BaseModel):
    start: datetime
    end: datetime
    level: RiskLevel
    dominant: str | None = None


class RoutePlan(BaseModel):
    kind: str  # recommended | direct
    feasible: bool
    waypoints: list[Waypoint]
    distance_km: float
    duration_h: float
    max_level: RiskLevel | None
    level_hours: dict[str, float]
    segments: list[RouteSegment]
    timeline: list[RiskSpan] = Field(default_factory=list)
    violations: list[str] = Field(default_factory=list)
    cost: float | None = None


class RouteComparison(BaseModel):
    departure: datetime
    speed_knots: float
    start: tuple[float, float]
    end: tuple[float, float]
    recommended: RoutePlan | None
    direct: RoutePlan
    reasons: list[str]
    cost_function: str = COST_FUNCTION
    simulated: bool = False


class RiskField:
    """Hourly risk on a regular sample grid with nearest-sample lookup.

    Samples are evaluated lazily and cached. If the nearest sample has no marine
    data (it falls on land at a harbour mouth), the nearest neighbouring sea sample
    is used — the same 'nearest sea cell' rule marine models apply."""

    def __init__(self, lat0: float, lon0: float, step: float, values_fn: ValuesFn, advisories: list[Advisory]) -> None:
        self.lat0, self.lon0, self.step = lat0, lon0, step
        self.values_fn = values_fn
        self.advisories = advisories
        self._cache: dict[tuple[int, int, datetime], tuple[RiskLevel, str | None, bool]] = {}
        self.simulated = False

    def _sample(self, i: int, j: int, hour: datetime) -> tuple[RiskLevel, str | None, bool]:
        key = (i, j, hour)
        if key not in self._cache:
            lat, lon = round(self.lat0 + i * self.step, 4), round(self.lon0 + j * self.step, 4)
            values = self.values_fn(lat, lon, hour)
            if any(o.data_type.value == "simulated" for o in values.values()):
                self.simulated = True
            has_marine = values.get("wave_height") is not None and values["wave_height"].value is not None
            h = assess_hour(hour, values, advisories_at_point(lat, lon, hour, self.advisories))
            self._cache[key] = (h.level, h.dominant.variable if h.dominant else None, has_marine)
        return self._cache[key]

    def level_at(self, lat: float, lon: float, t: datetime) -> tuple[RiskLevel, str | None]:
        hour = floor_hour(ensure_utc(t))
        fi, fj = (lat - self.lat0) / self.step, (lon - self.lon0) / self.step
        i, j = round(fi), round(fj)
        level, dominant, has_marine = self._sample(i, j, hour)
        if has_marine:
            return level, dominant
        neighbours = sorted(
            ((i + di, j + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1) if (di, dj) != (0, 0)),
            key=lambda ij: (ij[0] - fi) ** 2 + (ij[1] - fj) ** 2,
        )
        for ni, nj in neighbours:
            n_level, n_dom, n_marine = self._sample(ni, nj, hour)
            if n_marine:
                return n_level, n_dom
        return level, dominant


def route_bbox(start: tuple[float, float], end: tuple[float, float]) -> tuple[float, float, float, float]:
    span = max(abs(start[0] - end[0]), abs(start[1] - end[1]))
    margin = max(0.25, 0.35 * span)
    return (
        min(start[0], end[0]) - margin,
        max(start[0], end[0]) + margin,
        min(start[1], end[1]) - margin,
        max(start[1], end[1]) + margin,
    )


def sample_points(bbox: tuple[float, float, float, float], step: float) -> tuple[float, float, list[tuple[float, float]]]:
    lat_min, lat_max, lon_min, lon_max = bbox
    lat0, lon0 = math.floor(lat_min / step) * step, math.floor(lon_min / step) * step
    rows = int((lat_max - lat0) / step) + 2
    cols = int((lon_max - lon0) / step) + 2
    pts = [(round(lat0 + i * step, 4), round(lon0 + j * step, 4)) for i in range(rows) for j in range(cols)]
    return lat0, lon0, pts


def _is_sea(lat: float, lon: float) -> bool:
    return not is_land(lat, lon)


def _edge_on_sea(a: tuple[float, float], b: tuple[float, float], spacing_km: float = 1.0) -> bool:
    n = max(2, int(haversine_km(a[0], a[1], b[0], b[1]) / spacing_km))
    return all(_is_sea(a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n) for k in range(1, n))


class RoutePlanner:
    def __init__(self, geofences: GeofenceIndex, field: RiskField, departure: datetime, speed_kmh: float) -> None:
        self.geofences = geofences
        self.field = field
        self.departure = departure
        self.speed_kmh = speed_kmh

    # --- evaluation ------------------------------------------------------------------
    def _walk(self, pts: list[tuple[float, float]], t0: datetime, sample_km: float = 2.0):
        """Yield (a, b, eta_start, eta_end, level, dominant) for sub-segments of ≤ sample_km."""
        t = t0
        for a, b in zip(pts, pts[1:]):
            d = haversine_km(a[0], a[1], b[0], b[1])
            n = max(1, math.ceil(d / sample_km))
            for k in range(n):
                p = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
                q = (a[0] + (b[0] - a[0]) * (k + 1) / n, a[1] + (b[1] - a[1]) * (k + 1) / n)
                dt = timedelta(hours=(d / n) / self.speed_kmh)
                level, dom = self.field.level_at((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, t + dt / 2)
                yield p, q, t, t + dt, level, dom
                t += dt

    def summarize(self, kind: str, pts: list[tuple[float, float]], violations: list[str], feasible: bool) -> RoutePlan:
        waypoints = [Waypoint(lat=pts[0][0], lon=pts[0][1], eta=self.departure)]
        segments: list[RouteSegment] = []
        level_hours: dict[str, float] = {}
        worst: RiskLevel | None = None
        cost = 0.0
        timeline: list[RiskSpan] = []
        t = self.departure
        for a, b in zip(pts, pts[1:]):
            seg_levels = list(self._walk([a, b], t))
            for _, _, ts, te, lv, dom_v in seg_levels:
                if timeline and timeline[-1].level == lv:
                    timeline[-1].end = te
                else:
                    timeline.append(RiskSpan(start=ts, end=te, level=lv, dominant=dom_v))
            seg_worst = max((s[4] for s in seg_levels), key=lambda lv: RANK.get(lv, 1.5))
            dom = next((s[5] for s in seg_levels if s[4] == seg_worst), None)
            for _, _, ts, te, lv, _ in seg_levels:
                hrs = (te - ts).total_seconds() / 3600
                level_hours[lv.value] = round(level_hours.get(lv.value, 0.0) + hrs, 2)
                cost += hrs * (1 + RISK_WEIGHTS.get(lv, 100.0))
                if lv != RiskLevel.INSUFFICIENT_DATA and (worst is None or RANK[lv] > RANK[worst]):
                    worst = lv
            t_end = seg_levels[-1][3]
            segments.append(
                RouteSegment(
                    start=a, end=b, eta_start=t, eta_end=t_end,
                    distance_km=round(haversine_km(a[0], a[1], b[0], b[1]), 2), level=seg_worst, dominant=dom,
                )
            )
            waypoints.append(Waypoint(lat=b[0], lon=b[1], eta=t_end))
            t = t_end
        total = sum(s.distance_km for s in segments)
        # Same rule as the point risk engine: a data gap means ORCA cannot confirm the route is safe,
        # unless a known stretch is already HIGH or SEVERE.
        if level_hours.get(RiskLevel.INSUFFICIENT_DATA.value) and (worst is None or RANK[worst] < RANK[RiskLevel.HIGH]):
            worst = RiskLevel.INSUFFICIENT_DATA
        return RoutePlan(
            kind=kind, feasible=feasible, waypoints=waypoints, distance_km=round(total, 1),
            duration_h=round(total / self.speed_kmh, 2), max_level=worst, level_hours=level_hours,
            segments=segments, timeline=timeline, violations=violations, cost=round(cost, 3),
        )

    def direct(self, start, end) -> RoutePlan:
        violations = [f"Enters/crosses {f.name}: {f.rule}" for f in self.geofences.segment_violations(start, end, self.departure)]
        inner = (
            (start[0] + (end[0] - start[0]) * 0.05, start[1] + (end[1] - start[1]) * 0.05),
            (start[0] + (end[0] - start[0]) * 0.95, start[1] + (end[1] - start[1]) * 0.95),
        )
        if not _edge_on_sea(*inner):  # ignore harbour-mouth pixels at the very ends
            violations.append("Straight line crosses land")
        plan = self.summarize("direct", [start, end], violations, feasible=True)
        levels = {lv for *_, lv, _ in self._walk([start, end], self.departure)}
        for level in reversed(FORBIDDEN_LEVELS):
            if level in levels:
                plan.violations.append(f"Passes through {level.value} conditions")
        plan.feasible = not plan.violations
        return plan

    # --- search --------------------------------------------------------------------------
    def _edge_ok(self, a, b, t_mid) -> RiskLevel | None:
        level, _ = self.field.level_at((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, t_mid)
        if level in FORBIDDEN_LEVELS or self.geofences.segment_violations(a, b, t_mid):
            return None
        return level

    def recommended(self, start, end, max_hours: float, grid_step: float) -> RoutePlan | None:
        lat_min, lat_max, lon_min, lon_max = route_bbox(start, end)
        rows = int((lat_max - lat_min) / grid_step) + 1
        cols = int((lon_max - lon_min) / grid_step) + 1

        def node(ij):
            return (lat_min + ij[0] * grid_step, lon_min + ij[1] * grid_step)

        def nearest_index(p):
            return (min(rows - 1, max(0, round((p[0] - lat_min) / grid_step))),
                    min(cols - 1, max(0, round((p[1] - lon_min) / grid_step))))

        s_idx, e_idx = nearest_index(start), nearest_index(end)
        sea: dict = {}

        def usable(ij):
            if ij not in sea:
                sea[ij] = ij in (s_idx, e_idx) or _is_sea(*node(ij))
            return sea[ij]

        def point(ij):
            return start if ij == s_idx else end if ij == e_idx else node(ij)

        max_td = timedelta(hours=max_hours)
        open_heap = [(0.0, 0.0, s_idx)]
        best = {s_idx: 0.0}
        arrival_h = {s_idx: 0.0}
        parent: dict = {s_idx: None}
        steps = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
        while open_heap:
            _, cost, cur = heapq.heappop(open_heap)
            if cur == e_idx:
                break
            if cost > best.get(cur, math.inf) + 1e-9:
                continue
            a = point(cur)
            for di, dj in steps:
                nxt = (cur[0] + di, cur[1] + dj)
                if not (0 <= nxt[0] < rows and 0 <= nxt[1] < cols) or not usable(nxt):
                    continue
                b = point(nxt)
                if not _edge_on_sea(a, b, spacing_km=grid_step * 111 / 3):
                    if not (cur == s_idx or nxt == e_idx):
                        continue
                edge_h = haversine_km(a[0], a[1], b[0], b[1]) / self.speed_kmh
                t_mid = self.departure + timedelta(hours=arrival_h[cur] + edge_h / 2)
                if t_mid - self.departure > max_td:
                    continue
                level = self._edge_ok(a, b, t_mid)
                if level is None:
                    continue
                new_cost = cost + edge_h * (1.0 + RISK_WEIGHTS[level])
                if new_cost < best.get(nxt, math.inf) - 1e-9:
                    best[nxt] = new_cost
                    arrival_h[nxt] = arrival_h[cur] + edge_h
                    parent[nxt] = cur
                    h = haversine_km(b[0], b[1], end[0], end[1]) / self.speed_kmh
                    heapq.heappush(open_heap, (new_cost + h, new_cost, nxt))
        if e_idx not in parent:
            return None
        path, cur = [], e_idx
        while cur is not None:
            path.append(point(cur))
            cur = parent[cur]
        path.reverse()
        path = self._smooth(path)
        return self.summarize("recommended", [(round(a, 4), round(b, 4)) for a, b in path], [], True)

    def _smooth(self, path: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """String-pulling: replace zig-zags with straight legs when the shortcut stays on
        sea, violates nothing and costs no more than the grid path it replaces."""
        if len(path) <= 2:
            return path

        def leg_cost(pts, t0):
            total, worst = 0.0, RiskLevel.LOW
            for a, b, ts, te, lv, _ in self._walk(pts, t0):
                if lv in FORBIDDEN_LEVELS:
                    return math.inf, lv
                total += (te - ts).total_seconds() / 3600 * (1 + RISK_WEIGHTS[lv])
                if RANK.get(lv, 1.5) > RANK.get(worst, 1.5):
                    worst = lv
            return total, worst

        out = [path[0]]
        i = 0
        t = self.departure
        while i < len(path) - 1:
            j = len(path) - 1
            while j > i + 1:
                a, b = path[i], path[j]
                if _edge_on_sea(a, b) and not self.geofences.segment_violations(a, b, t):
                    short, _ = leg_cost([a, b], t)
                    orig, _ = leg_cost(path[i : j + 1], t)
                    if short <= orig + 1e-6:
                        break
                j -= 1
            out.append(path[j])
            t += timedelta(hours=haversine_km(*path[i], *path[j]) / self.speed_kmh)
            i = j
        return out


def _fmt_ist(dt: datetime) -> str:
    return ensure_utc(dt).astimezone(IST).strftime("%H:%M IST")


FACTOR_LABELS = {
    "wave_height": "wave height",
    "wind_speed": "wind",
    "weather_code": "thunderstorm",
    "visibility": "visibility",
    "advisory": "official warning",
}


def explain(direct: RoutePlan, rec: RoutePlan | None, speed_kmh: float) -> list[str]:
    reasons: list[str] = []
    if rec is None:
        reasons.append(
            "No route found that avoids HIGH/SEVERE conditions, land and restricted areas in the planning window — "
            "do not depart; wait for conditions to improve."
        )
        reasons.extend(f"Direct route: {v.rstrip('.')}." for v in direct.violations)
        return reasons
    for v in direct.violations:
        reasons.append(f"Direct route: {v.rstrip('.')}.")
    for level in (RiskLevel.SEVERE, RiskLevel.HIGH, RiskLevel.MODERATE):
        d_h = direct.level_hours.get(level.value, 0.0)
        r_h = rec.level_hours.get(level.value, 0.0)
        if d_h - r_h >= 0.2:
            first = next((span for span in direct.timeline if span.level == level), None)
            when = f" from about {_fmt_ist(first.start)}" if first else ""
            cause = f" ({FACTOR_LABELS.get(first.dominant, first.dominant)})" if first and first.dominant else ""
            reasons.append(
                f"Time in {level.value} conditions: direct {d_h:.1f} h{when}{cause} → recommended {r_h:.1f} h."
            )
            break
    extra_km = rec.distance_km - direct.distance_km
    if extra_km > 0.5:
        reasons.append(f"Detour adds {extra_km:.1f} km (~{extra_km / speed_kmh * 60:.0f} min at the given speed).")
    if len(reasons) == 0:
        reasons.append("The direct line is already the lowest-risk option under the cost function.")
    return reasons


async def plan_route(
    data_service,
    geofences: GeofenceIndex,
    start: tuple[float, float],
    end: tuple[float, float],
    departure: datetime,
    speed_knots: float = 8.0,
    advisories: list[Advisory] | None = None,
    marine_source: str = "replay",
    max_hours: float = 30.0,
) -> RouteComparison:
    departure = ensure_utc(departure)
    speed_kmh = speed_knots * KM_PER_NM
    bbox = route_bbox(start, end)
    span = max(bbox[1] - bbox[0], bbox[3] - bbox[2])
    grid_step = max(0.02, span / 50)
    # replay: analytic field, sample finely · historical: 0.25–0.5° model grids · live: few batched API points
    sample_step = {"replay": 0.04, "historical": 0.1}.get(marine_source, max(0.1, span / 10))
    lat0, lon0, pts = sample_points(bbox, sample_step)
    direct_h = haversine_km(*start, *end) / speed_kmh
    horizon = departure + timedelta(hours=min(max_hours, direct_h * 2.5 + 2))
    values_fn = await data_service.route_values(pts, floor_hour(departure), horizon, marine_source)
    field = RiskField(lat0, lon0, sample_step, values_fn, advisories or [])
    planner = RoutePlanner(geofences, field, departure, speed_kmh)
    direct = planner.direct(start, end)
    rec = planner.recommended(start, end, max_hours, grid_step)
    if rec is not None and direct.feasible and (direct.cost or 0) <= (rec.cost or 0) + 1e-6:
        rec = direct.model_copy(update={"kind": "recommended"})
    reasons = explain(direct, rec, speed_kmh)
    destination = geofences.check(end[0], end[1], departure)
    if destination.hard_constraints:
        reasons = [f"Destination: {c}" for c in destination.hard_constraints] + reasons
    return RouteComparison(
        departure=departure,
        speed_knots=speed_knots,
        start=start,
        end=end,
        recommended=rec,
        direct=direct,
        reasons=reasons,
        simulated=field.simulated,
    )
