import asyncio
from datetime import datetime

import httpx

from orca.adapters.advisories import IMDCapAdapter, parse_cap, parse_rss_links
from orca.adapters.base import PointQuery
from orca.adapters.open_meteo import FORECAST_URL, MARINE_URL, OpenMeteoAdapter
from orca.data_service import DataService
from orca.adapters.advisories import ScenarioAdvisoryAdapter
from orca.models import DataType
from orca.state import MarineState
from orca.timeutil import UTC

from .conftest import FIXTURES, NOW, tomorrow


def _weather_payload(lat, lon, times):
    return {
        "latitude": lat, "longitude": lon, "timezone": "GMT",
        "hourly_units": {"time": "iso8601", "wind_speed_10m": "km/h"},
        "hourly": {
            "time": times,
            "wind_speed_10m": [12.0, 31.5, None],
            "wind_gusts_10m": [18.0, 40.0, 50.0],
            "wind_direction_10m": [250, 255, 260],
            "weather_code": [2, 95, 3],
            "visibility": [24000.0, 2400.0, 999999999.0],  # last value implausible -> suspect
            "precipitation": [0.0, 4.2, 0.0],
            "cape": [500.0, 2000.0, 400.0],
        },
    }


def _marine_payload(lat, lon, times):
    return {
        "latitude": lat, "longitude": lon,
        "hourly": {
            "time": times,
            "wave_height": [0.9, 1.6, 2.7],
            "wave_period": [6.0, 7.0, 8.0],
            "swell_wave_height": [0.5, 0.6, 0.8],
            "sea_surface_temperature": [28.1, 28.0, 27.9],
            "ocean_current_velocity": [1.2, 1.5, 1.4],
            "sea_level_height_msl": [0.4, 0.1, -0.3],
        },
    }


def _mock_client(multi=False):
    times = ["2026-09-25T00:00", "2026-09-25T01:00", "2026-09-25T02:00"]

    def handler(request: httpx.Request) -> httpx.Response:
        lats = [float(v) for v in request.url.params["latitude"].split(",")]
        lons = [float(v) for v in request.url.params["longitude"].split(",")]
        assert request.url.params["timezone"] == "GMT"
        maker = _weather_payload if str(request.url).startswith(FORECAST_URL) else _marine_payload
        if str(request.url).startswith(FORECAST_URL):
            assert request.url.params["cell_selection"] == "sea"
        else:
            assert str(request.url).startswith(MARINE_URL)
        items = [maker(la, lo, times) for la, lo in zip(lats, lons)]
        return httpx.Response(200, json=items if len(items) > 1 else items[0])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_open_meteo_normalizes_to_canonical_forecast_observations():
    adapter = OpenMeteoAdapter(client=_mock_client())
    start = datetime(2026, 9, 25, 0, tzinfo=UTC)
    obs = asyncio.run(adapter.observe(PointQuery(15.2, 72.8, start, datetime(2026, 9, 25, 2, tzinfo=UTC))))
    state = MarineState(15.2, 72.8, obs)
    t1 = datetime(2026, 9, 25, 1, tzinfo=UTC)
    assert state.value(t1, "wind_speed") == 31.5
    assert state.value(t1, "wave_height") == 1.6
    assert state.value(t1, "current_speed") == 1.5
    assert all(o.data_type == DataType.FORECAST for o in obs)
    t2 = datetime(2026, 9, 25, 2, tzinfo=UTC)
    vis = state.values_at(t2)["visibility"]
    assert vis.value is None and vis.quality_flag == "suspect"
    wind_missing = state.values_at(t2)["wind_speed"]
    assert wind_missing.value is None and wind_missing.quality_flag == "missing"
    assert adapter.health().status == "ok"


def test_open_meteo_multi_location_batch():
    adapter = OpenMeteoAdapter(client=_mock_client())
    pts = [(15.0, 72.0), (15.5, 72.5), (16.0, 73.0)]
    out = asyncio.run(
        adapter.observe_many(pts, datetime(2026, 9, 25, 0, tzinfo=UTC), datetime(2026, 9, 25, 2, tzinfo=UTC))
    )
    assert set(out) == set(pts)
    assert all(any(o.variable == "wave_height" for o in v) for v in out.values())


