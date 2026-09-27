"""Alert Engine (guide §19–20): proactive, event-driven — not a chat reply.

New forecast / advisory → re-evaluate each watched location → compare with the
previous state → alert only when risk rises materially or a new warning covers
the point → push (SSE) with evidence and data age.

Vessel tracking: each reported position is geofenced; entering/approaching a
boundary or restricted area raises an immediate alert (once per status change)."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from .geo.geofences import SEVERITY_ORDER
from .i18n.messages import t, template_language
from .risk.engine import assess_window
from .risk.rules import RANK, RiskLevel
from .timeutil import IST, ensure_utc

log = logging.getLogger("orca.alerts")

LOOKAHEAD = timedelta(hours=12)


class Watch(BaseModel):
    id: str
    lat: float
    lon: float
    label: str
    language: str = "en"
    created_at: datetime
    last_level: str | None = None
    last_advisories: list[str] = Field(default_factory=list)
    last_evaluated: datetime | None = None


class Alert(BaseModel):
    id: str
    kind: str  # risk_increase | risk_high | new_advisory | geofence
    level: str
    title: str
    message: str
    lat: float
    lon: float
    watch_id: str | None = None
    vessel_id: str | None = None
    created_at: datetime
    data_retrieved_at: datetime | None = None
    simulated: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    source: str = ""


class AlertEngine:
    def __init__(self, svc, max_alerts: int = 200, max_watches: int = 500, max_vessels: int = 5000) -> None:
        self.svc = svc
        self.max_watches, self.max_vessels = max_watches, max_vessels
        self.watches: dict[str, Watch] = {}
        self.alerts: deque[Alert] = deque(maxlen=max_alerts)
        self._subscribers: set[asyncio.Queue] = set()
        self._vessel_status: dict[str, str] = {}
        self._task: asyncio.Task | None = None
        self.listeners: list = []  # async callables(alert) — e.g. SMS/WhatsApp delivery

    # ---- subscriptions (SSE) ---------------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def publish(self, alert: Alert) -> None:
        self.alerts.append(alert)
        for q in list(self._subscribers):
            try:
                q.put_nowait(alert)
            except asyncio.QueueFull:
                pass
        for listener in list(self.listeners):
            try:
                await listener(alert)
            except Exception as exc:  # a failed SMS must never stop the alert itself
                log.warning("alert listener failed: %s", exc)

    # ---- watches -------------------------------------------------------------------------
    def add_watch(self, lat: float, lon: float, label: str, language: str = "en") -> Watch:
        w = Watch(id=uuid.uuid4().hex[:10], lat=lat, lon=lon, label=label, language=language, created_at=self.svc.clock())
        self.watches[w.id] = w
        return w

    def clear(self) -> None:
        """Forget watches, alerts and vessel states (the replay moved to another event)."""
        self.watches.clear()
        self.alerts.clear()
        self._vessel_status.clear()

    def remove_watch(self, watch_id: str) -> bool:
        return self.watches.pop(watch_id, None) is not None

    async def evaluate_watch(self, w: Watch) -> list[Alert]:
        now = self.svc.clock()
        state, status = await self.svc.data.marine_state(w.lat, w.lon, now, now + LOOKAHEAD)
        decision = assess_window(state, now, now + LOOKAHEAD, now)
        lang = template_language(w.language)
        level = decision.risk_level
        worst = decision.worst_hour
        if worst and worst.dominant:
            d = worst.dominant
            value = f"{d.value:.1f}" if isinstance(d.value, (int, float)) else d.value
            factor = t(f"factor.{d.variable}", template_language(w.language), value=value)
        else:
            factor = "—"
        when = ensure_utc(worst.time).astimezone(IST).strftime("%H:%M") if worst else "—"
        simulated = decision.simulated
        retrieved = min((o.retrieved_at for o in state.observations), default=None)
        out: list[Alert] = []

        def make(kind: str, lvl: str, title: str, message: str, source: str, evidence: list[str]) -> Alert:
            return Alert(id=uuid.uuid4().hex[:12], kind=kind, level=lvl, title=title, message=message, lat=w.lat, lon=w.lon,
                         watch_id=w.id, created_at=now, data_retrieved_at=retrieved, simulated=simulated,
                         evidence_ids=evidence, source=source)

        known = level != RiskLevel.INSUFFICIENT_DATA
        prev_rank = RANK.get(RiskLevel(w.last_level), -1) if w.last_level else None
        if known and prev_rank is not None and RANK[level] > prev_rank:
            out.append(make("risk_increase", level.value, f"Risk rising — {w.label}",
                            t("alert.risk_increase", lang, place=w.label, old=t(f"level.{w.last_level}", lang),
                              new=t(f"level.{level.value}", lang), factor=factor, time=when),
                            decision.rule_version, decision.evidence_ids[:5]))
        elif known and prev_rank is None and RANK[level] >= RANK[RiskLevel.HIGH]:
            out.append(make("risk_high", level.value, f"{level.value} risk ahead — {w.label}",
                            t("alert.risk_high", lang, place=w.label, new=t(f"level.{level.value}", lang), factor=factor, time=when),
                            decision.rule_version, decision.evidence_ids[:5]))

        covering = {a.id: a for a in state.advisories_containing_point() if a.expires is None or a.expires > now}
        for adv_id, adv in covering.items():
            if adv_id not in w.last_advisories:
                out.append(make("new_advisory", adv.severity, f"New warning — {w.label}",
                                t("alert.new_advisory", lang, place=w.label, headline=adv.headline or adv.event,
                                  severity=adv.severity, source=adv.source),
                                adv.source, [adv.id]))
        w.last_level = level.value if known else w.last_level
        w.last_advisories = list(covering)
        w.last_evaluated = now
        for a in out:
            await self.publish(a)
        return out

    async def evaluate_all(self) -> list[Alert]:
        out: list[Alert] = []
        for w in list(self.watches.values()):
            try:
                out.extend(await self.evaluate_watch(w))
            except Exception as exc:  # one failing watch must not stop the others
                log.warning("watch %s evaluation failed: %s", w.id, exc)
        return out

    # ---- vessel geofencing ------------------------------------------------------------
    async def track(self, vessel_id: str, lat: float, lon: float, language: str = "en") -> tuple[dict, Alert | None]:
        now = self.svc.clock()
        advisories, _, _ = await self.svc.data.advisories(self.svc.current_source())
        status = self.svc.geofences.check(lat, lon, now, advisories)
        previous = self._vessel_status.pop(vessel_id, "clear")
        self._vessel_status[vessel_id] = status.status  # re-inserted, so the dict stays ordered oldest report first
        while len(self._vessel_status) > self.max_vessels:
            del self._vessel_status[next(iter(self._vessel_status))]
        alert = None
        worsened = SEVERITY_ORDER.index(status.status) > SEVERITY_ORDER.index(previous)
        if status.status != "clear" and (worsened or status.status != previous):
            hit = next((h for h in status.hits if h.relation in ("inside", "beyond")), None) or status.hits[0]
            lang = template_language(language)
            key = {"inside": "geofence.inside", "beyond": "geofence.beyond"}.get(hit.relation, "geofence.approaching")
            text = t(key, lang, name=hit.name, rule=hit.rule, distance=f"{hit.distance_km:.1f}")
            level = "SEVERE" if status.status in ("beyond_boundary", "inside_restricted") else ("HIGH" if status.status == "inside_protected" else "MODERATE")
            alert = Alert(id=uuid.uuid4().hex[:12], kind="geofence", level=level, title=f"Geofence — {vessel_id}",
                          message=t("alert.geofence", lang, vessel=vessel_id, text=text), lat=lat, lon=lon, vessel_id=vessel_id,
                          created_at=now, source=f"{hit.authority} (accuracy: {hit.accuracy})")
            await self.publish(alert)
        return status.model_dump(mode="json"), alert

    # ---- background loop ------------------------------------------------------------
    def start(self, interval_s: float) -> None:
        if self._task is None and interval_s > 0:
            self._task = asyncio.create_task(self._run(interval_s))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            await self.evaluate_all()
