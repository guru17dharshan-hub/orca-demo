"""Explanation Agent (guide §16, §28): turns finished, validated results into a
short answer in the user's language.

LLM path: compact evidence packet → structured JSON → VERDICT LOCK:
  • risk_level must equal the deterministic engine's level,
  • every cited evidence id must exist in the packet,
  • every number in the text must appear in the packet (no invented values),
  • no 'safe' wording when the engine says HIGH / SEVERE / cannot confirm.
Any violation discards the LLM text and the template explainer is used instead.
The UI never parses this text for risk — it renders the structured cards."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..i18n.detect import LANGUAGE_NAMES, normalize_digits
from ..i18n.messages import SUPPORTED_TEMPLATE_LANGUAGES, direction8, t, template_language
from ..llm.providers import LLMProvider
from ..timeutil import IST, ensure_utc

LEVEL_ENUM = ["LOW", "MODERATE", "HIGH", "SEVERE", "INSUFFICIENT_DATA", "NOT_APPLICABLE"]
UNSAFE_LEVELS = ("HIGH", "SEVERE", "INSUFFICIENT_DATA")

EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "risk_level": {"type": "string", "enum": LEVEL_ENUM},
        "key_factors": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "risk_level", "key_factors", "actions", "evidence_ids"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You explain validated marine decision results for ORCA, a decision-support assistant for Indian fishermen.

Rules:
- Use only the values in the evidence packet. Never invent or estimate a number, place, time or source.
- The risk level was computed by a deterministic engine. Copy it exactly into risk_level: safety.risk_level, or conditions.risk_level when there is no safety section, or NOT_APPLICABLE if the packet has neither. Never soften, upgrade or contradict it.
- Distinguish forecasts from observations, and say clearly when data is SIMULATED.
- Mention the time when risk changes and the factor that causes it, if present.
- If data is missing or uncertain, say so; never call conditions safe when the level is HIGH, SEVERE or INSUFFICIENT_DATA.
- Keep the answer short and plain for fishermen: 2–5 sentences. Times are IST.
- Write answer, key_factors and actions in the reply language given in the packet.
- evidence_ids: list the packet evidence ids your answer relies on."""

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass
class Explanation:
    answer: str
    key_factors: list[str]
    actions: list[str]
    evidence_ids: list[str]
    source: str  # llm | template
    note: str | None = None  # why the LLM text was not used, if applicable
    llm_meta: dict | None = None


def _ist(dt) -> str:
    return ensure_utc(dt).astimezone(IST).strftime("%H:%M")


def _numbers_in(obj: Any) -> set[float]:
    out: set[float] = set()
    if isinstance(obj, bool):
        return out
    if isinstance(obj, (int, float)):
        out.add(float(obj))
    elif isinstance(obj, str):
        out.update(float(m) for m in _NUM_RE.findall(normalize_digits(obj)))
    elif isinstance(obj, dict):
        for v in obj.values():
            out |= _numbers_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out |= _numbers_in(v)
    return out


def _number_allowed(text: str, allowed: set[float]) -> bool:
    """A number is supported if it is a packet value, or a packet value rounded to the precision it is written at
    (34.47 km/h may be written '34'; '35' is not supported by it)."""
    n = float(text)
    decimals = len(text.split(".")[1]) if "." in text else 0
    if any(round(a, decimals) == n for a in allowed):
        return True
    return any(abs(n - a) <= 0.05 + 0.005 * abs(a) or abs(abs(n) - abs(a)) <= 0.05 for a in allowed)


def validate_llm_output(data: dict, packet: dict, expected_level: str) -> str | None:
    """Return None if the LLM output passes the verdict lock, else the reason it failed."""
    if data.get("risk_level") != expected_level:
        return f"risk_level {data.get('risk_level')!r} does not match engine level {expected_level!r}"
    allowed_ids = {e["id"] for e in packet.get("evidence", [])}
    unknown = [e for e in data.get("evidence_ids", []) if e not in allowed_ids]
    if unknown:
        return f"unknown evidence ids: {unknown[:3]}"
    allowed_numbers = _numbers_in({k: v for k, v in packet.items() if k != "question"})  # the user's own numbers are not evidence
    text = " ".join([data.get("answer", ""), *data.get("key_factors", []), *data.get("actions", [])])
    for m in _NUM_RE.findall(normalize_digits(text)):
        if not _number_allowed(m, allowed_numbers):
            return f"number {m} is not in the evidence packet"
    if expected_level in UNSAFE_LEVELS:
        lowered = text.lower()
        if re.search(r"\b(it is|it's|is) safe\b", lowered) or re.search(r"(?<!not )\bsafe to (go|venture|fish)\b", lowered):
            return "text claims safety contrary to the engine level"
    return None


