"""Regression tests for DMI accumulated precipitation forecast intervals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from custom_components.rain_radar.const import DMI_FORECAST_PARAMETERS
from custom_components.rain_radar.providers.dmi import (
    DmiProvider,
    _current_precipitation,
    _parse_samples,
)
from custom_components.rain_radar.providers.models import (
    CacheMetadata,
    Location,
    RainRadarOptions,
)

_START = datetime(2026, 9, 4, 0, tzinfo=UTC)
_LOCATION = Location(latitude=55.72, longitude=12.56)
_OPTIONS = RainRadarOptions(
    contact="",
    forecast_provider="dmi",
    radar_area="denmark",
    rain_threshold=0.1,
    rain_soon_window_minutes=60,
    sample_radius_m=1000,
    rain_risk_horizon_hours=2,
)


def _payload(points: list[tuple[float, float | None]]) -> dict[str, Any]:
    """Create cumulative millimetres at known elapsed-hour boundaries."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "step": (_START + timedelta(hours=hour)).isoformat(),
                    "total-precipitation": total,
                    "precipitation-type": 1,
                },
            }
            for hour, total in points
        ],
    }


class _Client:
    """Return a fixed payload without external requests."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def async_get_json(self, *args, **kwargs):
        return self.payload, CacheMetadata(fetched_at=_START)


def _provider(payload: dict[str, Any], hour: float) -> DmiProvider:
    return DmiProvider(_Client(payload), now_fn=lambda: _START + timedelta(hours=hour))


def test_request_uses_unambiguous_cumulative_precipitation() -> None:
    """Do not request the rain-rate field with contradictory accumulation metadata."""
    assert DMI_FORECAST_PARAMETERS == "total-precipitation,precipitation-type"


def test_normalized_rates_use_actual_intervals_and_start_times() -> None:
    """Divide cumulative millimetres by each real interval's duration in hours."""
    samples = _parse_samples(_payload([(0, 1.0), (0.5, 1.3), (2.5, 1.5)]))

    assert len(samples) == 2
    assert samples[0].time == _START
    assert samples[0].end_time == _START + timedelta(minutes=30)
    assert samples[0].precipitation_rate == pytest.approx(0.6)
    assert samples[0].precipitation_amount == pytest.approx(0.3)
    assert samples[1].time == _START + timedelta(minutes=30)
    assert samples[1].end_time == _START + timedelta(hours=2, minutes=30)
    assert samples[1].precipitation_rate == pytest.approx(0.1)
    assert samples[1].precipitation_amount == pytest.approx(0.2)


async def test_accumulation_plateau_cannot_create_extreme_current_or_false_risk() -> (
    None
):
    """A plateau like the reported live series means dry, not thousands of mm/h."""
    totals = [0, 0.67, 0.92, 0.98, 1.3, 2.54, 2.541, 2.541, 2.541, 2.541]
    payload = _payload(list(enumerate(totals)))
    for feature in payload["features"]:
        feature["properties"]["rain-precipitation-rate"] = 2.5292969
    provider = _provider(payload, 6.5)

    forecast = await provider.async_get_precipitation_forecast(_LOCATION, _OPTIONS)
    risk = await provider.async_get_rain_risk(_LOCATION, _OPTIONS)

    assert forecast.current_precipitation == 0
    assert forecast.rain_now is False
    assert forecast.rain_soon is False
    assert forecast.rain_arrival_minutes is None
    assert risk.max_probability == 0
    assert all(hour.probability == 0 for hour in risk.hourly)


async def test_current_and_risk_include_the_interval_containing_now() -> None:
    """A 00:00-01:00 amount is current at 00:30, not a future arrival at 01:00."""
    provider = _provider(_payload([(0, 1), (1, 1.4), (2, 1.4), (3, 1.4)]), 0.5)

    forecast = await provider.async_get_precipitation_forecast(_LOCATION, _OPTIONS)
    risk = await provider.async_get_rain_risk(_LOCATION, _OPTIONS)

    assert forecast.current_precipitation == pytest.approx(0.4)
    assert forecast.rain_now is True
    assert forecast.rain_arrival_minutes == 0
    assert risk.max_probability == 100
    assert risk.hourly[0].time == _START
    assert risk.hourly[0].symbol_code == "rain"


async def test_rain_arrival_uses_interval_start_not_accumulation_end() -> None:
    """Rain in the 01:00-02:00 interval begins in 30 minutes at 00:30."""
    provider = _provider(_payload([(0, 0), (1, 0), (2, 0.4), (3, 0.4)]), 0.5)

    forecast = await provider.async_get_precipitation_forecast(_LOCATION, _OPTIONS)

    assert forecast.current_precipitation == 0
    assert forecast.rain_arrival_minutes == 30
    assert forecast.rain_soon is True


@pytest.mark.parametrize("hour", [-0.1, 2, 2.5])
def test_current_does_not_extrapolate_before_or_after_forecast_intervals(hour) -> None:
    """Future and exhausted intervals cannot stand in for current conditions."""
    samples = _parse_samples(_payload([(0, 0), (1, 0.4), (2, 0.4)]))

    assert _current_precipitation(samples, _START + timedelta(hours=hour)) is None


