import asyncio

from orca.agents import llm_planner as lp
from orca.agents.orchestrator import ChatRequest, Orchestrator
from orca.llm import NullProvider, ScriptedProvider
from orca.services import build_services


def _orc(llm=None):
    svc = build_services(mode="historical", llm=llm or NullProvider(), event_id="michaung-2023")
    return svc, Orchestrator(svc)


def ask(orc, text, **kw):
    return asyncio.run(orc.handle(ChatRequest(message=text, **kw)))


def _step(tool, harbour, day="tomorrow", part="morning"):
    return {"tool": tool, "harbour": harbour, "day": day, "part": part}


def test_trigger_only_for_multi_harbour_or_comparison_questions():
    assert lp.wants_llm_plan("Compare Chennai and Puducherry for tomorrow")
    assert lp.wants_llm_plan("चेन्नई और पुडुचेरी में से कौन सा बंदरगाह सबसे सुरक्षित है?") or len(lp.ports_in_text("चेन्नई और पुडुचेरी")) >= 1
    assert [p.id for p in lp.ports_in_text("Chennai or Nagapattinam?")] == ["chennai", "nagapattinam"]
    assert not lp.wants_llm_plan("Is it safe tomorrow morning near Chennai?")


def test_rule_plan_compares_named_harbours_without_an_llm():
    _, orc = _orc()
    r = ask(orc, "Compare Chennai and Puducherry for tomorrow morning")
    c = r.cards["compare"]
    assert c["planner"] == "rules" and [row["harbour_id"] for row in c["rows"]] == ["chennai", "puducherry"]
    assert all(row["level"] in ("LOW", "MODERATE", "HIGH", "SEVERE", "INSUFFICIENT_DATA") for row in c["rows"])
    assert r.answer.startswith(("Safest choice:", "None of these harbours"))
    assert [s["agent"] for s in r.trace.plan] == ["harbour_safety", "harbour_safety"]
    assert next(s for s in r.trace.steps if s.step_id == "plan").agent == "planner"


def test_llm_plan_is_validated_before_running():
    llm = ScriptedProvider(responses=[{"goal": "compare", "steps": [
        _step("harbour_safety", "Chennai"), _step("harbour_safety", "Atlantis"), _step("launch_rockets", "Chennai"),
        _step("harbour_safety", "Nagapattinam"), _step("warnings", "Chennai", "today", "now")]}])
    _, orc = _orc(llm)
    r = ask(orc, "Compare Chennai and Nagapattinam tomorrow morning, and any warnings at Chennai?")
    c = r.cards["compare"]
    assert c["planner"] == "llm"
    assert [(row["tool"], row["harbour_id"]) for row in c["rows"]] == [("harbour_safety", "chennai"), ("harbour_safety", "nagapattinam"), ("warnings", "chennai")]
    assert any("Atlantis" in n for n in c["notes"]) and any("launch_rockets" in n for n in c["notes"])
    assert next(s for s in r.trace.steps if s.step_id == "plan").agent == "llm-planner"


def test_llm_chooses_harbours_when_only_a_state_is_named():
    llm = ScriptedProvider(responses=[{"goal": "safest TN harbour", "steps": [
        _step("harbour_safety", "Chennai (Kasimedu)"), _step("harbour_safety", "Nagapattinam"), _step("harbour_safety", "Rameswaram")]}])
    _, orc = _orc(llm)
    r = ask(orc, "Which harbour in Tamil Nadu is safest tomorrow morning?")
    assert [row["harbour_id"] for row in r.cards["compare"]["rows"]] == ["chennai", "nagapattinam", "rameswaram"]


def test_route_questions_are_not_taken_over():
    _, orc = _orc()
    r = ask(orc, "What is the safest route to the nearest fishing zone tomorrow at 6 am?", lat=13.1, lon=80.45)
    assert "compare" not in r.cards and "route" in r.intents
