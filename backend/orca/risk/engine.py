"""Deterministic risk engine + dynamic risk trajectory (guide §13, §15).

Evaluates every hour of the requested operating window — never only 'now' — and
reports when the risk changes and which factor caused it. The LLM never runs
here; it only receives the finished RiskDecision to explain."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from ..models import Advisory, DataType, MarineObservation
from ..state import MarineState
from ..timeutil import ensure_utc, hours_between
from .rules import (
    ADVISORY_REFERENCE,
    ADVISORY_SEVERITY,
    ADVISORY_SEVERITY_DEFAULT,
    LEAD_TIME_CONFIDENCE_HOURS,
    RANK,
    RULESET_VERSION,
    SCORED_RULES,
    THUNDERSTORM_RULE,
    RiskLevel,
)


class FactorAssessment(BaseModel):
    variable: str
    value: float | str | None
    unit: str | None = None
    level: RiskLevel
    label: str
    reference: str
    evidence_id: str | None = None
    data_type: DataType | None = None


class HourAssessment(BaseModel):
    time: datetime
    level: RiskLevel
    factors: list[FactorAssessment]
    dominant: FactorAssessment | None = None
    missing: list[str] = Field(default_factory=list)


class ChangePoint(BaseModel):
    time: datetime
    from_level: RiskLevel
    to_level: RiskLevel
    direction: str  # rising | easing | data
    cause: FactorAssessment | None = None


class Window(BaseModel):
    start: datetime
    end: datetime  # end of the last hour in the window (exclusive)
    hours: int
    max_level: RiskLevel


class RiskDecision(BaseModel):
    decision_id: str
    lat: float
    lon: float
    risk_level: RiskLevel
    valid_from: datetime
    valid_until: datetime
    hours: list[HourAssessment]
    change_points: list[ChangePoint]
    go_windows: list[Window]
    caution_windows: list[Window]
    worst_hour: HourAssessment | None
    key_factors: list[FactorAssessment]
    advisories: list[dict]
    evidence_ids: list[str]
    rule_version: str = RULESET_VERSION
    data_types: list[str]
    simulated: bool
    uncertainty: list[str]
    hard_constraints: list[str] = Field(default_factory=list)


def _rank(level: RiskLevel) -> int:
    return RANK.get(level, -1)


def _factor_from_obs(obs: MarineObservation, level: RiskLevel, label: str, reference: str) -> FactorAssessment:
    return FactorAssessment(
        variable=obs.variable,
        value=obs.value,
        unit=obs.unit,
        level=level,
        label=label,
        reference=reference,
        evidence_id=obs.id,
        data_type=obs.data_type,
    )


def assess_hour(time: datetime, values: dict[str, MarineObservation], advisories: list[Advisory]) -> HourAssessment:
    factors: list[FactorAssessment] = []
    missing: list[str] = []

    for rule in SCORED_RULES:
        obs = values.get(rule.variable)
        if obs is None or obs.value is None:
            if rule.required:
                missing.append(rule.variable)
            continue
        band = rule.classify(float(obs.value))
        factors.append(_factor_from_obs(obs, band.level, band.label, rule.reference))

    wx = values.get(THUNDERSTORM_RULE["variable"])
    if wx is not None and wx.value is not None and int(wx.value) in THUNDERSTORM_RULE["codes"]:
        factors.append(
            _factor_from_obs(wx, THUNDERSTORM_RULE["level"], THUNDERSTORM_RULE["label"], THUNDERSTORM_RULE["reference"])
        )

    for adv in advisories:
        if adv.data_type == DataType.DERIVED:  # model-derived watches are shown, not scored
            continue
        level = ADVISORY_SEVERITY.get(adv.severity, ADVISORY_SEVERITY_DEFAULT)
        factors.append(
            FactorAssessment(
                variable="advisory",
                value=adv.headline or adv.event,
                level=level,
                label=f"{adv.event} — {adv.severity} ({adv.source})",
                reference=ADVISORY_REFERENCE,
                evidence_id=adv.id,
                data_type=adv.data_type,
            )
        )

    dominant = None
    for f in factors:  # first factor at the worst level wins (rule order = priority)
        if dominant is None or _rank(f.level) > _rank(dominant.level):
            dominant = f
    worst = dominant.level if dominant else None

    if missing and (worst is None or _rank(worst) < _rank(RiskLevel.HIGH)):
        level = RiskLevel.INSUFFICIENT_DATA
    else:
        level = worst or RiskLevel.INSUFFICIENT_DATA
    return HourAssessment(time=time, level=level, factors=factors, dominant=dominant, missing=missing)


def _windows(hours: list[HourAssessment], allowed: set[RiskLevel], start: datetime, end: datetime) -> list[Window]:
    """Contiguous runs of allowed hours, clipped to the requested [start, end] period."""
    out: list[Window] = []
    run: list[HourAssessment] = []
    for h in hours + [None]:  # sentinel flushes the last run
        if h is not None and h.level in allowed:
            run.append(h)
            continue
        if run:
            max_level = max((r.level for r in run), key=_rank)
            w_start = max(run[0].time, start)
            w_end = min(run[-1].time + timedelta(hours=1), end)
            if w_end > w_start:
                out.append(Window(start=w_start, end=w_end, hours=len(run), max_level=max_level))
            run = []
    return out


def assess_window(
    state: MarineState,
    start: datetime,
    end: datetime,
    now: datetime,
    hard_constraints: list[str] | None = None,
) -> RiskDecision:
    """Evaluate the operating window hour by hour and fold into one decision."""
    start, end, now = ensure_utc(start), ensure_utc(end), ensure_utc(now)
    hours = [assess_hour(t, state.values_at(t), state.advisories_at(t)) for t in hours_between(start, end)]

    known = [h for h in hours if h.level != RiskLevel.INSUFFICIENT_DATA]
    worst_hour = max(known, key=lambda h: _rank(h.level)) if known else None
    has_gap = len(known) < len(hours)
    if worst_hour is None or (has_gap and _rank(worst_hour.level) < _rank(RiskLevel.HIGH)):
        overall = RiskLevel.INSUFFICIENT_DATA
    else:
        overall = worst_hour.level

    change_points: list[ChangePoint] = []
    for prev, cur in zip(hours, hours[1:]):
        if prev.level == cur.level:
            continue
        if RiskLevel.INSUFFICIENT_DATA in (prev.level, cur.level):
            direction, cause = "data", None
        elif _rank(cur.level) > _rank(prev.level):
            direction, cause = "rising", cur.dominant
        else:
            direction, cause = "easing", prev.dominant
        change_points.append(
            ChangePoint(time=cur.time, from_level=prev.level, to_level=cur.level, direction=direction, cause=cause)
        )

    key_factors = []
    if worst_hour:
        key_factors = sorted(
            (f for f in worst_hour.factors if _rank(f.level) >= _rank(RiskLevel.MODERATE)),
            key=lambda f: -_rank(f.level),
        ) or [f for f in worst_hour.factors if f.variable in ("wave_height", "wind_speed")]

    evidence_ids: list[str] = []
    for h in [hours[0] if hours else None, worst_hour] + [
        next((x for x in hours if x.time == cp.time), None) for cp in change_points
    ]:
        if h is None:
            continue
        for f in h.factors:
            if f.evidence_id and f.evidence_id not in evidence_ids:
                evidence_ids.append(f.evidence_id)

    advisories_in_window = {a.id: a for h in hours for a in state.advisories_at(h.time)}
    uncertainty: list[str] = []
    if hours and (hours[-1].time - now) > timedelta(hours=LEAD_TIME_CONFIDENCE_HOURS):
        uncertainty.append(f"Part of this window is more than {LEAD_TIME_CONFIDENCE_HOURS} h ahead — forecast confidence is lower.")
    types = state.data_types()
    if DataType.FORECAST in types:
        uncertainty.append("Wind and wave values are model forecasts, not measurements.")
    if DataType.SIMULATED in types:
        uncertainty.append("SIMULATED scenario data — for demonstration only, not real conditions.")
    if any(o.data_type == DataType.DERIVED and o.variable == "weather_code" for o in state.observations):
        uncertainty.append("Thunderstorm and rain are derived from model fields (lifted index, rain rate), not observed.")
    if has_gap:
        uncertainty.append("Some hours lack wave-height or wind data; ORCA cannot confirm those hours are safe.")

    return RiskDecision(
        decision_id=str(uuid.uuid4()),
        lat=state.lat,
        lon=state.lon,
        risk_level=overall,
        valid_from=hours[0].time if hours else start,
        valid_until=(hours[-1].time + timedelta(hours=1)) if hours else end,
        hours=hours,
        change_points=change_points,
        go_windows=_windows(hours, {RiskLevel.LOW}, start, end),
        caution_windows=_windows(hours, {RiskLevel.LOW, RiskLevel.MODERATE}, start, end),
        worst_hour=worst_hour,
        key_factors=key_factors,
        advisories=[
            {
                "id": a.id,
                "source": a.source,
                "event": a.event,
                "headline": a.headline,
                "severity": a.severity,
                "onset": a.onset,
                "expires": a.expires,
                "area": a.area_desc,
                "data_type": a.data_type.value,
                "reference": a.reference,
            }
            for a in advisories_in_window.values()
        ],
        evidence_ids=evidence_ids,
        data_types=sorted(t.value for t in types),
        simulated=DataType.SIMULATED in types,
        uncertainty=uncertainty,
        hard_constraints=hard_constraints or [],
    )