def test_current_switches_at_interval_boundary() -> None:
    """The endpoint belongs to the next interval, not the preceding rainy hour."""
    samples = _parse_samples(_payload([(0, 0), (1, 0.4), (2, 0.4)]))

    assert _current_precipitation(samples, _START + timedelta(hours=1)) == 0


@pytest.mark.parametrize("total", [0.0, 4.0])
def test_single_cumulative_point_is_not_an_intensity(total) -> None:
    """Neither zero nor a positive baseline establishes a forecast interval."""
    assert _parse_samples(_payload([(0, total)])) == []


@pytest.mark.parametrize(
    ("points", "unknown_hours"),
    [
        ([(0, 0), (1, None), (2, 0.4), (3, 0.6)], [0.5, 1.5]),
        ([(0, 1), (1, 0.4), (2, 0.6)], [0.5]),
        ([(0, 0), (1, 0.2), (1, 0.4), (2, 0.6), (3, 0.8)], [0.5, 1.5]),
    ],
)
def test_invalid_boundaries_leave_unknown_intervals(points, unknown_hours) -> None:
    """Do not bridge null, decreasing, or ambiguous duplicate accumulation values."""
    samples = _parse_samples(_payload(points))

    assert samples
    for hour in unknown_hours:
        assert _current_precipitation(samples, _START + timedelta(hours=hour)) is None
    assert all(sample.end_time > sample.time for sample in samples)
    assert all(sample.precipitation_rate >= 0 for sample in samples)


async def test_dry_future_after_gap_does_not_prove_dry_soon_window() -> None:
    """A dry interval after missing data must not turn the missing period dry."""
    provider = _provider(_payload([(0, 0), (1, None), (2, 0.4), (3, 0.4)]), 0.5)

    forecast = await provider.async_get_precipitation_forecast(_LOCATION, _OPTIONS)

    assert forecast.current_precipitation is None
    assert forecast.rain_soon is None


async def test_known_dry_intervals_do_not_prove_dry_risk_across_a_gap() -> None:
    """Unknown parts of the risk horizon cannot be summarized as zero risk."""
    provider = _provider(_payload([(0, 0), (1, 0), (2, None), (3, 0), (4, 0)]), 0.5)

    risk = await provider.async_get_rain_risk(_LOCATION, _OPTIONS)

    assert risk.hourly
    assert all(hour.probability == 0 for hour in risk.hourly)
    assert risk.max_probability is None


def test_derived_nonfinite_rate_is_not_a_valid_interval() -> None:
    """Finite source values must not overflow into an infinite entity state."""
    assert _parse_samples(_payload([(0, 0), (1 / 3600, 1e308)])) == []


async def test_risk_compares_intensity_not_multi_hour_amount() -> None:
    """A small multi-hour accumulation below the mm/h threshold is not rainy."""
    provider = _provider(_payload([(0, 0), (3, 0.15)]), 0.5)

    risk = await provider.async_get_rain_risk(_LOCATION, _OPTIONS)

    assert risk.max_probability == 0
    assert risk.hourly[0].precipitation_amount == pytest.approx(0.15)


async def test_risk_includes_partial_interval_at_end_of_horizon() -> None:
    """Current partial hour must not consume the slot for the final partial hour."""
    provider = _provider(_payload([(0, 0), (1, 0), (2, 0), (3, 0.4)]), 0.5)

    risk = await provider.async_get_rain_risk(_LOCATION, _OPTIONS)

    assert risk.max_probability == 100
    assert len(risk.hourly) == 3


async def test_normalized_metadata_preserves_actual_model_intervals() -> None:
    """Expose real three-hour intervals without implying hourly precision."""
    provider = _provider(_payload([(0, 0), (3, 0.15)]), 0.5)
    forecast = await provider.async_get_precipitation_forecast(_LOCATION, _OPTIONS)
    risk = await provider.async_get_rain_risk(_LOCATION, _OPTIONS)

    assert forecast.data_kind == "model"
    assert forecast.resolution_minutes == 180
    assert forecast.window_complete is True
    assert forecast.observation_time is None
    assert forecast.samples[0].interval_start == _START
    assert forecast.samples[0].interval_end == _START + timedelta(hours=3)
    assert risk.data_kind == "model"
    assert risk.resolution_minutes == 180
    assert risk.window_complete is True
    assert risk.hourly[0].interval_start == _START
    assert risk.hourly[0].interval_end == _START + timedelta(hours=3)


async def test_normalized_metadata_exposes_missing_dmi_window() -> None:
    """Unknown DMI gaps remain visible in the shared quality metadata."""
    provider = _provider(_payload([(0, 0), (1, None), (2, 0.4), (3, 0.4)]), 0.5)
    forecast = await provider.async_get_precipitation_forecast(_LOCATION, _OPTIONS)
    risk = await provider.async_get_rain_risk(_LOCATION, _OPTIONS)

    assert forecast.window_complete is False
    assert forecast.reason == "incomplete_window"
    assert risk.window_complete is False
    assert risk.reason == "incomplete_window"
