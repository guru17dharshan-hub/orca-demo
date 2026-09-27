import asyncio

import httpx
import pytest

from orca.adapters.open_meteo import OpenMeteoAdapter
from orca.agents.explanation import validate_llm_output
from orca.agents.intent import parse_message, resolve_window
from orca.agents.orchestrator import ChatRequest, Orchestrator
from orca.i18n.detect import detect_language, normalize_digits
from orca.llm import NullProvider, ScriptedProvider
from orca.services import build_services
from orca.timeutil import SimClock, fmt_ist, fmt_ist_hour

from .conftest import NOW

GOA = dict(lat=15.40, lon=73.70, location_label="your location")


def make(llm=None, mode="replay", **kw):
    svc = build_services(mode=mode, llm=llm or NullProvider(), clock=SimClock(base=lambda: NOW), **kw)
    return svc, Orchestrator(svc)


def ask(orc, message, session_id=None, **loc):
    return asyncio.run(orc.handle(ChatRequest(message=message, session_id=session_id, **loc)))


# --- language & intent ----------------------------------------------------------------
@pytest.mark.parametrize(
    "text,lang",
    [
        ("Is it safe tomorrow?", "en"),
        ("कल सुबह समुद्र में जाना सुरक्षित है?", "hi"),
        ("उद्या सकाळी समुद्रात जाणे सुरक्षित आहे का?", "mr"),
        ("நாளை கடலுக்குச் செல்லலாமா?", "ta"),
        ("రేపు సముద్రంలోకి వెళ్లవచ్చా?", "te"),
        ("നാളെ കടലിൽ പോകാമോ?", "ml"),
        ("ನಾಳೆ ಸಮುದ್ರಕ್ಕೆ ಹೋಗಬಹುದೇ?", "kn"),
        ("kal subah samundar mein jana surakshit hai kya", "hi"),
    ],
)
def test_language_detection(text, lang):
    assert detect_language(text) == lang


def test_native_digits_are_normalized():
    assert normalize_digits("१५.२, ७२.८") == "15.2, 72.8"
    assert parse_message("१५.२, ७२.८ पर कल सुरक्षित है?").coordinates == (15.2, 72.8)


@pytest.mark.parametrize(
    "question,intent",
    [
        ("Where is the nearest Potential Fishing Zone (PFZ) today?", "pfz"),
        ("Is it safe to venture into the sea tomorrow morning?", "safety"),
        ("What are the tide, weather, and sea conditions near my fishing location?", "conditions"),
        ("Are there any lightning or cyclone alerts in my area?", "alerts"),
        ("Which regions show high chlorophyll concentration and favourable sea surface temperature?", "hotspots"),
        ("What is the safest route for a fishing vessel considering weather and sea-state conditions?", "route"),
        ("Why has fish productivity declined in a particular coastal region?", "productivity"),
        ("Which fishing zones should be avoided due to hazardous marine conditions or geofencing restrictions?", "avoid"),
    ],
)
def test_all_official_example_queries_map_to_an_intent(question, intent):
    assert parse_message(question).primary_intent == intent


def test_time_resolution_in_ist():
    start, end = resolve_window(parse_message("tomorrow at 6 AM for 4 hours").time, NOW)
    assert fmt_ist(start) == "25 Sep 06:00 IST" and fmt_ist(end) == "25 Sep 10:00 IST"
    start, end = resolve_window(parse_message("Is it safe?").time, NOW)  # no time words -> now..+6h
    assert start == NOW and (end - start).total_seconds() == 6 * 3600
    start, _ = resolve_window(parse_message("கடல் நிலை நாளை மாலை").time, NOW)
    assert fmt_ist_hour(start) == "16:00"


def test_existential_there_is_not_a_reference():
    assert not parse_message("Are there any cyclone alerts?").refers_to_previous
    assert parse_message("Is it safe there?").refers_to_previous


# --- orchestration --------------------------------------------------------------------
def test_golden_conversation_follow_up_resolves_to_zone():
    svc, orc = make()
    r1 = ask(orc, "Where is the nearest Potential Fishing Zone today?", **GOA)
    assert r1.intents == ["pfz"] and r1.cards["pfz"]["candidates"][0]["id"] == "DEMO-PFZ-01"
    assert "DEMO" in r1.answer and r1.simulated
    r2 = ask(orc, "Is it safe to go there tomorrow at 6 AM?", r1.session_id, **GOA)
    assert r2.place.source == "pfz" and "DEMO-PFZ-01" not in r2.place.label and "Mormugao" in r2.place.label
    safety = r2.cards["safety"]
    assert safety["risk_level"] == "HIGH"
    assert any(c["direction"] == "rising" for c in safety["change_points"])
    assert "thunderstorm" in r2.answer and "06:00" in r2.answer
    assert {s.step_id for s in r2.trace.steps} >= {"intent", "plan", "data", "weather", "ocean", "geo", "risk", "explain"}
    assert r2.trace.final_decision == "HIGH" and r2.trace.risk_engine_version.startswith("orca-rules")
    assert r2.evidence and all(e.data_type.value == "simulated" for e in r2.evidence if e.variable != "advisory")


