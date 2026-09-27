"""ORCA HTTP API (FastAPI). Run:  uvicorn orca.api:app --reload  (from backend/)

The UI consumes structured fields only (guide §27 contract rule): risk levels,
factors, windows, evidence and map features — never parsed from answer text."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .agents.orchestrator import ChatRequest, ChatResponse, Orchestrator
from .agents.specialists import summarize_decision
from .alerts import AlertEngine
from .data_service import ROUTE_VARIABLES
from .geo.ports import PORTS, offshore_point, snap_to_sea
from .historical.events import EVENTS
from .historical.layers import field_layers, replay_timeline
from .pfz import rank_zones
from .risk import assess_window, rules_table
from .risk.engine import assess_hour
from .route import plan_route
from .services import Services, build_services
from .notify import Notifier
from .speech import MAX_AUDIO_BYTES, MAX_SPEECH_CHARS, GeminiSpeaker, SpeechError, Transcriber, TranscriptionError, speaker_from_env, transcriber_from_env
from .state import advisories_at_point
from .timeutil import ensure_utc, floor_hour

log = logging.getLogger("orca.api")

# Windows takes MIME types from the registry, which can map .js to text/plain; browsers refuse service workers
# and modules served that way, and expect the app manifest as manifest+json.
import mimetypes  # noqa: E402

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/manifest+json", ".webmanifest")
DEFAULT_WEB_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


class RouteRequest(BaseModel):
    start_lat: float | None = Field(default=None, ge=-90, le=90)
    start_lon: float | None = Field(default=None, ge=-180, le=180)
    start_port: str | None = None
    end_lat: float = Field(ge=-90, le=90)
    end_lon: float = Field(ge=-180, le=180)
    departure: datetime | None = None
    speed_knots: float = Field(default=8.0, gt=0.5, le=40)


class WatchRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    label: str = Field(default="watched location", max_length=80)
    language: str = Field(default="en", max_length=10)


class TrackRequest(BaseModel):
    vessel_id: str = Field(min_length=1, max_length=40)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    language: str = "en"


class ReplayEventRequest(BaseModel):
    event_id: str
    as_of: datetime | None = None


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_SPEECH_CHARS)
    language: str = Field(default="en", pattern="^[a-z]{2}$")


class SubscribeRequest(BaseModel):
    phone: str = Field(min_length=8, max_length=16)
    channel: str = Field(default="sms", pattern="^(sms|whatsapp)$")
    harbour_id: str = Field(min_length=2, max_length=30)
    language: str = Field(default="en", pattern="^[a-z]{2}$")


class AdvanceRequest(BaseModel):
    hours: float = Field(gt=-72, le=72)


def _feature(geometry: dict, **props) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": props}


def _geom(coords: list[tuple[float, float]], kind: str) -> dict:
    ring = [[lon, lat] for lat, lon in coords]
    if kind == "line":
        return {"type": "LineString", "coordinates": ring}
    if ring and ring[0] != ring[-1]:
        ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


def create_app(svc: Services | None = None, alert_interval_s: float | None = None, web_dir: Path | None = None,
               transcriber: Transcriber | None = None, speaker: GeminiSpeaker | None | bool = True, sender=None) -> FastAPI:
    svc = svc or build_services()
    stt = transcriber or transcriber_from_env()
    tts = speaker_from_env() if speaker is True else (speaker or None)
    alerts = AlertEngine(svc)
    svc.alerts = alerts
    notifier = Notifier(alerts, sender)
    orchestrator = Orchestrator(svc)
    interval = alert_interval_s if alert_interval_s is not None else float(os.getenv("ORCA_ALERT_INTERVAL_S", "300"))
    web_dir = web_dir or Path(os.getenv("ORCA_WEB_DIR", DEFAULT_WEB_DIR))
    admin_token = os.getenv("ORCA_ADMIN_TOKEN")

    def require_admin(x_orca_admin_token: str | None = Header(default=None)) -> None:
        """Clock, replay and re-evaluation are shared by every user. With ORCA_ADMIN_TOKEN set, only holders of the
        token may change them; unset (local demo), they stay open."""
        if admin_token and not secrets.compare_digest(x_orca_admin_token or "", admin_token):
            raise HTTPException(401, "admin token required (X-Orca-Admin-Token header)")

    admin = [Depends(require_admin)]

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        alerts.start(interval)
        yield
        await alerts.stop()

    app = FastAPI(title="ORCA — Marine EcOsystem Reasoning with Collaborative Agents", version=__version__, lifespan=lifespan)
    app.state.svc = svc

    @app.middleware("http")
    async def cache_headers(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # content-hashed by Vite
        elif not path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"  # index.html always revalidated
        return response

    # ---- system --------------------------------------------------------------------------
    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "version": __version__,
            "data_mode": svc.mode,
            "last_marine_source": svc.data.last_status.marine_source if svc.data.last_status else None,
            "clock": svc.clock().isoformat(),
            "clock_offset_hours": svc.clock.offset.total_seconds() / 3600,
            "scenario": {"name": svc.scenario.name, "title": svc.scenario.title, "day1_starts": svc.scenario.anchor.isoformat()},
            "replay": _replay_info(),
            "llm": {"provider": svc.llm.name, "model": svc.llm.model, "available": svc.llm.available},
            "stt": stt.describe(),
            "tts": {"available": tts is not None, "engine": f"{tts.name}:{tts.model}" if tts else None},
            "notify": {"provider": notifier.sender.name, "subscriptions": len(notifier.subs)},
            "adapters": [h.model_dump(mode="json") for h in svc.data.health()]
            + [p.health().model_dump(mode="json") for p in (svc.pfz_live, svc.pfz_demo, svc.pfz_historical) if p is not None],
            "watches": len(alerts.watches),
        }

    @app.get("/api/rules")
    async def rules():
        return rules_table()

    @app.get("/api/ports")
    async def ports():
        return [p.model_dump() | {"sea_point": snap_to_sea(p.lat, p.lon)} for p in PORTS]

    @app.get("/api/geofences")
    async def geofences():
        return {"type": "FeatureCollection", "features": [
            _feature(_geom(f.coordinates, f.geometry), id=f.id, name=f.name, kind=f.kind, authority=f.authority,
                     accuracy=f.accuracy, accuracy_note=f.accuracy_note, rule=f.rule, active_months=f.active_months)
            for f in svc.geofences.static
        ]}

    @app.get("/api/advisories")
    async def advisories(source: str = Query("auto", pattern="^(auto|live|replay|historical)$")):
        marine_source = source if source != "auto" else svc.current_source()
        items, used, errors = await svc.data.advisories(marine_source)
        return {"used": used, "errors": errors, "type": "FeatureCollection", "features": [
            _feature(_geom(poly, "polygon"), id=a.id, event=a.event, headline=a.headline, severity=a.severity, source=a.source,
                     data_type=a.data_type.value, onset=a.onset and a.onset.isoformat(), expires=a.expires and a.expires.isoformat(),
                     area=a.area_desc, reference=a.reference)
            for a in items for poly in a.polygons
        ]}

    # ---- conversational endpoint -------------------------------------------------------
    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest):
        if not req.message.strip():
            raise HTTPException(422, "message must not be empty")
        return await orchestrator.handle(req)

    @app.post("/api/speak")
    async def speak(req: SpeakRequest):
        """Read a short answer aloud (WAV) — for languages the phone has no voice for."""
        if tts is None:
            raise HTTPException(503, "no speech engine configured (set GEMINI_API_KEY)")
        try:
            wav = await tts.speak(req.text.strip(), req.language)
        except SpeechError as exc:
            raise HTTPException(502, f"speech failed: {exc}") from exc
        return Response(content=wav, media_type="audio/wav", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/api/transcribe")
    async def transcribe(request: Request, language: str | None = Query(None, pattern="^[a-z]{2}$")):
        """Raw audio body (audio/wav from the UI) → text in the speaker's language and script."""
        if not stt.engines:
            raise HTTPException(503, "no speech engine configured (set GEMINI_API_KEY or GROQ_API_KEY)")
        mime = request.headers.get("content-type", "").split(";")[0].strip()
        if not mime.startswith("audio/"):
            raise HTTPException(415, "send the recording as an audio/* request body")
        audio = await request.body()
        if not audio:
            raise HTTPException(422, "empty recording")
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "recording too long")
        try:
            result = await stt.transcribe(audio, mime, language)
        except TranscriptionError as exc:
            raise HTTPException(502, f"transcription failed: {exc}") from exc
        return {"text": result.text, "language": result.language, "engine": result.engine, "latency_ms": result.latency_ms}

    # ---- direct endpoints (structured, no LLM) -------------------------------------------
    @app.get("/api/risk")
    async def risk(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
                   start: datetime | None = None, end: datetime | None = None):
        now = svc.clock()
        start = ensure_utc(start) if start else now
        end = ensure_utc(end) if end else start + timedelta(hours=12)
        if end <= start or end - start > timedelta(hours=120):
            raise HTTPException(422, "end must be after start and within 120 h")
        state, status = await svc.data.marine_state(lat, lon, floor_hour(start), end)
        g = svc.geofences.check(lat, lon, start, state.advisories)
        decision = assess_window(state, start, end, now, hard_constraints=g.hard_constraints)
        index = state.evidence_index()
        return {"decision": summarize_decision(decision), "geofence": g.model_dump(mode="json"),
                "evidence": [index[e].model_dump(mode="json") for e in decision.evidence_ids if e in index],
                "data_status": status.model_dump(mode="json"), "simulated": decision.simulated}

    @app.get("/api/conditions")
    async def conditions(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), hours: int = Query(48, ge=6, le=48)):
        """Hourly series of every variable at a point (6 h back for context, then ahead), with hourly risk levels."""
        now = svc.clock()
        start, end = floor_hour(now) - timedelta(hours=6), floor_hour(now) + timedelta(hours=hours)
        state, status = await svc.data.marine_state(lat, lon, start, end)
        series = {}
        for var in ("wave_height", "wave_period", "wind_speed", "wind_gusts", "wind_direction", "visibility", "precipitation",
                    "weather_code", "sea_surface_temperature", "chlorophyll", "sea_level", "current_speed"):
            pts = state.series(var)
            if any(v is not None for _, v in pts):
                series[var] = [{"t": t.isoformat(), "v": None if v is None else round(float(v), 3)} for t, v in pts]
        decision = assess_window(state, start, end, now)
        return {
            "now": now.isoformat(),
            "lat": lat, "lon": lon,
            "series": series,
            "levels": [{"t": h.time.isoformat(), "level": h.level.value, "dominant": h.dominant.variable if h.dominant else None}
                       for h in decision.hours],
            "sources": [s | {"retrieved_at": s["retrieved_at"].isoformat() if hasattr(s.get("retrieved_at"), "isoformat") else s.get("retrieved_at")}
                        for s in state.sources()],
            "advisories": [{"id": a.id, "event": a.event, "headline": a.headline, "severity": a.severity, "source": a.source,
                            "data_type": a.data_type.value} for a in state.advisories_containing_point()],
            "data_status": status.model_dump(mode="json"),
        }

    @app.get("/api/pfz")
    async def pfz(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), limit: int = Query(5, ge=1, le=20)):
        from .agents.specialists import pfz_agent
        res = await pfz_agent(svc, lat, lon, svc.clock(), svc.offline_source or "live", limit)
        return {"provider": res.value["provider"], "note": res.value["note"],
                "candidates": [c.model_dump(mode="json") for c in res.value["candidates"]]}

    @app.post("/api/route")
    async def route(req: RouteRequest):
        if req.start_port:
            port = next((p for p in PORTS if p.id == req.start_port), None)
            if port is None:
                raise HTTPException(404, f"unknown port {req.start_port}")
            start = snap_to_sea(port.lat, port.lon)
        elif req.start_lat is not None and req.start_lon is not None:
            start = snap_to_sea(req.start_lat, req.start_lon)
        else:
            raise HTTPException(422, "give start_port or start_lat/start_lon")
        departure = ensure_utc(req.departure) if req.departure else svc.clock()
        marine_source = svc.offline_source or "live"
        advisories, _, _ = await svc.data.advisories(marine_source)
        try:
            result = await plan_route(svc.data, svc.geofences, start, (req.end_lat, req.end_lon), departure, req.speed_knots,
                                      advisories, marine_source)
        except Exception as exc:
            if svc.mode != "auto":
                raise HTTPException(503, f"route data unavailable: {exc}") from exc
            advisories, _, _ = await svc.data.advisories("replay")
            result = await plan_route(svc.data, svc.geofences, start, (req.end_lat, req.end_lon), departure, req.speed_knots,
                                      advisories, "replay")
        return result.model_dump(mode="json")

    @app.get("/api/geofence/check")
    async def geofence_check(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
        return svc.geofences.check(lat, lon, svc.clock()).model_dump(mode="json")

    @app.get("/api/layers/risk")
    async def risk_layer(lat_min: float, lat_max: float, lon_min: float, lon_max: float, time: datetime | None = None,
                         step: float = Query(0.25, ge=0.05, le=2.0)):
        """Risk-level grid for the map overlay at one hour (replay is cheap; live is capped to 300 cells)."""
        if lat_max <= lat_min or lon_max <= lon_min:
            raise HTTPException(422, "invalid bbox")
        marine_source = svc.current_source()
        cells = ((lat_max - lat_min) / step + 1) * ((lon_max - lon_min) / step + 1)
        cap = 2500 if marine_source in ("replay", "historical") else 300
        while cells > cap:
            step *= 1.5
            cells = ((lat_max - lat_min) / step + 1) * ((lon_max - lon_min) / step + 1)
        t = floor_hour(ensure_utc(time) if time else svc.clock())
        pts = [(round(lat_min + i * step, 3), round(lon_min + j * step, 3))
               for i in range(int((lat_max - lat_min) / step) + 1) for j in range(int((lon_max - lon_min) / step) + 1)]
        lookup = await svc.data.route_values(pts, t, t, marine_source, ROUTE_VARIABLES)
        advisories, _, _ = await svc.data.advisories(marine_source)
        out = []
        for lat, lon in pts:
            vals = lookup(lat, lon, t)
            if not vals or vals.get("wave_height") is None or vals["wave_height"].value is None:
                continue
            h = assess_hour(t, vals, advisories_at_point(lat, lon, t, advisories))
            out.append({"lat": lat, "lon": lon, "level": h.level.value, "dominant": h.dominant.variable if h.dominant else None})
        return {"time": t.isoformat(), "step": step, "marine_source": marine_source, "cells": out}

    # ---- harbour board + bulletin (authorities) -------------------------------------------
    @app.get("/api/board")
    async def board(day: str = Query("tomorrow", pattern="^(today|tomorrow)$"),
                    part: str = Query("morning", pattern="^(now|morning|afternoon|evening|night)$")):
        from .board import harbour_board

        return await harbour_board(svc, day, part, svc.clock())

    @app.get("/api/bulletin")
    async def bulletin_endpoint(day: str = Query("tomorrow", pattern="^(today|tomorrow)$"),
                                part: str = Query("morning", pattern="^(now|morning|afternoon|evening|night)$"),
                                language: str = Query("en", pattern="^[a-z]{2}$")):
        from .board import bulletin, harbour_board

        b = await harbour_board(svc, day, part, svc.clock())
        return bulletin(b, language) | {"board": b}

    # ---- historical replay ("time machine") ---------------------------------------------
    def _replay_info() -> dict | None:
        if svc.replay is None:
            return None
        ev = svc.replay.event
        return {"event": ev.id, "title": ev.title, "kind": ev.kind, "as_of": svc.clock().isoformat(),
                "start": ev.replay_start.isoformat(), "end": ev.replay_end.isoformat(),
                "place": {"lat": ev.place[0], "lon": ev.place[1], "label": ev.place[2]}}

    def _need_replay():
        if svc.replay is None:
            raise HTTPException(409, "historical replay is off (start with ORCA_DATA_MODE=historical)")
        return svc.replay

    @app.get("/api/replay/events")
    async def replay_events():
        from .historical.archive import EventArchive
        out = []
        for ev in EVENTS.values():
            arc = EventArchive(ev)
            out.append({
                "id": ev.id, "title": ev.title, "kind": ev.kind, "summary": ev.summary, "region": ev.region,
                "start": ev.replay_start.isoformat(), "end": ev.replay_end.isoformat(),
                "default_as_of": ev.default_as_of.isoformat(), "tags": ev.tags,
                "place": {"lat": ev.place[0], "lon": ev.place[1], "label": ev.place[2]}, "language_hint": ev.language_hint,
                "available": arc.available, "products": arc.meta.get("products", {}) if arc.available else {},
            })
        return {"active": _replay_info(), "events": out}

    @app.post("/api/replay/event", dependencies=admin)
    async def replay_set_event(req: ReplayEventRequest):
        _need_replay()
        ev = EVENTS.get(req.event_id)
        if ev is None:
            raise HTTPException(404, f"unknown event {req.event_id}")
        as_of = ensure_utc(req.as_of) if req.as_of else None
        if as_of is not None and not (ev.replay_start <= as_of <= ev.replay_end):
            raise HTTPException(422, f"as_of must be between {ev.replay_start.isoformat()} and {ev.replay_end.isoformat()}")
        svc.set_event(ev.id, as_of)
        alerts.clear()
        return _replay_info()

    @app.get("/api/replay/timeline")
    async def replay_timeline_endpoint():
        ctx = _need_replay()
        cap = next(a for a in svc.data.historical_advisories if a.name == "imd-cap-archive")
        return replay_timeline(ctx.archive, cap, svc.clock())

    @app.get("/api/layers/fields")
    async def layer_fields(fields: str = "wind,waves", time: datetime | None = None):
        """Gridded archive fields for animated map layers (historical mode)."""
        ctx = _need_replay()
        which = {f.strip() for f in fields.split(",") if f.strip()} & {"wind", "waves", "sst", "chl"}
        t = floor_hour(ensure_utc(time) if time else svc.clock())
        return field_layers(ctx.archive, t, svc.clock(), which)

    @app.get("/api/backtest")
    async def backtest():
        path = Path(__file__).resolve().parents[1] / "data" / "backtest" / "results.json"
        if not path.exists():
            raise HTTPException(404, "no backtest results yet: run python scripts/historical/backtest.py")
        return json.loads(path.read_text(encoding="utf-8"))

    @app.get("/api/sea-point")
    async def sea_point(lat: float, lon: float):
        return {"offshore": offshore_point(lat, lon), "snapped": snap_to_sea(lat, lon)}

    # ---- traces -------------------------------------------------------------------------
    @app.get("/api/traces")
    async def traces(n: int = Query(20, ge=1, le=200)):
        return [tr.model_dump(mode="json") for tr in svc.traces.recent(n)]

    @app.get("/api/traces/{request_id}")
    async def trace(request_id: str):
        tr = svc.traces.get(request_id)
        if tr is None:
            raise HTTPException(404, "trace not found")
        return tr.model_dump(mode="json")

    # ---- alerts ---------------------------------------------------------------------------
    @app.get("/api/alerts")
    async def list_alerts():
        return {"alerts": [a.model_dump(mode="json") for a in reversed(alerts.alerts)],
                "watches": [w.model_dump(mode="json") for w in alerts.watches.values()]}

    @app.post("/api/alerts/watch")
    async def add_watch(req: WatchRequest):
        if len(alerts.watches) >= alerts.max_watches:
            raise HTTPException(429, f"watch limit reached ({alerts.max_watches}); delete a watch first")
        w = alerts.add_watch(req.lat, req.lon, req.label, req.language)
        fired = await alerts.evaluate_watch(w)
        return {"watch": w.model_dump(mode="json"), "alerts": [a.model_dump(mode="json") for a in fired]}

    @app.delete("/api/alerts/watch/{watch_id}")
    async def delete_watch(watch_id: str):
        if not alerts.remove_watch(watch_id):
            raise HTTPException(404, "watch not found")
        return {"deleted": watch_id}

    @app.post("/api/alerts/evaluate", dependencies=admin)
    async def evaluate():
        fired = await alerts.evaluate_all()
        return {"alerts": [a.model_dump(mode="json") for a in fired]}

    @app.get("/api/alerts/stream")
    async def stream(request: Request):
        queue = alerts.subscribe()

        async def events():
            try:
                yield "event: hello\ndata: {}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        alert = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"event: alert\ndata: {json.dumps(alert.model_dump(mode='json'), ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
            finally:
                alerts.unsubscribe(queue)

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---- SMS / WhatsApp alerts ---------------------------------------------------------------
    @app.post("/api/subscriptions")
    async def subscribe(req: SubscribeRequest):
        from .agents.llm_planner import Step, run_step

        port = next((p for p in PORTS if p.id == req.harbour_id), None)
        if port is None:
            raise HTTPException(404, f"unknown harbour {req.harbour_id}")
        now_check = await run_step(svc, Step("harbour_safety", port, "today", "now"), svc.clock())
        lat, lon = now_check["lat"], now_check["lon"]
        try:
            sub = await notifier.subscribe(req.phone.replace(" ", ""), req.channel, port, req.language, lat, lon, now_check["level"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OverflowError as exc:
            raise HTTPException(429, str(exc)) from exc
        return sub.public() | {"provider": notifier.sender.name}

    @app.delete("/api/subscriptions/{sub_id}")
    async def unsubscribe(sub_id: str):
        if not notifier.unsubscribe(sub_id):
            raise HTTPException(404, "subscription not found")
        return {"deleted": sub_id}

    @app.get("/api/subscriptions", dependencies=admin)
    async def subscriptions():
        return [s.public() for s in notifier.subs.values()]

    @app.get("/api/outbox", dependencies=admin)
    async def outbox():
        """Messages sent (or, without a provider, that would have been sent) — numbers masked."""
        return {"provider": notifier.sender.name, "messages": [m.model_dump(mode="json") for m in notifier.outbox]}

    @app.post("/api/track")
    async def track(req: TrackRequest):
        status, alert = await alerts.track(req.vessel_id, req.lat, req.lon, req.language)
        return {"geofence": status, "alert": alert.model_dump(mode="json") if alert else None}

    # ---- simulation controls (only meaningful with simulated data) -----------------------
    @app.post("/api/sim/advance", dependencies=admin)
    async def advance(req: AdvanceRequest):
        if svc.mode == "live":
            raise HTTPException(409, "time controls are disabled in live mode")
        svc.clock.advance(req.hours)
        fired = await alerts.evaluate_all()
        return {"clock": svc.clock().isoformat(), "offset_hours": svc.clock.offset.total_seconds() / 3600,
                "alerts": [a.model_dump(mode="json") for a in fired]}

    @app.post("/api/sim/reset", dependencies=admin)
    async def reset():
        svc.clock.reset()
        return {"clock": svc.clock().isoformat(), "offset_hours": 0.0}

    # ---- web UI -------------------------------------------------------------------------------
    if web_dir.exists():
        if (web_dir / "assets").exists():
            app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            candidate = (web_dir / path).resolve()
            if path and candidate.is_file() and web_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(web_dir / "index.html")

    return app


def _default_app() -> FastAPI:
    from dotenv import load_dotenv

    for env_file in (Path(__file__).resolve().parents[1] / ".env", Path(__file__).resolve().parents[2] / ".env"):
        load_dotenv(env_file, override=False)  # backend/.env, then repo-root .env; real env vars win
    logging.basicConfig(level=os.getenv("ORCA_LOG_LEVEL", "INFO"))
    return create_app()


app = _default_app() if os.getenv("ORCA_NO_DEFAULT_APP") != "1" else None
