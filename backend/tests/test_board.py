import asyncio
import os

os.environ.setdefault("ORCA_NO_DEFAULT_APP", "1")

from fastapi.testclient import TestClient  # noqa: E402

from orca.api import create_app  # noqa: E402
from orca.board import bulletin, harbour_board  # noqa: E402
from orca.llm import NullProvider  # noqa: E402
from orca.services import build_services  # noqa: E402


def _svc():
    return build_services(mode="historical", llm=NullProvider(), event_id="michaung-2023")


def test_board_covers_only_harbours_in_the_loaded_data_and_groups_them():
    svc = _svc()
    b = asyncio.run(harbour_board(svc, "tomorrow", "morning", svc.clock()))
    ids = {r["harbour_id"] for r in b["rows"]}
    assert "chennai" in ids and "goa" not in ids  # Michaung archive is the Bay of Bengal
    assert sum(b["counts"].values()) == len(b["rows"])
    levels = [r["level"] for r in b["rows"]]
    order = {"HIGH": 0, "SEVERE": 0, "MODERATE": 1, "LOW": 2, "INSUFFICIENT_DATA": 3}
    assert [order[x] for x in levels] == sorted(order[x] for x in levels)


def test_bulletin_in_tamil_and_english():
    svc = _svc()
    b = asyncio.run(harbour_board(svc, "tomorrow", "morning", svc.clock()))
    ta = bulletin(b, "ta")
    assert ta["lines"][0].startswith("ORCA கடல் பாதுகாப்பு அறிக்கை")
    en = bulletin(b, "en")
    assert any(line.startswith(("HOLD BOATS", "Go with care", "Normal fishing", "Cannot confirm")) for line in en["lines"])
    assert "HISTORICAL REPLAY" in en["text"]


def test_board_and_bulletin_endpoints(tmp_path):
    with TestClient(create_app(_svc(), alert_interval_s=0, web_dir=tmp_path)) as c:
        assert c.get("/api/board", params={"day": "tomorrow", "part": "morning"}).json()["rows"]
        r = c.get("/api/bulletin", params={"language": "hi"}).json()
        assert r["language"] == "hi" and r["text"].startswith("ORCA समुद्री सुरक्षा बुलेटिन")
        assert c.get("/api/board", params={"day": "yesterday"}).status_code == 422
