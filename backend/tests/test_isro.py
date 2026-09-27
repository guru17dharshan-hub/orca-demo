import io
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from orca.historical.archive import EventArchive
from orca.historical.events import EVENTS
from orca.timeutil import UTC

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "historical"))
import fetch_mosdac  # noqa: E402

MICHAUNG = EVENTS["michaung-2023"]


def test_points_outside_an_archive_get_no_data():
    arc = EventArchive(EVENTS["tauktae-2021"])  # Arabian Sea archive: 62–80°E
    t = datetime(2021, 5, 15, 3, tzinfo=UTC)
    assert arc.wave.sample("htsgw", 15.4, 73.6, t, EVENTS["tauktae-2021"].default_as_of, sea=True) is not None  # Goa
    assert arc.wave.sample("htsgw", 17.7, 83.4, t, EVENTS["tauktae-2021"].default_as_of, sea=True) is None  # Visakhapatnam
    assert not arc.covers(17.7, 83.4) and arc.covers(15.4, 73.6)


def _l3b_file(tmp_path: Path) -> bytes:
    """A small file laid out as the INSAT-3D format document describes (scaled int16 lat/lon, SST, flags)."""
    h5py = pytest.importorskip("h5py")
    lat = np.linspace(14.0, 12.0, 21)[:, None].repeat(21, 1)
    lon = np.linspace(80.0, 82.0, 21)[None, :].repeat(21, 0)
    sst_k = 273.15 + 28.0 + 0.1 * (lon - 80.0)
    sst_k[0, 0] = -999.0  # fill
    flags = np.full(lat.shape, 3, np.uint8)
    flags[5, 5] = 1  # cloud-masked pixel must be dropped
    sst_k[5, 5] = 273.15 + 5.0
    buf = io.BytesIO()
    with h5py.File(buf, "w") as h5:
        for name, arr in (("Latitude", lat), ("Longitude", lon)):
            d = h5.create_dataset(name, data=np.round(arr / 0.01).astype(np.int16)[None])
            d.attrs["scale_factor"] = np.float32(0.01)
            d.attrs["add_offset"] = np.float32(0.0)
            d.attrs["_FillValue"] = np.int16(-999)
        d = h5.create_dataset("SST", data=sst_k.astype(np.float32)[None])
        d.attrs["_FillValue"] = np.float32(-999.0)
        d.attrs["units"] = "K"
        h5.create_dataset("SST_QFLAGS", data=flags[None])
    return buf.getvalue()


def test_read_and_regrid_insat_l3b_sst(tmp_path):
    lat, lon, sst = fetch_mosdac.read_l3b_sst(_l3b_file(tmp_path))
    assert np.nanmin(sst) > 27.9 and np.nanmax(sst) < 28.3  # Kelvin -> °C; the cloudy 5 °C pixel is gone
    assert np.isnan(sst[0, 0]) and np.isnan(sst[5, 5])
    lats, lons, grid = fetch_mosdac.regrid(lat, lon, sst, (12.0, 14.0, 80.0, 82.0), step=0.5)
    assert grid.shape == (4, 4) and np.nanmean(grid) == pytest.approx(28.1, abs=0.05)


def test_granule_day_and_version_choice():
    entries = [{"identifier": "3DIMG_17MAY2021_0000_L3B_SST_DLY_V01R00.h5", "id": "1"},
               {"identifier": "3DIMG_17MAY2021_0000_L3B_SST_DLY_V02R00.h5", "id": "2"}]
    picked = fetch_mosdac.pick_daily(entries)
    assert list(picked) == [date(2021, 5, 17)] and picked[date(2021, 5, 17)]["id"] == "2"


def _write_isro(root: Path, days: list[date]) -> None:
    folder = root / MICHAUNG.id
    folder.mkdir(parents=True)
    lats, lons = np.arange(12.025, 14.0, 0.05), np.arange(80.025, 82.0, 0.05)
    sst = np.full((len(days), len(lats), len(lons)), 2850, np.int16)  # 28.50 °C
    sst[-1] = 2700
    np.savez_compressed(folder / "isro_sst.npz", lat=lats, lon=lons, sst=sst,
                        days=np.array([d.isoformat() for d in days]), files=np.array([f"f{d:%d}.h5" for d in days]))


