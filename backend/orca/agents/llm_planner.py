"""LLM planning for questions the fixed planner does not cover — comparing harbours, "which is safest",
several places or times in one question.

The LLM only PLANS: it picks tools and arguments from a small, typed menu. Every step is validated
(known tool, known harbour, allowed time words, at most MAX_STEPS) before anything runs; an invalid or
missing plan falls back to a deterministic one. The tools are ORCA's own deterministic engines, so every
verdict still comes from the versioned risk rules, and the answer is assembled from checked templates."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..geo.ports import PORTS, Port, offshore_point
from ..llm.providers import LLMProvider
from ..risk.engine import RiskDecision, assess_window
from ..risk.rules import RANK, RiskLevel
from ..timeutil import IST, ensure_utc, floor_hour

MAX_STEPS = 8
TOOLS = {
    "harbour_safety": "hour-by-hour risk verdict for the sea off a harbour in a time window",
    "nearest_zone": "nearest reachable Potential Fishing Zone from a harbour",
    "warnings": "official IMD warnings covering the waters off a harbour",
}
DAYS = ("today", "tomorrow")
PARTS = ("now", "morning", "afternoon", "evening", "night")
PART_HOURS = {"morning": (4, 12), "afternoon": (12, 17), "evening": (17, 21), "night": (21, 28)}  # IST, night runs past midnight

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "goal": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tool": {"type": "string", "enum": list(TOOLS)},
                    "harbour": {"type": "string"},
                    "day": {"type": "string", "enum": list(DAYS)},
                    "part": {"type": "string", "enum": list(PARTS)},
                },
                "required": ["tool", "harbour", "day", "part"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["goal", "steps"],
    "additionalProperties": False,
}

PLANNER_SYSTEM = (
    "You plan tool calls for ORCA, a marine safety assistant for Indian fishermen. You never answer the question "
    "yourself. Choose steps only from these tools: "
    + "; ".join(f"{k}: {v}" for k, v in TOOLS.items())
    + ". Use only harbours from the list given. For comparisons, call harbour_safety once per harbour with the same "
    "day and part. If the user names a state or coast instead of harbours, choose that state's harbours from the list "
    f"(at most {MAX_STEPS} steps). Use part 'now' only when the user asks about the present."
)

COMPARE_WORDS = re.compile(
    r"\bcompare\b|\bcomparison\b|\bversus\b|\bvs\.?\b|\bwhich (harbou?r|port|place|one)\b|\bsafest\b|\bbetter\b|\bbest harbou?r\b"
    r"|तुलना|सबसे सुरक्षित|ஒப்பிடு|ஒப்பீடு|பாதுகாப்பான துறைமுகம்|పోల్చ|సురక్షితమైన రేవు|താരതമ്യ|ഏറ്റവും സുരക്ഷിത",
    re.IGNORECASE,
)


def ports_in_text(text: str) -> list[Port]:
    """Every harbour named in the text, in the order they appear (longest alias wins on overlaps)."""
    lowered = text.lower()
    hits: list[tuple[int, Port]] = []
    for port in PORTS:
        spots = [lowered.find(a.lower()) for a in port.aliases if a.lower() in lowered]
        if spots:
            hits.append((min(spots), port))
    return [p for _, p in sorted(hits, key=lambda x: x[0])]


def wants_llm_plan(text: str) -> bool:
    """Questions the fixed planner cannot express: two or more harbours, or an explicit comparison."""
    return len(ports_in_text(text)) >= 2 or bool(COMPARE_WORDS.search(text))


def find_port(name: str) -> Port | None:
    key = name.strip().lower()
    for p in PORTS:
        if key in (p.id, p.name.lower()) or key in (a.lower() for a in p.aliases):
            return p
    for p in PORTS:  # "Chennai (Kasimedu)" style or partial names
        if key and (key in p.name.lower() or p.name.lower().split(" (")[0] in key):
            return p
    return None


@dataclass
class Step:
    tool: str
    port: Port
    day: str
    part: str

    def describe(self) -> str:
        return f"{self.tool}({self.port.name}, {self.day} {self.part})"


@dataclass
class PlannedRequest:
    goal: str
    steps: list[Step]
    source: str  # llm | rules
    notes: list[str] = field(default_factory=list)


def validate_plan(data: dict | None) -> tuple[list[Step], list[str]]:
    """Keep only well-formed steps; report every rejection."""
    steps, notes = [], []
    for raw in (data or {}).get("steps", [])[: MAX_STEPS + 4]:
        tool, harbour = raw.get("tool"), str(raw.get("harbour", ""))
        port = find_port(harbour)
        if tool not in TOOLS:
            notes.append(f"rejected unknown tool {tool!r}")
        elif port is None:
            notes.append(f"rejected unknown harbour {harbour!r}")
        elif raw.get("day") not in DAYS or raw.get("part") not in PARTS:
            notes.append(f"rejected time {raw.get('day')!r}/{raw.get('part')!r}")
        elif len(steps) < MAX_STEPS and all((s.tool, s.port.id, s.day, s.part) != (tool, port.id, raw["day"], raw["part"]) for s in steps):
            steps.append(Step(tool, port, raw["day"], raw["part"]))
    return steps, notes


def rules_plan(text: str, parsed_time) -> PlannedRequest:
    """Deterministic fallback: a safety check at every harbour named, for the time the user gave."""
    day = "tomorrow" if (parsed_time.day_offset or 0) >= 1 else "today"
    hour = parsed_time.part[0] if parsed_time.part else (parsed_time.clock[0] if parsed_time.clock else None)
    if parsed_time.now or (hour is None and day == "today"):
        part = "now"
    elif hour is None:
        part = "morning"
    else:
        part = "morning" if 4 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening" if 17 <= hour < 21 else "night"
    ports = ports_in_text(text)
    return PlannedRequest("compare harbours" if len(ports) > 1 else "check harbour",
                          [Step("harbour_safety", p, day, part) for p in ports[:MAX_STEPS]], "rules")


async def plan_request(text: str, parsed_time, llm: LLMProvider) -> PlannedRequest:
    fallback = rules_plan(text, parsed_time)
    if not llm.available:
        return fallback
    harbours = "; ".join(f"{p.name} ({p.state})" for p in PORTS)
    result = await llm.complete_json(PLANNER_SYSTEM, f"Harbours: {harbours}\nQuestion: {text}", PLAN_SCHEMA)
    steps, notes = validate_plan(result.data)
    if not steps:
        fallback.notes = [f"LLM plan unusable ({result.error or 'no valid steps'}); rule plan used", *notes]
        return fallback
    return PlannedRequest(str((result.data or {}).get("goal", ""))[:200], steps, "llm", notes)


def window_for(day: str, part: str, now: datetime) -> tuple[datetime, datetime]:
    now = ensure_utc(now)
    if part == "now":
        start = floor_hour(now)
        return start, start + timedelta(hours=6)
    ist_midnight = now.astimezone(IST).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1 if day == "tomorrow" else 0)
    h0, h1 = PART_HOURS[part]
    start, end = ist_midnight + timedelta(hours=h0), ist_midnight + timedelta(hours=h1)
    if end <= now:  # 'today morning' asked in the afternoon: that window has passed, look at the next one
        start, end = start + timedelta(days=1), end + timedelta(days=1)
    return ensure_utc(start), ensure_utc(end)


# ------------------------------------------------------------------------------ execution (deterministic tools)
async def run_step(svc, step: Step, now: datetime) -> dict[str, Any]:
    lat, lon = offshore_point(step.port.lat, step.port.lon)
    start, end = window_for(step.day, step.part, now)
    base = {"tool": step.tool, "harbour": step.port.name, "harbour_id": step.port.id, "lat": lat, "lon": lon,
            "window": {"start": start, "end": end}}
    if step.tool == "nearest_zone":
        from . import specialists as sp

        res = await sp.pfz_agent(svc, step.port.lat, step.port.lon, now, svc.offline_source or "live")
        viable = [c for c in res.value["candidates"] if c.viable]
        z = viable[0] if viable else None
        return base | {"zone": None if z is None else {"name": z.zone.name, "distance_km": z.distance_km, "compass": z.compass}}
    state, status = await svc.data.marine_state(lat, lon, floor_hour(start), end)
    base["_status"] = status
    if step.tool == "warnings":
        warn = [a for a in state.advisories_containing_point() if a.data_type.value == "official_advisory"]
        return base | {"warnings": [{"event": a.event, "severity": a.severity, "headline": a.headline} for a in warn]}
    warn = [a for a in state.advisories_containing_point() if a.data_type.value == "official_advisory"]
    base["warnings"] = [{"event": a.event, "severity": a.severity, "headline": a.headline} for a in warn]
    geo = svc.geofences.check(lat, lon, start, state.advisories)
    decision: RiskDecision = assess_window(state, start, end, now, hard_constraints=geo.hard_constraints)
    kf = decision.key_factors[0] if decision.key_factors else None
    return base | {
        "level": decision.risk_level.value,
        "factor": None if kf is None else {"variable": kf.variable, "value": kf.value},
        "go_window": None if not decision.go_windows else {"start": decision.go_windows[0].start, "end": decision.go_windows[0].end},
        "evidence_ids": decision.evidence_ids[:6],
        "_state": state,
    }


ORDER = {**{lv.value: RANK[lv] for lv in RANK}, RiskLevel.INSUFFICIENT_DATA.value: 2.5}  # 'cannot confirm' is never recommended


async def execute(svc, plan: PlannedRequest, now: datetime) -> list[dict[str, Any]]:
    return list(await asyncio.gather(*(run_step(svc, s, now) for s in plan.steps)))


def best_harbour(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    rows = [r for r in results if r["tool"] == "harbour_safety"]
    rows.sort(key=lambda r: (ORDER.get(r["level"], 3), r["factor"]["value"] if r["factor"] and isinstance(r["factor"]["value"], (int, float)) else 0))
    top = rows[0] if rows else None
    return top if top and top["level"] in ("LOW", "MODERATE") else None
