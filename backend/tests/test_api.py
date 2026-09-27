import os

os.environ.setdefault("ORCA_NO_DEFAULT_APP", "1")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from orca.api import create_app  # noqa: E402
from orca.llm import NullProvider  # noqa: E402
from orca.services import build_services  # noqa: E402
from orca.timeutil import SimClock  # noqa: E402

from .conftest import NOW  # noqa: E402


@pytest.fixture
def client(tmp_path):
    svc = build_services(mode="replay", llm=NullProvider(), clock=SimClock(base=lambda: NOW))
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>ORCA</title>", encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log('orca')", encoding="utf-8")
    with TestClient(create_app(svc, alert_interval_s=0, web_dir=web)) as c:
        yield c


def test_health_reports_mode_adapters_and_llm(client):
    h = client.get("/api/health").json()
    assert h["data_mode"] == "replay" and h["llm"]["available"] is False
    assert {a["name"] for a in h["adapters"]} >= {"replay", "open-meteo", "imd-cap"}


def test_rules_and_reference_layers(client):
    assert client.get("/api/rules").json()["version"].startswith("orca-rules-")
    gf = client.get("/api/geofences").json()
    assert gf["type"] == "FeatureCollection" and all("accuracy" in f["properties"] for f in gf["features"])
    assert any(p["id"] == "goa" for p in client.get("/api/ports").json())


def test_chat_round_trip_with_session(client):
    r1 = client.post("/api/chat", json={"message": "Where is the nearest PFZ today?", "lat": 15.4, "lon": 73.7}).json()
    assert r1["cards"]["pfz"]["candidates"]
    r2 = client.post("/api/chat", json={"message": "Is it safe there tomorrow at 6 AM?", "session_id": r1["session_id"],
                                        "lat": 15.4, "lon": 73.7}).json()
    assert r2["cards"]["safety"]["risk_level"] == "HIGH"
    assert r2["trace"]["request_id"] and client.get(f"/api/traces/{r2['trace']['request_id']}").status_code == 200


def test_chat_rejects_empty_message(client):
    assert client.post("/api/chat", json={"message": "  "}).status_code == 422


def test_direct_risk_endpoint(client):
    body = client.get("/api/risk", params={"lat": 15.2, "lon": 72.8, "start": "2026-09-25T00:30:00Z", "end": "2026-09-25T06:30:00Z"}).json()
    assert body["decision"]["risk_level"] == "HIGH" and body["simulated"]
    assert body["evidence"] and body["decision"]["timeline"]
    assert client.get("/api/risk", params={"lat": 15.2, "lon": 72.8, "start": "2026-09-25T06:00:00Z", "end": "2026-09-25T05:00:00Z"}).status_code == 422


def test_route_endpoint(client):
    body = client.post("/api/route", json={"start_port": "goa", "end_lat": 15.45, "end_lon": 73.35,
                                            "departure": "2026-09-24T08:30:00Z"}).json()  # calm afternoon
    assert body["recommended"]["feasible"] and body["reasons"] and body["cost_function"]
    assert client.post("/api/route", json={"start_port": "nowhere", "end_lat": 15, "end_lon": 73}).status_code == 404


def test_risk_layer_grid(client):
    body = client.get("/api/layers/risk", params={"lat_min": 13, "lat_max": 17, "lon_min": 69, "lon_max": 74,
                                                  "time": "2026-09-25T12:00:00Z", "step": 0.5}).json()
    levels = {c["level"] for c in body["cells"]}
    assert "SEVERE" in levels and "LOW" in levels  # storm offshore, calm near the coast


def test_proactive_alert_after_time_advances(client):
    watch = client.post("/api/alerts/watch", json={"lat": 15.2, "lon": 72.8, "label": "Goa offshore"}).json()
    assert watch["alerts"] == []  # calm now
    advanced = client.post("/api/sim/advance", json={"hours": 12}).json()
    kinds = {a["kind"] for a in advanced["alerts"]}
    assert "risk_increase" in kinds and "new_advisory" in kinds
    alert = next(a for a in advanced["alerts"] if a["kind"] == "risk_increase")
    assert alert["simulated"] and alert["level"] in ("HIGH", "SEVERE") and alert["evidence_ids"]
    assert client.get("/api/alerts").json()["alerts"]
    assert client.post("/api/sim/reset").json()["offset_hours"] == 0.0


def test_vessel_tracking_geofence_alerts_once_per_status_change(client):
    first = client.post("/api/track", json={"vessel_id": "TN-01", "lat": 9.55, "lon": 79.40}).json()
    assert first["geofence"]["status"] == "approaching" and first["alert"]["kind"] == "geofence"
    again = client.post("/api/track", json={"vessel_id": "TN-01", "lat": 9.55, "lon": 79.41}).json()
    assert again["alert"] is None
    crossed = client.post("/api/track", json={"vessel_id": "TN-01", "lat": 9.40, "lon": 79.60}).json()
    assert crossed["geofence"]["status"] == "beyond_boundary" and crossed["alert"]["level"] == "SEVERE"