def test_explicit_location_overrides_context():
    svc, orc = make()
    r1 = ask(orc, "Where is the nearest PFZ?", **GOA)
    r2 = ask(orc, "Is it safe at 12.9, 74.6 tomorrow morning?", r1.session_id, **GOA)
    assert r2.place.source == "coordinates" and r2.place.lat == 12.9


def test_route_follow_up_starts_from_harbour_to_zone():
    svc, orc = make()
    r1 = ask(orc, "Where is the nearest fishing zone?", **GOA)
    r2 = ask(orc, "Show me the safest route there today at 2 pm", r1.session_id, **GOA)
    route = r2.cards["route"]
    assert route["recommended"]["feasible"]
    end = route["end"]
    assert abs(end[0] - 15.45) < 0.01 and abs(end[1] - 73.35) < 0.01
    assert any(f["properties"]["kind"] == "route_recommended" for f in r2.map["features"])


def test_route_through_storm_is_refused():
    svc, orc = make()
    r1 = ask(orc, "Where is the nearest fishing zone?", **GOA)
    r2 = ask(orc, "Show me the safest route there tomorrow at 6 am", r1.session_id, **GOA)
    route = r2.cards["route"]
    assert route["recommended"] is None and route["direct"]["level_hours"].get("HIGH", 0) > 0
    assert "No route found" in r2.answer


@pytest.mark.parametrize(
    "question,expected",
    [
        ("कल सुबह गोवा से समुद्र में जाना सुरक्षित है?", "कुल जोखिम"),
        ("நாளை காலை ராமேஸ்வரம் அருகே கடலுக்குச் செல்லலாமா?", "மொத்த அபாயம்"),
        ("రేపు ఉదయం కాకినాడ దగ్గర సముద్రంలోకి వెళ్లవచ్చా?", "మొత్తం ప్రమాదం"),
        ("നാളെ രാവിലെ കൊച്ചിയിൽ കടലിൽ പോകാമോ?", "മൊത്തം അപകടസാധ്യത"),
    ],
)
def test_reply_in_users_language(question, expected):
    _, orc = make()
    r = ask(orc, question, **GOA)
    assert expected in r.answer
    assert r.cards["safety"]["risk_level"] in ("LOW", "MODERATE", "HIGH", "SEVERE")  # structured facts stay language-neutral


def test_unsupported_template_language_falls_back_honestly():
    _, orc = make()
    r = ask(orc, "ನಾಳೆ ಬೆಳಿಗ್ಗೆ ಮಂಗಳೂರು ಬಳಿ ಸಮುದ್ರಕ್ಕೆ ಹೋಗಬಹುದೇ?", **GOA)
    assert r.language == "kn" and "LLM explainer" in r.answer


def test_no_location_asks_user():
    _, orc = make()
    r = ask(orc, "Is it safe tomorrow morning?")
    assert r.place is None and "location" in r.answer.lower()


def test_all_example_queries_complete_without_step_errors():
    _, orc = make()
    sid = None
    for q in [
        "Where is the nearest Potential Fishing Zone (PFZ) today?",
        "Is it safe to venture into the sea tomorrow morning?",
        "What are the tide, weather, and sea conditions near my fishing location?",
        "Are there any lightning or cyclone alerts in my area?",
        "Which regions show high chlorophyll concentration and favourable sea surface temperature?",
        "What is the safest route for a fishing vessel considering weather and sea-state conditions?",
        "Why has fish productivity declined near Kochi?",
        "Which fishing zones should be avoided due to hazardous marine conditions or geofencing restrictions?",
    ]:
        r = ask(orc, q, sid, **GOA)
        sid = r.session_id
        assert r.answer
        assert not [s for s in r.trace.steps if s.ok is False], (q, [(s.step_id, s.error) for s in r.trace.steps])


def test_live_mode_failure_is_honest_not_fabricated():
    failing = OpenMeteoAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    _, orc = make(mode="live", live_marine=failing, live_advisories=[])
    r = ask(orc, "Is it safe tomorrow morning?", **GOA)
    assert r.cards["safety"]["risk_level"] == "INSUFFICIENT_DATA"
    assert r.data_status.marine_source == "none" and not r.simulated
    assert "cannot confirm" in r.answer.lower() or "CANNOT CONFIRM" in r.answer


# --- LLM paths & verdict lock -------------------------------------------------------------
def _llm_answer(level, text="Risk increases in the morning.", ids=None):
    return {"answer": text, "risk_level": level, "key_factors": [], "actions": [], "evidence_ids": ids or []}