# ------------------------------------------------------------------------------ packet
def build_packet(question: str, language: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Compact, structured evidence packet — never raw datasets."""
    packet: dict[str, Any] = {
        "question": question,
        "reply_language": LANGUAGE_NAMES.get(language, "English"),
        "intents": ctx.get("intents", []),
        "place": ctx.get("place"),
        "window_ist": ctx.get("window"),
        "data": ctx.get("data"),
    }
    decision = ctx.get("decision")
    evidence: list[dict] = []
    if decision is not None:
        packet["safety"] = {
            "risk_level": decision.risk_level.value,
            "rule_version": decision.rule_version,
            "hourly": [
                {
                    "time_ist": _ist(h.time),
                    "level": h.level.value,
                    **{f.variable: f.value for f in h.factors if f.variable in ("wave_height", "wind_speed", "visibility")},
                    "thunderstorm": any(f.variable == "weather_code" for f in h.factors),
                    "official_warning": any(f.variable == "advisory" for f in h.factors),
                    "missing": h.missing,
                }
                for h in decision.hours
            ],
            "changes": [
                {"time_ist": _ist(c.time), "to": c.to_level.value, "direction": c.direction,
                 "cause": c.cause.variable if c.cause else None, "cause_value": c.cause.value if c.cause else None}
                for c in decision.change_points
            ],
            "lowest_risk_windows_ist": [f"{_ist(w.start)}–{_ist(w.end)}" for w in decision.go_windows],
            "hard_constraints": decision.hard_constraints,
            "uncertainty": decision.uncertainty,
        }
        index = ctx.get("evidence_index", {})
        for eid in decision.evidence_ids[:30]:
            e = index.get(eid)
            if e is not None:
                evidence.append({"id": e.id, "source": e.source, "data_type": e.data_type.value, "variable": e.variable,
                                 "value": e.value, "unit": e.unit, "valid_time_ist": _ist(e.valid_time) if e.valid_time else None})
    for key in ("coverage", "pfz", "route", "conditions", "alerts", "geofence", "hotspots", "productivity", "avoid", "regulations"):
        if ctx.get(key) is not None:
            packet[key] = ctx[key]
    for extra in ctx.get("extra_evidence", []):
        evidence.append(extra)
    packet["evidence"] = evidence
    return packet


# ------------------------------------------------------------------------------ templates
def _fmt(v: Any, digits: int = 1) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.{digits}f}"
    return str(v)


def template_explanation(language: str, ctx: dict[str, Any]) -> Explanation:
    lang = template_language(language)
    lines: list[str] = []
    factors: list[str] = []
    actions: list[str] = []
    intents = ctx.get("intents", [])
    decision = ctx.get("decision")
    place = ctx.get("place") or "—"

    def factor_text(variable: str | None, value: Any) -> str:
        if variable is None:
            return ""
        return t(f"factor.{variable}", lang, value=_fmt(value) if isinstance(value, (int, float)) else value)

    if "help" in intents and len(intents) == 1:
        lines.append(t("help", lang))
    if ctx.get("needs_location"):
        lines.append(t("clarify.location", lang))

    pfz = ctx.get("pfz")
    if pfz is not None:
        cands = pfz.get("candidates", [])
        viable = [c for c in cands if c["viable"]]
        top = viable[0] if viable else (cands[0] if cands else None)
        if top:
            lines.append(t("pfz.nearest", lang, name=top["name"], distance=_fmt(top["distance_km"]),
                           direction=t(f"dir.{direction8(top['compass'])}", lang)))
            if top.get("issues"):
                lines.append(t("pfz.issue", lang, issues="; ".join(top["issues"])))
            if top.get("demo"):
                lines.append(t("pfz.demo", lang))
            elif top.get("derived"):
                lines.append(t("pfz.derived", lang, basis=top.get("basis") or "SST"))
        else:
            lines.append(t("pfz.none", lang))

    if decision is not None:
        level = decision.risk_level.value
        lines.append(t("safety.summary", lang, place=place, window=ctx.get("window", ""), level=t(f"level.{level}", lang)))
        rising = [c for c in decision.change_points if c.direction == "rising"]
        for c in rising[:2]:
            lines.append(t("safety.rising", lang, level=t(f"level.{c.to_level.value}", lang), time=_ist(c.time),
                           factor=factor_text(c.cause.variable, c.cause.value) if c.cause else "—"))
        if decision.go_windows:
            w = max(decision.go_windows, key=lambda w: w.hours)
            lines.append(t("safety.go_window", lang, start=_ist(w.start), end=_ist(w.end)))
        elif level != "LOW":
            lines.append(t("safety.no_go_window", lang))
        if level in ("MODERATE", "HIGH", "SEVERE") and not rising and decision.key_factors:
            kf = decision.key_factors[0]
            lines.append(t("safety.reason", lang, factor=factor_text(kf.variable, kf.value)))
        for c in decision.hard_constraints:
            lines.append(t("safety.constraint", lang, text=c))
        actions.append(t(f"advice.{level}", lang))
        for f in decision.key_factors[:4]:
            factors.append(factor_text(f.variable, f.value))

    cond = ctx.get("conditions")
    if cond is not None and decision is None:
        now = cond.get("now", {})
        key = "conditions.now" if now.get("sea_level_m") is not None else "conditions.now_notide"
        lines.append(t(key, lang, place=place, wave=_fmt(now.get("wave_height_m")), wind=_fmt(now.get("wind_kmh")),
                       sst=_fmt(now.get("sst_c")), tide=_fmt(now.get("sea_level_m"), 2)))
        if cond.get("risk_level"):
            lines.append(t("conditions.trend", lang, level=t(f"level.{cond['risk_level']}", lang)))
            actions.append(t(f"advice.{cond['risk_level']}", lang))

    alerts = ctx.get("alerts")
    if alerts is not None:
        covering = [a for a in alerts.get("covering", []) if a.get("data_type") != "derived"]
        if covering:
            lines.append(t("alerts.some", lang, list="; ".join(f"{a['event']} ({a['severity']}, {a['source']})" for a in covering)))
        else:
            lines.append(t("alerts.none", lang))
        watches = [a for a in alerts.get("covering", []) + alerts.get("elsewhere", []) if a.get("data_type") == "derived"]
        if watches:
            lines.append(t("alerts.cyclone_watch", lang, headline=watches[0]["headline"]))

    geo = ctx.get("geofence")
    if geo is not None and ("avoid" in intents or "safety" in intents or "alerts" in intents or "route" in intents):
        hits = [h for h in geo.get("hits", []) if h["kind"] != "hazard"]
        for h in hits[:2]:
            key = {"inside": "geofence.inside", "beyond": "geofence.beyond"}.get(h["relation"], "geofence.approaching")
            lines.append(t(key, lang, name=h["name"], rule=h["rule"], distance=_fmt(h["distance_km"])))

    route = ctx.get("route")
    if route is not None:
        rec = route.get("recommended")
        if rec:
            lines.append(t("route.rec", lang, distance=_fmt(rec["distance_km"]), hours=_fmt(rec["duration_h"]),
                           level=t(f"level.{rec['max_level'] or 'INSUFFICIENT_DATA'}", lang)))
        else:
            lines.append(t("route.none", lang))
        reasons = route.get("reasons", [])
        if lang == "en" and reasons:
            lines.append(reasons[0])  # planner reasons are English; other languages see them in the route card
        factors.extend(reasons[:3])

    hot = ctx.get("hotspots")
    if hot is not None:
        if hot.get("available"):
            items = "; ".join(f"{h['lat']:.2f}N {h['lon']:.2f}E — {h['chl']} mg/m³, {h['sst']} °C" + (" (SST front)" if h.get("sst_front") else "")
                              for h in hot["hotspots"][:4])
            lines.append(t("hotspots.list", lang, list=items))
        elif (ctx.get("data") or {}).get("marine_source") == "historical":
            lines.append(t("hotspots.no_archive", lang))
        else:
            lines.append(t("hotspots.unavailable", lang))

    prod = ctx.get("productivity")
    if prod is not None:
        if prod.get("available") and prod.get("sst_anomaly_c") is not None:  # real satellite SST record
            lines.append(t("productivity.sst", lang, place=place, sst=f"{prod['sst_change_c']:+.1f}",
                           days=prod["period_days"]["recent"], anom=f"{prod['sst_anomaly_c']:+.1f}"))
            lines.append(t("productivity.warm" if prod["sst_anomaly_c"] >= 0.5 else "productivity.not_warm", lang))
        elif prod.get("available"):
            lines.append(t("productivity.summary", lang, place=place, sst=f"{prod['sst_change_c']:+.1f}", chl=f"{prod['chl_change_pct']:+.0f}"))
            lines.append(t("productivity.interpretation", lang))
        else:
            lines.append(t("productivity.unavailable", lang))

    avoid = ctx.get("avoid")
    if avoid is not None:
        items = avoid.get("items", [])
        if items:
            lines.append(t("avoid.list", lang, list="; ".join(
                f"{i['name']} ({'; '.join(r.rstrip('.') for r in i['reasons'][:2])})" for i in items[:5])))
        else:
            lines.append(t("avoid.none", lang))

    if (ctx.get("data") or {}).get("simulated"):
        lines.append(t("simulated", lang))
    hist = ctx.get("historical")
    if hist and "outside_replay" in (ctx.get("coverage") or {}).get("gaps", []):
        lines.insert(0, t("coverage.outside", lang, event=hist["event"], place=place))
    if hist:
        lines.append(t("historical", lang, event=hist["event"], as_of=hist["as_of"]))
    note = None
    if language not in SUPPORTED_TEMPLATE_LANGUAGES:
        note = t("fallback.language", "en", language=LANGUAGE_NAMES.get(language, language))
        lines.insert(0, note)
    return Explanation(answer=" ".join(line for line in lines if line), key_factors=[f for f in factors if f],
                       actions=actions, evidence_ids=list(decision.evidence_ids[:10]) if decision else [], source="template", note=note)


# ------------------------------------------------------------------------------ agent
def expected_level(ctx: dict[str, Any]) -> str:
    """The engine level the LLM was shown: the safety verdict, else the conditions trend (a conditions question
    carries the window's risk level too), else nothing to copy."""
    decision = ctx.get("decision")
    if decision is not None:
        return decision.risk_level.value
    return (ctx.get("conditions") or {}).get("risk_level") or "NOT_APPLICABLE"


async def explain(provider: LLMProvider, question: str, language: str, ctx: dict[str, Any]) -> Explanation:
    fallback = template_explanation(language, ctx)
    if not provider.available:
        return fallback
    packet = build_packet(question, language, ctx)
    expected = expected_level(ctx)
    result = await provider.complete_json(SYSTEM_PROMPT, json.dumps(packet, ensure_ascii=False, default=str), EXPLANATION_SCHEMA)
    meta = {"provider": result.provider, "model": result.model, "served_by": result.served_by, "latency_ms": round(result.latency_ms, 1)}
    if result.data is None:
        fallback.note, fallback.llm_meta = f"LLM unavailable: {result.error}", meta
        return fallback
    reason = validate_llm_output(result.data, packet, expected)
    if reason:
        fallback.note, fallback.llm_meta = f"LLM explanation discarded by verdict lock: {reason}", meta | {"discarded": True}
        return fallback
    answer, actions = result.data["answer"], result.data["actions"]
    if expected in UNSAFE_LEVELS:
        # The 'safe' wording check above is English-only, so for unsafe verdicts the deterministic
        # advice leads the answer in every language, whatever the LLM wrote after it.
        advice = t(f"advice.{expected}", template_language(language))
        answer = f"{advice} {answer}"
        actions = [advice, *(a for a in actions if a != advice)]
    hist = ctx.get("historical")
    if hist and "outside_replay" in (ctx.get("coverage") or {}).get("gaps", []):
        answer = f"{t('coverage.outside', template_language(language), event=hist['event'], place=ctx.get('place') or '—')} {answer}"
    return Explanation(
        answer=answer,
        key_factors=result.data["key_factors"],
        actions=actions,
        evidence_ids=result.data["evidence_ids"],
        source="llm",
        llm_meta=meta,
    )