def test_static_ui_served_with_cache_headers(client):
    index = client.get("/")
    assert index.status_code == 200 and "no-cache" in index.headers["cache-control"]
    asset = client.get("/assets/app.js")
    assert "immutable" in asset.headers["cache-control"]
    assert client.get("/some/client/route").text.startswith("<!doctype html>")  # SPA fallback
    assert client.get("/api/does-not-exist").status_code == 404


def test_admin_token_gates_shared_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCA_ADMIN_TOKEN", "s3cret")
    svc = build_services(mode="replay", llm=NullProvider(), clock=SimClock(base=lambda: NOW))
    with TestClient(create_app(svc, alert_interval_s=0, web_dir=tmp_path)) as c:
        assert c.post("/api/sim/advance", json={"hours": 6}).status_code == 401
        assert c.post("/api/sim/reset", headers={"X-Orca-Admin-Token": "wrong"}).status_code == 401
        assert c.post("/api/sim/advance", json={"hours": 6}, headers={"X-Orca-Admin-Token": "s3cret"}).status_code == 200
        assert c.get("/api/health").status_code == 200  # reads stay open


def test_watch_limit(client):
    client.app.state.svc.alerts.max_watches = 1
    assert client.post("/api/alerts/watch", json={"lat": 15.2, "lon": 72.8}).status_code == 200
    assert client.post("/api/alerts/watch", json={"lat": 15.3, "lon": 72.8}).status_code == 429


class _Engine:
    def __init__(self, name, result=None, error=None):
        self.name, self.model, self.result, self.error, self.calls = name, "m", result, error, []

    async def transcribe(self, audio, mime, hint):
        from orca.speech import TranscriptionError

        self.calls.append((len(audio), mime, hint))
        if self.error:
            raise TranscriptionError(self.error)
        return self.result


def _stt_client(tmp_path, engines):
    from orca.speech import Transcriber

    svc = build_services(mode="replay", llm=NullProvider(), clock=SimClock(base=lambda: NOW))
    return TestClient(create_app(svc, alert_interval_s=0, web_dir=tmp_path, transcriber=Transcriber(engines)))


def test_transcribe_falls_back_to_next_engine(tmp_path):
    first, second = _Engine("gemini", error="rate limited"), _Engine("whisper", result=("कल सुबह गोवा", "hi"))
    with _stt_client(tmp_path, [first, second]) as c:
        assert c.get("/api/health").json()["stt"] == {"available": True, "engines": ["gemini:m", "whisper:m"]}
        body = c.post("/api/transcribe?language=hi", content=b"RIFF....", headers={"Content-Type": "audio/wav"}).json()
        assert body["text"] == "कल सुबह गोवा" and body["language"] == "hi" and body["engine"] == "whisper:m"
        assert first.calls == [(8, "audio/wav", "hi")]


def test_transcribe_rejects_bad_requests(tmp_path):
    with _stt_client(tmp_path, [_Engine("gemini", error="API error 500")]) as c:
        assert c.post("/api/transcribe", content=b"x", headers={"Content-Type": "text/plain"}).status_code == 415
        assert c.post("/api/transcribe", content=b"", headers={"Content-Type": "audio/wav"}).status_code == 422
        failed = c.post("/api/transcribe", content=b"x", headers={"Content-Type": "audio/wav"})
        assert failed.status_code == 502 and "API error 500" in failed.json()["detail"]
    with _stt_client(tmp_path, []) as c:
        assert c.post("/api/transcribe", content=b"x", headers={"Content-Type": "audio/wav"}).status_code == 503


class _Speaker:
    name, model = "fake-tts", "m"

    def __init__(self, error=None):
        self.error, self.calls = error, []

    async def speak(self, text, language):
        from orca.speech import SpeechError, pcm_to_wav

        self.calls.append((text, language))
        if self.error:
            raise SpeechError(self.error)
        return pcm_to_wav(b"\x00\x00" * 240)


def _tts_client(tmp_path, speaker):
    svc = build_services(mode="replay", llm=NullProvider(), clock=SimClock(base=lambda: NOW))
    return TestClient(create_app(svc, alert_interval_s=0, web_dir=tmp_path, speaker=speaker))


def test_speak_returns_wav(tmp_path):
    sp = _Speaker()
    with _tts_client(tmp_path, sp) as c:
        assert c.get("/api/health").json()["tts"] == {"available": True, "engine": "fake-tts:m"}
        r = c.post("/api/speak", json={"text": "கடலுக்குச் செல்ல வேண்டாம்", "language": "ta"})
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
        assert sp.calls == [("கடலுக்குச் செல்ல வேண்டாம்", "ta")]


def test_speak_errors(tmp_path):
    with _tts_client(tmp_path, _Speaker(error="rate limited")) as c:
        assert c.post("/api/speak", json={"text": "hi", "language": "en"}).status_code == 502
        assert c.post("/api/speak", json={"text": "x" * 601, "language": "en"}).status_code == 422
    with _tts_client(tmp_path, None) as c:
        assert c.post("/api/speak", json={"text": "hi", "language": "en"}).status_code == 503
