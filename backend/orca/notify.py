"""Alerts to phones: SMS and WhatsApp (a fisherman at sea has a basic phone, not a browser).

Subscribing a phone to a harbour creates a watch in the alert engine for the sea off that harbour, in the
subscriber's language; every alert the engine raises for that watch (risk rising, a new official warning)
is sent to the phone. Messages are the alert engine's own localized text, shortened for SMS.

Delivery: Twilio (SMS, and WhatsApp through Twilio's WhatsApp sender) when TWILIO_ACCOUNT_SID,
TWILIO_AUTH_TOKEN and TWILIO_SMS_FROM / TWILIO_WHATSAPP_FROM are set; otherwise an outbox that records
what would have been sent (for demos). Phone numbers are personal data: kept in memory only, never logged,
and masked in every API response."""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections import deque
from datetime import datetime

import httpx
from pydantic import BaseModel

from .i18n.messages import t, template_language

log = logging.getLogger("orca.notify")

PHONE = re.compile(r"^\+[1-9]\d{7,14}$")  # E.164
SMS_LIMIT = 306  # two concatenated GSM segments; Indian scripts use UCS-2 and cost more, so keep it short
MAX_SUBSCRIPTIONS = 1000


def mask(phone: str) -> str:
    return phone[:3] + "•" * max(0, len(phone) - 7) + phone[-4:]


class Subscription(BaseModel):
    id: str
    phone: str
    channel: str  # sms | whatsapp
    language: str
    harbour_id: str
    harbour: str
    watch_id: str
    created_at: datetime

    def public(self) -> dict:
        return self.model_dump(mode="json") | {"phone": mask(self.phone)}


class SentMessage(BaseModel):
    to: str  # masked
    channel: str
    body: str
    sent_at: datetime
    provider: str
    ok: bool
    error: str | None = None


class TwilioSender:
    name = "twilio"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.sid = os.environ["TWILIO_ACCOUNT_SID"]
        self.token = os.environ["TWILIO_AUTH_TOKEN"]
        self.sms_from = os.getenv("TWILIO_SMS_FROM")
        self.wa_from = os.getenv("TWILIO_WHATSAPP_FROM")
        self._client = httpx.AsyncClient(timeout=20, auth=(self.sid, self.token), transport=transport)

    async def send(self, phone: str, channel: str, body: str) -> str | None:
        """Returns None on success, else a short error (no phone numbers in it)."""
        sender = self.wa_from if channel == "whatsapp" else self.sms_from
        if not sender:
            return f"no Twilio sender configured for {channel}"
        to, frm = (f"whatsapp:{phone}", f"whatsapp:{sender}") if channel == "whatsapp" else (phone, sender)
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Messages.json"
        try:
            r = await self._client.post(url, data={"To": to, "From": frm, "Body": body})
        except httpx.HTTPError as exc:
            return f"connection error: {type(exc).__name__}"
        if r.status_code >= 300:
            log.warning("twilio error %s", r.status_code)  # the body may echo the number: not logged
            return f"Twilio error {r.status_code}"
        return None


class OutboxSender:
    """No provider configured: record what would be sent, so the flow can be shown end to end."""

    name = "outbox"

    async def send(self, phone: str, channel: str, body: str) -> str | None:
        return None


def sender_from_env():
    return TwilioSender() if os.getenv("TWILIO_ACCOUNT_SID") and os.getenv("TWILIO_AUTH_TOKEN") else OutboxSender()


class Notifier:
    def __init__(self, alerts, sender=None) -> None:
        self.alerts = alerts
        self.sender = sender or sender_from_env()
        self.subs: dict[str, Subscription] = {}
        self.outbox: deque[SentMessage] = deque(maxlen=200)
        alerts.listeners.append(self.on_alert)

    async def subscribe(self, phone: str, channel: str, port, language: str, lat: float, lon: float, verdict: str | None) -> Subscription:
        if not PHONE.match(phone):
            raise ValueError("phone must be in international format, e.g. +919876543210")
        if channel not in ("sms", "whatsapp"):
            raise ValueError("channel must be sms or whatsapp")
        if len(self.subs) >= MAX_SUBSCRIPTIONS:
            raise OverflowError("subscription limit reached")
        lang = template_language(language)
        watch = self.alerts.add_watch(lat, lon, port.name, lang)
        sub = Subscription(id=uuid.uuid4().hex[:10], phone=phone, channel=channel, language=lang, harbour_id=port.id,
                           harbour=port.name, watch_id=watch.id, created_at=self.alerts.svc.clock())
        self.subs[sub.id] = sub
        level = t(f"level.{verdict}", lang) if verdict else "—"
        await self._send(sub, t("sms.welcome", lang, place=port.name, level=level))
        await self.alerts.evaluate_watch(watch)  # anything already dangerous is sent straight away
        return sub

    def unsubscribe(self, sub_id: str) -> bool:
        sub = self.subs.pop(sub_id, None)
        if sub is None:
            return False
        self.alerts.remove_watch(sub.watch_id)
        return True

    async def on_alert(self, alert) -> None:
        for sub in list(self.subs.values()):
            if alert.watch_id == sub.watch_id:
                await self._send(sub, f"ORCA: {alert.message}")

    async def _send(self, sub: Subscription, body: str) -> None:
        body = body if len(body) <= SMS_LIMIT else body[: SMS_LIMIT - 1] + "…"
        error = await self.sender.send(sub.phone, sub.channel, body)
        self.outbox.appendleft(SentMessage(to=mask(sub.phone), channel=sub.channel, body=body, sent_at=self.alerts.svc.clock(),
                                           provider=self.sender.name, ok=error is None, error=error))