def test_open_meteo_failure_marks_health_unavailable():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503, text="down")))
    adapter = OpenMeteoAdapter(client=client)
    try:
        asyncio.run(adapter.observe(PointQuery(15.2, 72.8, NOW, NOW)))
    except Exception as exc:
        assert "503" in str(exc)
    assert adapter.health().status == "unavailable"


def test_parse_real_imd_cap_alert():
    xml = (FIXTURES / "imd_cap_2026-09-23.xml").read_text(encoding="utf-8")
    adv = parse_cap(xml, "https://example/cap.xml", NOW)
    assert adv is not None
    assert adv.data_type == DataType.OFFICIAL_ADVISORY
    assert adv.severity == "Severe"
    assert "Odisha" in adv.area_desc
    assert adv.polygons and len(adv.polygons[0]) > 10
    assert adv.onset < adv.expires
    # Visakhapatnam (17.7N 83.2E) lies inside the Coastal-AP/Odisha polygon; Goa does not.
    state_in = MarineState(17.7, 83.2, [], [adv])
    state_out = MarineState(15.4, 73.8, [], [adv])
    t = datetime(2026, 9, 23, 12, tzinfo=UTC)
    assert state_in.advisories_at(t) == [adv]
    assert state_out.advisories_at(t) == []
    assert state_in.advisories_at(datetime(2026, 9, 25, 12, tzinfo=UTC)) == []  # expired


def test_parse_rss_links():
    links = parse_rss_links((FIXTURES / "imd_rss_2026-09-23.xml").read_text(encoding="utf-8"))
    assert len(links) == 3 and all(link.endswith(".xml") for link in links)


def test_imd_adapter_end_to_end_with_mock_transport():
    rss = (FIXTURES / "imd_rss_2026-09-23.xml").read_text(encoding="utf-8")
    cap = (FIXTURES / "imd_cap_2026-09-23.xml").read_text(encoding="utf-8")

    def handler(request):
        return httpx.Response(200, text=rss if request.url.path.endswith("rss.xml") else cap)

    adapter = IMDCapAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    active = asyncio.run(adapter.fetch_advisories(datetime(2026, 9, 23, 12, tzinfo=UTC)))
    assert active and active[0].source.startswith("India Meteorological Department")
    assert adapter.health().status == "ok"


def test_auto_mode_falls_back_to_labelled_replay(replay):
    failing = OpenMeteoAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    svc = DataService(
        mode="auto",
        live_marine=failing,
        replay_marine=replay,
        live_advisories=[],
        replay_advisories=[ScenarioAdvisoryAdapter(replay.scenario)],
        clock=lambda: NOW,
    )
    state, status = asyncio.run(svc.marine_state(15.2, 72.8, tomorrow(6), tomorrow(9)))
    assert status.marine_source == "replay" and "403" in status.fallback_reason
    assert state.is_simulated()


def test_live_mode_never_fabricates(replay):
    failing = OpenMeteoAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403))))
    svc = DataService("live", failing, replay, [], [], clock=lambda: NOW)
    state, status = asyncio.run(svc.marine_state(15.2, 72.8, tomorrow(6), tomorrow(9)))
    assert status.marine_source == "none"
    assert state.observations == []


def test_auto_mode_circuit_breaker_skips_dead_live_source(replay):
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(403)

    failing = OpenMeteoAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    svc = DataService("auto", failing, replay, [], [], clock=lambda: NOW)
    asyncio.run(svc.marine_state(15.2, 72.8, tomorrow(6), tomorrow(9)))
    first_calls = len(calls)
    _, status = asyncio.run(svc.marine_state(15.2, 72.8, tomorrow(6), tomorrow(9)))
    assert first_calls > 0 and len(calls) == first_calls  # second request did not touch the live API
    assert status.marine_source == "replay" and "retrying live shortly" in status.fallback_reason