def test_llm_explanation_accepted_when_consistent():
    llm = ScriptedProvider(responses=[_llm_answer("HIGH", "Conditions worsen after 08:30 IST due to a thunderstorm.")])
    _, orc = make(llm=llm)
    r = ask(orc, "Is it safe at 15.45, 73.35 tomorrow at 6 AM?", **GOA)
    assert r.answer_source == "llm" and "08:30" in r.answer
    packet = llm.calls[0]["user"]
    assert '"simulated": true' in packet  # the LLM is told the data is simulated
    assert '"risk_level": "HIGH"' in packet and '"evidence": [{' in packet


@pytest.mark.parametrize(
    "bad,reason",
    [
        (_llm_answer("LOW", "It is calm."), "risk_level"),
        (_llm_answer("HIGH", "Waves will reach 7.3 m."), "number 7.3"),
        (_llm_answer("HIGH", "Rough later.", ids=["made-up-id"]), "unknown evidence"),
        (_llm_answer("HIGH", "It is safe to go fishing."), "claims safety"),
    ],
)
def test_verdict_lock_discards_bad_llm_output(bad, reason):
    llm = ScriptedProvider(responses=[bad])
    _, orc = make(llm=llm)
    r = ask(orc, "Is it safe at 15.45, 73.35 tomorrow at 6 AM?", **GOA)
    assert r.answer_source == "template"
    assert reason in (r.answer_note or "")
    assert r.cards["safety"]["risk_level"] == "HIGH"


def test_validate_numbers_accept_native_digits():
    packet = {"evidence": [], "safety": {"hourly": [{"time_ist": "08:30", "wave_height": 1.3}]}}
    assert validate_llm_output(_llm_answer("HIGH", "लहरें १.३ मी, ०८:३० बजे"), packet, "HIGH") is None


def test_llm_intent_fallback_used_only_when_rules_fail():
    llm = ScriptedProvider(responses=[
        {"intents": ["pfz"], "place_name": "Karwar", "latitude": None, "longitude": None, "day_offset": None,
         "start_hour_local": None, "duration_hours": None},
    ])
    _, orc = make(llm=llm)
    r = ask(orc, "मुझे बताओ अभी कहाँ जाल डालूँ?", **GOA)  # no rule keyword
    assert r.trace.intent_source == "llm" and r.intents == ["pfz"]
    assert "Karwar" in r.place.label


def test_unsafe_llm_answer_leads_with_deterministic_advice_in_reply_language():
    # The 'safe' wording check is English-only, so the engine's advice must lead any non-English LLM answer.
    llm = ScriptedProvider(responses=[_llm_answer("HIGH", "समुद्र शांत है।")])
    _, orc = make(llm=llm)
    r = ask(orc, "Is it safe at 15.45, 73.35 tomorrow at 6 AM?", language="hi", **GOA)
    assert r.answer_source == "llm"
    assert r.answer.startswith("अधिक जोखिम") and r.actions[0].startswith("अधिक जोखिम")


def test_numbers_from_the_question_are_not_evidence():
    packet = {"question": "will waves be 9 m?", "evidence": [], "safety": {"hourly": [{"wave_height": 1.3}]}}
    assert "number 9" in validate_llm_output(_llm_answer("HIGH", "Waves reach 9 m."), packet, "HIGH")


def test_fallback_provider_uses_next_provider_when_first_fails():
    from orca.llm import FallbackProvider

    first, second = ScriptedProvider(responses=[None], name="groq"), ScriptedProvider(responses=[{"ok": 1}], name="gemini")
    result = asyncio.run(FallbackProvider([first, second]).complete_json("s", "u", {}))
    assert result.data == {"ok": 1} and result.provider == "gemini"
    assert len(first.calls) == 1 and len(second.calls) == 1


def test_conditions_answer_uses_the_trend_level_it_was_shown():
    q = "What are the tide, weather, and sea conditions near my fishing location?"
    _, orc = make()
    level = ask(orc, q, **GOA).cards["conditions"]["risk_level"]
    llm = ScriptedProvider(responses=[_llm_answer(level, "Sea conditions near you.")])
    _, orc = make(llm=llm)
    r = ask(orc, q, **GOA)
    assert '"risk_level": "%s"' % level in llm.calls[0]["user"]
    assert r.answer_source == "llm", r.answer_note


@pytest.mark.parametrize("text,ok", [("Wind up to 34 km/h.", True), ("Wind up to 34.5 km/h.", True), ("Wind up to 35 km/h.", False)])
def test_numbers_may_be_rounded_but_not_invented(text, ok):
    packet = {"evidence": [], "conditions": {"max_wind_kmh": 34.4696}}
    assert (validate_llm_output(_llm_answer("HIGH", text), packet, "HIGH") is None) == ok
