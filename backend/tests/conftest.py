import os

os.environ.setdefault("ORCA_MOSDAC_SEARCH", "0")  # no live ISRO portal calls in unit tests

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from orca.adapters.replay import ReplayAdapter
from orca.scenario import Scenario
from orca.timeutil import IST, UTC, ist_midnight

FIXTURES = Path(__file__).parent / "fixtures"

# 24 Sep 2026 12:30 IST — "today"; the scenario's day 1 ("tomorrow") starts 25 Sep 00:00 IST.
NOW = datetime(2026, 9, 24, 7, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def scenario() -> Scenario:
    return Scenario(ist_midnight(NOW, 1))


@pytest.fixture
def replay(scenario) -> ReplayAdapter:
    return ReplayAdapter(scenario)


def ist(day: int, hour: int) -> datetime:
    """Helper: a September-2026 IST wall-clock time as UTC."""
    return datetime(2026, 9, day, hour, tzinfo=IST).astimezone(UTC)


def tomorrow(hour: int) -> datetime:
    return ist(25, hour)


def hours(n: int) -> timedelta:
    return timedelta(hours=n)
