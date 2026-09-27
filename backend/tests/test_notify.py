import asyncio
import os
from urllib.parse import parse_qs

os.environ.setdefault("ORCA_NO_DEFAULT_APP", "1")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from orca.alerts import Alert  # noqa: E402
from orca.api import create_app  # noqa: E402
from orca.llm import NullProvider  # noqa: E402
from orca.notify import OutboxSender, TwilioSender, mask  # noqa: E402
from orca.services import build_services  # noqa: E402

PHONE = "+919876543210"


def _client(tmp_path):
    svc = build_services(mode="historical", llm=NullProvider(), event_id="tauktae-2021")
    return svc, TestClient(create_app(svc, alert_interval_s=0, web_dir=tmp_path, sender=OutboxSender()))


def test_subscribe_sends_welcome_in_language_and_masks_the_number(tmp_path):
    svc, c = _client(tmp_path)
    with c:
        r = c.post("/api/subscriptions", json={"phone": PHONE, "channel": "whatsapp", "harbour_id": "goa", "language": "hi"})
        assert r.status_code == 200, r.text
        sub = r.json()
        assert sub["phone"] == mask(PHONE) and PHONE not in r.text and sub["provider"] == "outbox"
        out = c.get("/api/outbox").json()
        assert out["messages"][-1]["body"].startswith("Vasco / Mormugao (Goa) के लिए ORCA अलर्ट चालू")
        assert all(PHONE not in m["to"] for m in out["messages"])
        assert c.post("/api/subscriptions", json={"phone": "98765", "harbour_id": "goa"}).status_code == 422
        assert c.post("/api/subscriptions", json={"phone": PHONE, "harbour_id": "atlantis"}).status_code == 404
        assert c.delete(f"/api/subscriptions/{sub['id']}").status_code == 200


def test_alerts_for_the_watch_reach_the_phone(tmp_path):
    svc, c = _client(tmp_path)
    with c:
        sub = c.post("/api/subscriptions", json={"phone": PHONE, "harbour_id": "goa"}).json()
        before = len(c.get("/api/outbox").json()["messages"])
        alert = Alert(id="a1", kind="risk_increase", level="HIGH", title="t", message="Goa: risk rose to HIGH from 06:00.",
                      lat=15.3, lon=73.6, watch_id=sub["watch_id"], created_at=svc.clock())
        asyncio.run(svc.alerts.publish(alert))
        msgs = c.get("/api/outbox").json()["messages"]
        assert len(msgs) == before + 1 and msgs[0]["body"] == "ORCA: Goa: risk rose to HIGH from 06:00."


def test_twilio_sender_builds_sms_and_whatsapp_requests(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), parse_qs(request.content.decode()), request.headers.get("authorization", "")))
        return httpx.Response(201, json={"sid": "SM1"})

    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("TWILIO_SMS_FROM", "+15550001111")
    monkeypatch.setenv("TWILIO_WHATSAPP_FROM", "+14155238886")
    s = TwilioSender(transport=httpx.MockTransport(handler))
    assert asyncio.run(s.send(PHONE, "sms", "hello")) is None
    assert asyncio.run(s.send(PHONE, "whatsapp", "hello")) is None
    (url1, sms, auth), (_, wa, _) = seen
    assert url1.endswith("/Accounts/AC123/Messages.json") and auth.startswith("Basic ")
    assert sms["To"] == [PHONE] and sms["From"] == ["+15550001111"]
    assert wa["To"] == [f"whatsapp:{PHONE}"] and wa["From"] == ["whatsapp:+14155238886"]
