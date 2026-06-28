"""Fixtures for Rain Radar tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rain_radar.const import (
    CONF_CONTACT,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_PROVIDER,
    CONF_RAIN_RISK_HORIZON_HOURS,
    CONF_RAIN_SOON_WINDOW,
    CONF_RAIN_THRESHOLD,
    CONF_SAMPLE_RADIUS_M,
    DEFAULT_PROVIDER,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations) -> None:
    """Enable custom integrations."""
    return


def load_fixture(filename: str) -> dict[str, Any] | list[Any]:
    """Load fixture JSON."""
    path = Path(__file__).parent / "fixtures" / filename
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def rain_radar_config_entry() -> MockConfigEntry:
    """Return a Rain Radar config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Home",
        data={
            CONF_NAME: "Home",
            CONF_LATITUDE: 59.3293,
            CONF_LONGITUDE: 18.0686,
            CONF_PROVIDER: DEFAULT_PROVIDER,
            CONF_CONTACT: "rain-radar@example.com",
            CONF_RAIN_THRESHOLD: 0.1,
            CONF_RAIN_SOON_WINDOW: 60,
            CONF_SAMPLE_RADIUS_M: 1000,
            CONF_RAIN_RISK_HORIZON_HOURS: 12,
        },
        entry_id="test_entry_id",
        version=1,
    )