def test_isro_sst_respects_release_time(tmp_path):
    _write_isro(tmp_path, [date(2023, 12, 1), date(2023, 12, 2)])
    arc = EventArchive(MICHAUNG, tmp_path)
    assert arc.isro_sst_at(13.1, 80.5, datetime(2023, 12, 2, 6, 0, tzinfo=UTC)) is None  # 1 Dec's product out at 06:15
    v, day, f = arc.isro_sst_at(13.1, 80.5, datetime(2023, 12, 2, 7, 0, tzinfo=UTC))
    assert (v, day, f) == (28.5, date(2023, 12, 1), "f01.h5")
    assert arc.isro_sst_at(13.1, 80.5, datetime(2023, 12, 4, tzinfo=UTC))[0] == 27.0
    assert arc.isro_sst_at(18.0, 85.0, datetime(2023, 12, 4, tzinfo=UTC)) is None  # outside the file


def test_tide_at_nearest_harbour(tmp_path):
    folder = tmp_path / MICHAUNG.id
    folder.mkdir(parents=True)
    t0 = datetime(2023, 12, 2, tzinfo=UTC)
    np.savez_compressed(folder / "tide.npz", names=np.array(["Chennai"]), lat=np.array([13.1]), lon=np.array([80.35]),
                        times=np.array([(t0 + timedelta(hours=h)).isoformat() for h in range(3)]),
                        level_mm=np.array([[100], [300], [-32768]], np.int16))
    arc = EventArchive(MICHAUNG, tmp_path)
    level, name = arc.tide_at(13.15, 80.4, t0 + timedelta(minutes=30))
    assert name == "Chennai" and level == pytest.approx(0.2)
    assert arc.tide_at(13.15, 80.4, t0 + timedelta(hours=1, minutes=30)) is None  # missing value
    assert arc.tide_at(15.4, 73.7, t0) is None  # Goa: no harbour within 60 km in this file


# --- data discovery agent ------------------------------------------------------------------
class _FakeMosdac:
    enabled = True

    def __init__(self):
        self.calls = []

    async def granules(self, dataset, day, lat, lon):
        self.calls.append(dataset)
        return [{"identifier": f"{dataset}_granule.h5", "id": "1"}] if dataset.startswith("3R") else []


def _historical(event_id="tauktae-2021"):
    from orca.llm import NullProvider
    from orca.services import build_services

    return build_services(mode="historical", llm=NullProvider(), event_id=event_id)


def test_discovery_flags_a_place_outside_the_replay():
    import asyncio

    from orca.catalog import discover

    svc = _historical()
    fake = _FakeMosdac()
    t = svc.clock()
    goa = asyncio.run(discover(svc, 15.35, 73.6, t, t + timedelta(hours=6), fake))
    assert not goa.gaps and {s.id for s in goa.covering()} >= {"gfs", "imd_cap", "tide"}
    mosdac = next(s for s in goa.sources if s.id == "mosdac")
    assert mosdac.status == "found_on_mosdac" and "2 INSAT SST granules" in mosdac.detail
    vizag = asyncio.run(discover(svc, 17.6, 83.5, t, t + timedelta(hours=6), fake))
    assert vizag.gaps == ["outside_replay"] and next(s for s in vizag.sources if s.id == "gfs").status == "outside"


def test_answer_says_when_the_replay_does_not_cover_the_place():
    import asyncio

    from orca.agents.orchestrator import ChatRequest, Orchestrator

    svc = _historical()
    r = asyncio.run(Orchestrator(svc).handle(ChatRequest(message="Is it safe tomorrow morning at 17.6, 83.5?")))
    assert r.cards["discovery"]["gaps"] == ["outside_replay"]
    assert r.answer.startswith("The loaded data (")
    assert r.cards["safety"]["risk_level"] != "LOW"  # never LOW without wave data
    steps = [s["id"] for s in r.trace.plan]
    assert steps.index("catalog") < steps.index("data")


def test_goa_conditions_include_tide_in_the_replay():
    import asyncio

    from orca.agents.orchestrator import ChatRequest, Orchestrator

    svc = _historical()
    r = asyncio.run(Orchestrator(svc).handle(ChatRequest(message="What are the tide and sea conditions near Goa?")))
    assert r.cards["conditions"]["tides"], "tide extremes expected from the harbour tide archive"
