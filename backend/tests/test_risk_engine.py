import asyncio
from datetime import timedelta

import pytest

from orca.adapters.base import PointQuery
from orca.models import Advisory, DataType, MarineObservation
from orca.risk import RiskLevel, assess_hour, assess_window, rules_table
from orca.risk.rules import VISIBILITY_RULE, WAVE_RULE, WIND_RULE
from orca.state import MarineState
from orca.timeutil import fmt_ist_hour

from .conftest import NOW, tomorrow


def obs(var: str, value, unit: str = "", t=None) -> MarineObservation:
    t = t or tomorrow(6)
    return MarineObservation(
        id=f"t:{var}:{t:%H}",
        source="test",
        source_product="test",
        lat=15.2,
        lon=72.8,
        retrieved_at=NOW,
        valid_time=t,
        data_type=DataType.FORECAST,
        variable=var,
        value=value,
        unit=unit,
        processing_version="test",
    )


@pytest.mark.parametrize(
    "rule,value,level",
    [
        (WAVE_RULE, 0.0, RiskLevel.LOW),
        (WAVE_RULE, 1.24, RiskLevel.LOW),
        (WAVE_RULE, 1.25, RiskLevel.MODERATE),
        (WAVE_RULE, 2.5, RiskLevel.HIGH),
        (WAVE_RULE, 4.0, RiskLevel.SEVERE),
        (WIND_RULE, 28.9, RiskLevel.LOW),
        (WIND_RULE, 29.0, RiskLevel.MODERATE),
        (WIND_RULE, 39.0, RiskLevel.HIGH),
        (WIND_RULE, 62.0, RiskLevel.SEVERE),
        (VISIBILITY_RULE, 800, RiskLevel.HIGH),
        (VISIBILITY_RULE, 2000, RiskLevel.MODERATE),
        (VISIBILITY_RULE, 10000, RiskLevel.LOW),
    ],
)
def test_band_boundaries(rule, value, level):
    assert rule.classify(value).level == level


def test_hour_is_worst_factor():
    h = assess_hour(tomorrow(6), {"wave_height": obs("wave_height", 1.0), "wind_speed": obs("wind_speed", 45.0)}, [])
    assert h.level == RiskLevel.HIGH
    assert h.dominant.variable == "wind_speed"


def test_missing_required_data_is_insufficient_not_safe():
    h = assess_hour(tomorrow(6), {"wind_speed": obs("wind_speed", 10.0)}, [])
    assert h.level == RiskLevel.INSUFFICIENT_DATA
    assert h.missing == ["wave_height"]


def test_missing_data_does_not_hide_known_danger():
    h = assess_hour(tomorrow(6), {"wind_speed": obs("wind_speed", 70.0)}, [])
    assert h.level == RiskLevel.SEVERE


def test_thunderstorm_code_is_high():
    values = {"wave_height": obs("wave_height", 0.5), "wind_speed": obs("wind_speed", 10.0), "weather_code": obs("weather_code", 95)}
    assert assess_hour(tomorrow(6), values, []).level == RiskLevel.HIGH


def test_official_advisory_severity_maps_to_level():
    adv = Advisory(
        id="adv1", source="IMD", data_type=DataType.OFFICIAL_ADVISORY, event="Cyclone", headline="Cyclone warning",
        severity="Extreme", retrieved_at=NOW,
    )
    values = {"wave_height": obs("wave_height", 0.5), "wind_speed": obs("wind_speed", 10.0)}
    h = assess_hour(tomorrow(6), values, [adv])
    assert h.level == RiskLevel.SEVERE
    assert h.dominant.evidence_id == "adv1"


def test_official_advisory_with_unknown_severity_is_still_scored():
    adv = Advisory(
        id="adv2", source="IMD", data_type=DataType.OFFICIAL_ADVISORY, event="Fishermen warning", headline="Do not venture",
        severity="Unknown", retrieved_at=NOW,
    )
    values = {"wave_height": obs("wave_height", 0.5), "wind_speed": obs("wind_speed", 10.0)}
    h = assess_hour(tomorrow(6), values, [adv])
    assert h.level == RiskLevel.MODERATE
    assert h.dominant.evidence_id == "adv2"


def _golden_decision(replay, start_hour=6, end_hour=12, now=NOW):
    q = PointQuery(15.2, 72.8, tomorrow(start_hour), tomorrow(end_hour))
    observations = asyncio.run(replay.observe(q))
    state = MarineState(15.2, 72.8, observations, replay.scenario.advisories(now))
    return assess_window(state, q.start, q.end, now=now)


def test_golden_journey_trajectory_escalates(replay):
    """Guide §18: LOW at dawn → MODERATE → HIGH for 'tomorrow morning from 15.2,72.8'.

    Model output is on UTC hours, i.e. hh:30 in IST."""
    d = _golden_decision(replay)
    by_hour = {fmt_ist_hour(h.time): h.level for h in d.hours}
    assert by_hour["06:30"] == RiskLevel.LOW
    assert by_hour["08:30"] == RiskLevel.MODERATE
    assert by_hour["10:30"] == RiskLevel.HIGH
    assert d.risk_level == RiskLevel.HIGH
    rising = [c for c in d.change_points if c.direction == "rising"]
    assert [c.to_level for c in rising][:2] == [RiskLevel.MODERATE, RiskLevel.HIGH]
    assert rising[0].cause is not None
    assert d.go_windows and fmt_ist_hour(d.go_windows[0].start) == "06:00"  # clipped to the requested start
    assert d.simulated and "simulated" in d.data_types
    assert any("SIMULATED" in u for u in d.uncertainty)


def test_simulated_advisory_applies_after_issue_inside_area(replay):
    before_issue = _golden_decision(replay, 10, 12)
    assert not before_issue.advisories
    after_issue = _golden_decision(replay, 10, 12, now=NOW + timedelta(hours=4))  # issued 14:00 IST today
    assert any(f.variable == "advisory" for h in after_issue.hours for f in h.factors)
    assert all(a["data_type"] == "simulated" for a in after_issue.advisories)


def test_evidence_ids_resolve(replay):
    q = PointQuery(15.2, 72.8, tomorrow(6), tomorrow(12))
    observations = asyncio.run(replay.observe(q))
    state = MarineState(15.2, 72.8, observations, replay.scenario.advisories(NOW))
    d = assess_window(state, q.start, q.end, now=NOW)
    index = state.evidence_index()
    assert d.evidence_ids and all(e in index for e in d.evidence_ids)


def test_lead_time_uncertainty_flag(replay):
    q = PointQuery(15.2, 72.8, NOW + timedelta(hours=70), NOW + timedelta(hours=80))
    state = MarineState(15.2, 72.8, asyncio.run(replay.observe(q)))
    d = assess_window(state, q.start, q.end, now=NOW)
    assert any("72 h" in u for u in d.uncertainty)


def test_rules_table_is_citable():
    table = rules_table()
    assert table["version"].startswith("orca-rules-")
    assert all(r["reference"] for r in table["scored"])
    assert "wind_gusts" in table["not_scored"]
