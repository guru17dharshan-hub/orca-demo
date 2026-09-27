"""Harbour board and daily bulletin — for coastal authorities, fisheries departments and disaster managers.

The board runs the same deterministic harbour check as a fisherman's question at every harbour the loaded
data covers, for one time window, and groups them: hold boats (HIGH/SEVERE), go with care (MODERATE),
normal (LOW), cannot confirm (data missing). The reporting agent turns the board into a short bulletin in
any supported language, ready to print or paste into a WhatsApp group; every line comes from templates."""

from __future__ import annotations

import asyncio
from datetime import datetime

from .agents.llm_planner import Step, run_step, window_for
from .geo.ports import PORTS, offshore_point
from .i18n.messages import t, template_language
from .risk.rules import RULESET_VERSION
from .timeutil import IST, ensure_utc

GROUPS = (("hold", ("HIGH", "SEVERE")), ("care", ("MODERATE",)), ("go", ("LOW",)), ("unknown", ("INSUFFICIENT_DATA",)))


async def harbour_board(svc, day: str, part: str, now: datetime) -> dict:
    ports = [p for p in PORTS if svc.replay is None or svc.replay.archive.covers(*offshore_point(p.lat, p.lon))]
    results = await asyncio.gather(*(run_step(svc, Step("harbour_safety", p, day, part), now) for p in ports))
    rows = []
    for p, r in zip(ports, results):
        r.pop("_state", None)
        r.pop("_status", None)
        rows.append(r | {"state": p.state})
    order = {lv: i for i, (_, lvs) in enumerate(GROUPS) for lv in lvs}
    rows.sort(key=lambda r: (order.get(r["level"], 9), r["lat"]))
    start, end = window_for(day, part, now)
    return {
        "day": day, "part": part, "window": {"start": start, "end": end}, "as_of": ensure_utc(now),
        "rules": RULESET_VERSION,
        "counts": {g: sum(1 for r in rows if r["level"] in lvs) for g, lvs in GROUPS},
        "rows": rows,
        "replay": None if svc.replay is None else svc.replay.event.title,
    }


def bulletin(board: dict, language: str) -> dict:
    """The reporting agent: the board as a short bulletin in the reader's language."""
    lang = template_language(language)
    w = board["window"]
    s, e = ensure_utc(w["start"]).astimezone(IST), ensure_utc(w["end"]).astimezone(IST)
    lines = [t("bulletin.title", lang, when=f"{s:%d %b %Y} {s:%H:%M}–{e:%H:%M} IST")]
    for group, levels in GROUPS:
        names = [r["harbour"] for r in board["rows"] if r["level"] in levels]
        if names:
            lines.append(t(f"bulletin.{group}", lang, list=", ".join(names)))
    warned = sorted({f"{x['event']} ({r['harbour']})" for r in board["rows"] for x in r.get("warnings", [])})
    lines.append(t("bulletin.warn", lang, list="; ".join(warned[:8])) if warned else t("bulletin.nowarn", lang))
    as_of = ensure_utc(board["as_of"]).astimezone(IST)
    lines.append(t("bulletin.basis", lang, version=board["rules"], as_of=f"{as_of:%d %b %Y %H:%M}"))
    if board.get("replay"):
        lines.append(t("bulletin.replay", lang, event=board["replay"]))
    lines.append(t("disclaimer", lang))
    return {"language": lang, "lines": lines, "text": "\n".join(lines)}
