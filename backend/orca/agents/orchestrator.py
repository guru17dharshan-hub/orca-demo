"""Agent Orchestrator (guide §17): executes the plan, collects evidence and
provenance, asks the Explanation Agent to summarize, and returns structured
cards + map features + trace. Workflow:

 1 receive question → 2 detect language → 3 resolve context → 4 extract entities
 5 planner selects tools → 6 run independent agents in parallel → 7 validate
 8 marine state → 9 spatial + temporal analysis → 10 risk → 11 route/alerts
 12 evidence + provenance → 13 explanation → 14 structured response → 15 save state
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ..data_service import DataStatus
from ..i18n.detect import LANGUAGE_NAMES
from ..i18n.messages import direction8, t, template_language
from ..models import Evidence
from ..risk.engine import RiskDecision
from ..services import Services
from ..timeutil import IST, ensure_utc
from ..trace import Trace, TraceRecorder
from . import specialists as sp
from .context import Place, SelectedZone
from .explanation import explain
from .intent import llm_parse, parse_message
from . import llm_planner as lp
from .planner import Plan, build_plan

LEVEL_COLORS = {"LOW": "low", "MODERATE": "moderate", "HIGH": "high", "SEVERE": "severe", "INSUFFICIENT_DATA": "unknown"}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    location_label: str | None = None
    language: str | None = None  # force reply language (else detected)


class ChatResponse(BaseModel):
    request_id: str
    session_id: str
    language: str
    language_name: str
    answer: str
    answer_source: str
    answer_note: str | None = None
    key_factors: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    intents: list[str]
    place: Place | None
    window: dict[str, datetime] | None
    cards: dict[str, Any]
    map: dict[str, Any]
    evidence: list[Evidence]
    data_status: DataStatus | None
    simulated: bool
    clock_offset_hours: float
    disclaimer: str
    trace: Trace
    suggestions: list[str]


async def run_plan(plan: Plan, recorder: TraceRecorder) -> dict[str, sp.AgentResult]:
    results: dict[str, sp.AgentResult] = {}
    failed: set[str] = set()
    pending = list(plan.steps)
    while pending:
        ready = [s for s in pending if all(d in results or d in failed for d in s.depends_on)]
        if not ready:
            break
        pending = [s for s in pending if s not in ready]

        async def execute(step):
            if any(d in failed for d in step.depends_on):
                recorder.start(step.id, step.agent, step.kind, step.action, step.depends_on)
                recorder.finish(step.id, False, error="skipped: a dependency failed")
                failed.add(step.id)
                return
            recorder.start(step.id, step.agent, step.kind, step.action, step.depends_on)
            try:
                res = await step.run(results)
            except Exception as exc:  # an agent failure must not take the request down
                failed.add(step.id)
                recorder.finish(step.id, False, error=f"{type(exc).__name__}: {exc}"[:300])
                return
            results[step.id] = res
            recorder.finish(step.id, True, res.summary, res.sources)

        await asyncio.gather(*(execute(s) for s in ready))
    return results


def _feature(geometry: dict, **props) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": props}


def _point(lat: float, lon: float) -> dict:
    return {"type": "Point", "coordinates": [lon, lat]}


def _line(points) -> dict:
    return {"type": "LineString", "coordinates": [[p[1], p[0]] for p in points]}


def _polygon(points) -> dict:
    ring = [[p[1], p[0]] for p in points]
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


class Orchestrator:
    def __init__(self, svc: Services) -> None:
        self.svc = svc

    async def handle(self, req: ChatRequest) -> ChatResponse:
        svc = self.svc
        now = svc.clock()
        state = svc.contexts.get(req.session_id, now)
        recorder = TraceRecorder(state.session_id, req.message)

        # 1–4 language, intent, entities
        recorder.start("intent", "intent-agent", "tool-agent", "detect language, intents and entities")
        parsed = parse_message(req.message)
        # Questions the fixed planner cannot express (several harbours, "which is safest") are planned by the LLM,
        # validated, and executed with the same deterministic engines — no separate intent call needed.
        specific = set(parsed.intents) & {"route", "pfz", "hotspots", "productivity", "avoid", "alerts"}
        planned = len(lp.ports_in_text(req.message)) >= 2 or (lp.wants_llm_plan(req.message) and not specific)
        if not parsed.intents and req.message.strip() and not planned:
            parsed = await llm_parse(parsed, svc.llm)
        language = req.language or parsed.language
        recorder.finish("intent", True, f"{language}; intents={parsed.intents} via {parsed.source}; "
                        f"coords={parsed.coordinates}; port={parsed.port.id if parsed.port else None}")
        recorder.trace.language, recorder.trace.intents, recorder.trace.intent_source = language, parsed.intents, parsed.source

        device = Place(lat=req.lat, lon=req.lon, label=req.location_label or "your location", source="device") if req.lat is not None and req.lon is not None else None

        if planned:
            return await self._handle_planned(req, parsed, language, state, recorder, now)

        # 5 plan
        recorder.start("plan", "planner", "deterministic-engine", "decompose request into agent tasks")
        plan = build_plan(svc, parsed, state, device, now)
        recorder.finish("plan", True, "; ".join(plan.rationale))
        recorder.trace.plan = [s.describe() for s in plan.steps]

        # 6–11 run agents
        results = await run_plan(plan, recorder)

        # 12 assemble evidence, cards, map
        ctx, cards, features, evidence, status, decision = self._assemble(plan, results, language)
        recorder.trace.data_status = status.model_dump(mode="json") if status else None
        if decision is not None:
            recorder.trace.risk_engine_version = decision.rule_version
            recorder.trace.final_decision = decision.risk_level.value
            recorder.trace.evidence_ids = decision.evidence_ids

        # 13 explanation
        recorder.start("explain", "explanation-agent", "llm-agent" if svc.llm.available else "tool-agent",
                       "phrase the validated result in the user's language", ["risk"] if decision else [])
        explanation = await explain(svc.llm, req.message, language, ctx)
        recorder.finish("explain", True, f"{explanation.source}" + (f" — {explanation.note}" if explanation.note else ""))
        recorder.trace.llm = {"provider": svc.llm.name, "model": svc.llm.model, "used": explanation.source == "llm",
                              "note": explanation.note, **(explanation.llm_meta or {})}

        # 15 save structured conversation state
        if plan.place is not None:
            state.location = plan.place if plan.place.source != "pfz" else state.location
        state.window = plan.window
        state.language = language
        state.speed_knots = plan.speed_knots
        pfz_res = results.get("pfz")
        if pfz_res and pfz_res.value["candidates"]:
            viable = [c for c in pfz_res.value["candidates"] if c.viable] or pfz_res.value["candidates"]
            z = viable[0].zone
            state.selected_zone = SelectedZone(id=z.id, name=z.name, lat=z.centroid[0], lon=z.centroid[1])
        state.last_intents = plan.intents
        svc.contexts.save(state, now)

        trace = recorder.close()
        svc.traces.add(trace)
        lang_t = template_language(language)
        simulated = bool(status and status.marine_source == "replay") or any(
            e.data_type.value == "simulated" for e in evidence
        )
        return ChatResponse(
            request_id=trace.request_id,
            session_id=state.session_id,
            language=language,
            language_name=LANGUAGE_NAMES.get(language, language),
            answer=explanation.answer,
            answer_source=explanation.source,
            answer_note=explanation.note,
            key_factors=explanation.key_factors,
            actions=explanation.actions,
            intents=plan.intents,
            place=plan.place,
            window={"start": plan.window[0], "end": plan.window[1]} if plan.place else None,
            cards=cards,
            map={"type": "FeatureCollection", "features": features},
            evidence=evidence,
            data_status=status,
            simulated=simulated,
            clock_offset_hours=svc.clock.offset.total_seconds() / 3600,
            disclaimer=t("disclaimer", lang_t),
            trace=trace,
            suggestions=_suggestions(plan),
        )

    async def _handle_planned(self, req: ChatRequest, parsed, language: str, state, recorder: TraceRecorder, now: datetime) -> ChatResponse:
        svc = self.svc
        lang = template_language(language)
        planner_kind = "llm-agent" if svc.llm.available else "deterministic-engine"
        recorder.start("plan", "llm-planner" if svc.llm.available else "planner", planner_kind, "plan tool calls for a multi-harbour question")
        plan = await lp.plan_request(req.message, parsed.time, svc.llm)
        recorder.finish("plan", True, f"{plan.source} plan: " + "; ".join(s.describe() for s in plan.steps) + (f" | {'; '.join(plan.notes)}" if plan.notes else ""))
        recorder.trace.plan = [{"id": f"s{i}", "agent": s.tool, "kind": "deterministic-engine", "action": s.describe(), "depends_on": ["plan"]}
                               for i, s in enumerate(plan.steps)]
        for i, s in enumerate(plan.steps):
            recorder.start(f"s{i}", s.tool, "deterministic-engine", s.describe(), ["plan"])
        results = await lp.execute(svc, plan, now)
        for i, r in enumerate(results):
            summary = r.get("level") or (f"{len(r['warnings'])} warnings" if "warnings" in r else (r.get("zone") or {}).get("name", "no zone"))
            recorder.finish(f"s{i}", True, f"{r['harbour']}: {summary}")

        best = lp.best_harbour(results)
        lines: list[str] = []
        safety = [r for r in results if r["tool"] == "harbour_safety"]
        if safety:
            if best:
                gw = best["go_window"]
                when = t("compare.when", lang, start=_ist_hm(gw["start"]), end=_ist_hm(gw["end"])) if gw else ""
                lines.append(t("compare.best", lang, place=best["harbour"], level=t(f"level.{best['level']}", lang), when=when))
            else:
                lines.append(t("compare.none", lang))
            for r in sorted(safety, key=lambda r: lp.ORDER.get(r["level"], 3)):
                f = r["factor"]
                reason = "" if not f else " — " + t(f"factor.{f['variable']}", lang, value=f"{f['value']:.1f}" if isinstance(f["value"], (int, float)) else f["value"])
                lines.append(t("compare.row", lang, place=r["harbour"], level=t(f"level.{r['level']}", lang), reason=reason))
        for r in results:
            if r["tool"] == "nearest_zone":
                z = r["zone"]
                lines.append(t("compare.zone", lang, place=r["harbour"], name=z["name"], distance=f"{z['distance_km']:.0f}",
                               direction=t(f"dir.{direction8(z['compass'])}", lang)) if z else t("compare.nozone", lang, place=r["harbour"]))
            elif r["tool"] == "warnings":
                n = len(r["warnings"])
                lines.append(t("compare.warn", lang, place=r["harbour"], count=n) if n else t("compare.nowarn", lang, place=r["harbour"]))
        if svc.replay is not None:
            lines.append(t("historical", lang, event=svc.replay.event.title, as_of=sp.ist_label(svc.clock())))

        evidence: dict[str, Evidence] = {}
        status = None
        for r in results:
            st = r.pop("_state", None)
            status = r.pop("_status", None) or status
            if st is not None:
                index = st.evidence_index()
                for eid in r.get("evidence_ids", []):
                    if eid in index:
                        evidence[eid] = index[eid]
        features = [_feature(_point(r["lat"], r["lon"]), kind="harbour", label=r["harbour"], level=r.get("level")) for r in results if r["tool"] == "harbour_safety"]
        rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]

        if best:
            state.location = Place(lat=best["lat"], lon=best["lon"], label=f"off {best['harbour']}", source="port")
        state.language, state.last_intents = language, ["compare"]
        svc.contexts.save(state, now)
        recorder.trace.final_decision = best["level"] if best else None
        trace = recorder.close()
        svc.traces.add(trace)
        first = results[0]["window"] if results else None
        return ChatResponse(
            request_id=trace.request_id, session_id=state.session_id, language=language,
            language_name=LANGUAGE_NAMES.get(language, language), answer=" ".join(lines), answer_source="template",
            answer_note=f"planned by {'the LLM' if plan.source == 'llm' else 'rules'}; every verdict from the risk rules",
            key_factors=[], actions=[], intents=["compare"],
            place=state.location if best else None, window=first, map={"type": "FeatureCollection", "features": features},
            cards={"compare": {"planner": plan.source, "goal": plan.goal, "notes": plan.notes, "best": best["harbour_id"] if best else None, "rows": rows}},
            evidence=list(evidence.values())[:40], data_status=status, simulated=bool(status and status.marine_source == "replay"),
            clock_offset_hours=svc.clock.offset.total_seconds() / 3600, disclaimer=t("disclaimer", lang), trace=trace,
            suggestions=["Is it safe there tomorrow at 6 AM?", "Where is the nearest fishing zone from there?"],
        )

    def _assemble(self, plan: Plan, results: dict[str, sp.AgentResult], language: str):
        ctx: dict[str, Any] = {"intents": plan.intents, "needs_location": plan.needs_location}
        cards: dict[str, Any] = {}
        features: list[dict] = []
        evidence: dict[str, Evidence] = {}
        status: DataStatus | None = None
        decision: RiskDecision | None = None

        if self.svc.replay is not None:
            ctx["historical"] = {"event": self.svc.replay.event.title, "as_of": sp.ist_label(self.svc.clock())}
        if plan.place is not None:
            ctx["place"] = plan.place.label
            ctx["window"] = sp.window_label(*plan.window)
            features.append(_feature(_point(plan.place.lat, plan.place.lon), kind="location", label=plan.place.label, source=plan.place.source))

        if "catalog" in results:
            d = results["catalog"].value
            cards["discovery"] = d.model_dump(mode="json")
            ctx["coverage"] = {"gaps": d.gaps, "sources": [f"{x.agency}: {x.name} [{x.status}]" for x in d.sources]}

        state = None
        if "data" in results:
            state, status = results["data"].value
            ctx["data"] = {"simulated": status.marine_source == "replay", "marine_source": status.marine_source,
                           "fallback_reason": status.fallback_reason, "advisory_sources": status.advisory_sources}

            ctx["evidence_index"] = state.evidence_index()
            if plan.safety_target == "top_pfz" and plan.place is not None:
                ctx["place"] = f"{plan.place.label} → nearest fishing zone"
            cards["sources"] = state.sources() + [{"source": a.source, "product": a.event, "data_type": a.data_type.value, "retrieved_at": a.retrieved_at,
                  "reference": a.reference, "variables": ["advisory"]} for a in state.advisories]
            for a in state.advisories:
                for poly in a.polygons:
                    features.append(_feature(_polygon(poly), kind="advisory", label=a.headline or a.event, severity=a.severity,
                                             source=a.source, data_type=a.data_type.value, onset=_iso(a.onset), expires=_iso(a.expires)))
                evidence[a.id] = a.to_evidence()

        if "risk" in results:
            decision = results["risk"].value
            if "safety" in plan.intents:
                ctx["decision"] = decision  # the verdict is only spoken when safety was asked
            cards["safety"] = sp.summarize_decision(decision)
            if state is not None:
                index = state.evidence_index()
                for eid in decision.evidence_ids:
                    if eid in index:
                        evidence[eid] = index[eid]
            if plan.safety_target == "top_pfz" and state is not None:
                features.append(_feature(_point(state.lat, state.lon), kind="safety_target", label="safety evaluated here",
                                         level=decision.risk_level.value))

        if "weather" in results and "ocean" in results:
            ocean, weather = results["ocean"].value, results["weather"].value
            cond = {"now": ocean["now"], "tides": ocean["tides"], "max_wave_m": ocean["max_wave_m"], "sst_range_c": ocean["sst_range_c"],
                    "max_current_kmh": ocean["max_current_kmh"], "weather": weather}
            if decision is not None:
                cond["risk_level"] = decision.risk_level.value
            cards["conditions"] = cond
            if "conditions" in plan.intents:
                ctx["conditions"] = cond
                if "safety" not in plan.intents:
                    ctx.pop("decision", None)  # a conditions answer states the trend, not a full safety verdict

        if "geo" in results:
            g = results["geo"].value
            cards["geofence"] = g["geofence"].model_dump(mode="json")
            cards["regulations"] = [n.model_dump(mode="json") for n in g["regulations"]]
            ctx["geofence"] = cards["geofence"]
            ctx["regulations"] = cards["regulations"]

        if "alerts" in results:
            a = results["alerts"].value
            as_dict = lambda adv: {"id": adv.id, "event": adv.event, "headline": adv.headline, "severity": adv.severity,  # noqa: E731
                                   "source": adv.source, "data_type": adv.data_type.value, "onset": _iso(adv.onset),
                                   "expires": _iso(adv.expires), "area": adv.area_desc, "reference": adv.reference}
            cards["alerts"] = {"covering": [as_dict(x) for x in a["covering"]], "elsewhere": [as_dict(x) for x in a["elsewhere"]]}
            if "alerts" in plan.intents:
                ctx["alerts"] = cards["alerts"]

        if "pfz" in results:
            p = results["pfz"].value
            cands = []
            for c in p["candidates"]:
                z = c.zone
                cands.append({"id": z.id, "name": z.name, "distance_km": c.distance_km, "bearing_deg": c.bearing_deg,
                              "compass": c.compass, "viable": c.viable, "issues": c.issues, "source": z.source,
                              "data_type": z.data_type.value, "demo": z.data_type.value == "simulated",
                              "derived": z.data_type.value == "derived", "basis": z.attributes.get("basis"),
                              "valid_from": _iso(z.valid_from), "valid_until": _iso(z.valid_until), "centroid": z.centroid,
                              "attributes": z.attributes})
                geom = _polygon(z.coordinates) if z.geometry == "polygon" else _line(z.coordinates)
                features.append(_feature(geom, kind="pfz", id=z.id, label=z.name, viable=c.viable, distance_km=c.distance_km,
                                         data_type=z.data_type.value))
                evidence[f"pfz:{z.id}"] = z.to_evidence()
            cards["pfz"] = {"candidates": cands, "provider": p["provider"], "note": p["note"]}
            if "pfz" in plan.intents or "avoid" in plan.intents:
                ctx["pfz"] = cards["pfz"]

        if "route" in results and results["route"].value is not None:
            r = results["route"].value
            cards["route"] = r.model_dump(mode="json")
            ctx["route"] = {
                "recommended": None if r.recommended is None else {
                    "distance_km": r.recommended.distance_km, "duration_h": r.recommended.duration_h,
                    "max_level": r.recommended.max_level.value if r.recommended.max_level else None,
                    "level_hours": r.recommended.level_hours},
                "direct": {"distance_km": r.direct.distance_km, "duration_h": r.direct.duration_h,
                           "max_level": r.direct.max_level.value if r.direct.max_level else None,
                           "level_hours": r.direct.level_hours, "violations": r.direct.violations},
                "reasons": r.reasons, "cost_function": r.cost_function, "speed_knots": r.speed_knots,
            }
            features.append(_feature(_line([(w.lat, w.lon) for w in r.direct.waypoints]), kind="route_direct",
                                     label="direct line", feasible=r.direct.feasible,
                                     max_level=r.direct.max_level.value if r.direct.max_level else None))
            if r.recommended is not None:
                for seg in r.recommended.segments:
                    features.append(_feature(_line([seg.start, seg.end]), kind="route_recommended", level=seg.level.value,
                                             eta_start=_iso(seg.eta_start), eta_end=_iso(seg.eta_end)))
        elif "route" in results:
            cards["route"] = None
            ctx["route"] = {"recommended": None, "reasons": [results["route"].summary]}

        if "eo" in results:
            cards["hotspots"] = results["eo"].value
            ctx["hotspots"] = results["eo"].value
            for h in results["eo"].value.get("hotspots", []):
                features.append(_feature(_point(h["lat"], h["lon"]), kind="hotspot", chl=h["chl"], sst=h["sst"], sst_front=h["sst_front"]))

        if "productivity" in results:
            cards["productivity"] = results["productivity"].value
            ctx["productivity"] = {k: v for k, v in results["productivity"].value.items() if k != "series"}

        if "avoid" in results:
            cards["avoid"] = results["avoid"].value
            ctx["avoid"] = results["avoid"].value
            for item in results["avoid"].value["items"]:
                if "lat" in item:
                    features.append(_feature(_point(item["lat"], item["lon"]), kind="avoid", label=item["name"], reasons=item["reasons"]))

        if plan.place is not None:
            near = [f for f in self.svc.geofences.static
                    if min(abs(p[0] - plan.place.lat) + abs(p[1] - plan.place.lon) for p in f.coordinates) < 4.0]
            for f in near:
                geom = _line(f.coordinates) if f.geometry == "line" else _polygon(f.coordinates)
                features.append(_feature(geom, kind="geofence", geofence_kind=f.kind, id=f.id, label=f.name,
                                         accuracy=f.accuracy, rule=f.rule))
        return ctx, cards, features, list(evidence.values())[:60], status, decision


def _ist_hm(dt: datetime) -> str:
    return ensure_utc(dt).astimezone(IST).strftime("%H:%M")


def _iso(dt: datetime | None) -> str | None:
    return ensure_utc(dt).isoformat() if dt else None


def _suggestions(plan: Plan) -> list[str]:
    intents = set(plan.intents)
    if "pfz" in intents:
        return ["Is it safe to go there tomorrow at 6 AM?", "Show me the safest route there", "Which zones should I avoid?"]
    if "safety" in intents:
        return ["What are the tide and sea conditions?", "Are there any cyclone or lightning alerts?", "Plan a safer route to the nearest fishing zone"]
    if "route" in intents:
        return ["Is it safe to go there tomorrow morning?", "Which zones should be avoided?"]
    return ["Where is the nearest fishing zone today?", "Is it safe to venture into the sea tomorrow morning?",
            "Are there any lightning or cyclone alerts in my area?"]
