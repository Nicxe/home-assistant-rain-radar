"""Regression coverage for unknown values and forecast validity windows."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.rain_radar.api import RainRadarApiError
from custom_components.rain_radar.providers.met_no import MetNoProvider
from custom_components.rain_radar.providers.models import CoverageStatus, Location
from custom_components.rain_radar.providers.smhi import SmhiProvider

from .test_met_no_provider import FakeClient as MetClient
from .test_met_no_provider import _options
from .test_smhi_provider import FakeClient as SmhiClient

NOW = datetime(2026, 9, 6, 8, 30, tzinfo=UTC)
LOCATION = Location(59.3, 18.0)


def _met(rates, *, offset=0, produced=NOW):
    return {
        "properties": {
            "meta": {"updated_at": produced.isoformat()},
            "timeseries": [
                {
                    "time": (NOW + timedelta(minutes=index * 5 + offset)).isoformat(),
                    "data": {"instant": {"details": {"precipitation_rate": rate}}},
                }
                for index, rate in enumerate(rates)
            ],
        }
    }


def _smhi(rates, *, offset=0, produced=NOW):
    return {
        "createdTime": produced.isoformat(),
        "timeSeries": [
            {
                "time": (NOW + timedelta(minutes=30 + index * 60 + offset)).isoformat(),
                "intervalParametersStartTime": (
                    NOW + timedelta(minutes=-30 + index * 60 + offset)
                ).isoformat(),
                "data": {"precipitation_amount_mean": rate},
            }
            for index, rate in enumerate(rates)
        ],
    }


def _provider(kind, rates, **kwargs):
    if kind == "met":
        return MetNoProvider(MetClient({"met_no_nowcast": _met(rates, **kwargs)}))
    return SmhiProvider(SmhiClient(_smhi(rates, **kwargs)))


@pytest.mark.parametrize("kind", ["met", "smhi"])
async def test_missing_values_are_unknown_and_recover(kind, freezer):
    freezer.move_to(NOW)
    provider = _provider(kind, [None] * 14)
    forecast = await provider.async_get_precipitation_forecast(LOCATION, _options())
    assert forecast.current_precipitation is None
    assert forecast.rain_now is None
    assert forecast.rain_soon is None
    assert forecast.window_complete is False
    recovered = await _provider(kind, [0.0] * 14).async_get_precipitation_forecast(
        LOCATION, _options()
    )
    assert recovered.rain_now is False
    assert recovered.rain_soon is False
    assert recovered.window_complete is True


@pytest.mark.parametrize("kind", ["met", "smhi"])
@pytest.mark.parametrize("rates", [[0.0, None] + [0.0] * 12, [0.0]])
async def test_incomplete_dry_window_never_means_no_rain(kind, rates, freezer):
    freezer.move_to(NOW)
    forecast = await _provider(kind, rates).async_get_precipitation_forecast(
        LOCATION, _options()
    )
    assert forecast.rain_now is False
    assert forecast.rain_soon is None


@pytest.mark.parametrize("kind", ["met", "smhi"])
async def test_missing_timestamp_creates_unknown_gap(kind, freezer):
    freezer.move_to(NOW)
    payload = _met([0.0] * 14) if kind == "met" else _smhi([0.0] * 3)
    series = (
        payload["properties"]["timeseries"] if kind == "met" else payload["timeSeries"]
    )
    del series[1]
    provider = (
        MetNoProvider(MetClient({"met_no_nowcast": payload}))
        if kind == "met"
        else SmhiProvider(SmhiClient(payload))
    )
    forecast = await provider.async_get_precipitation_forecast(LOCATION, _options())
    assert forecast.rain_soon is None


@pytest.mark.parametrize("kind", ["met", "smhi"])
@pytest.mark.parametrize(
    ("offset", "produced"), [(-1440, NOW), (0, NOW - timedelta(days=1))]
)
async def test_weather_age_is_independent_of_http_cache(
    kind, offset, produced, freezer
):
    freezer.move_to(NOW)
    forecast = await _provider(
        kind, [1.2] * 14, offset=offset, produced=produced
    ).async_get_precipitation_forecast(LOCATION, _options())
    assert forecast.current_precipitation is None
    assert forecast.rain_now is None
    assert forecast.rain_soon is None
    assert forecast.is_stale is True


async def test_met_risk_includes_both_partial_hours(freezer):
    freezer.move_to(NOW)
    payload = {
        "properties": {
            "meta": {"updated_at": NOW.isoformat()},
            "timeseries": [
                {
                    "time": (NOW + timedelta(minutes=offset)).isoformat(),
                    "data": {
                        "next_1_hours": {
                            "details": {"probability_of_precipitation": probability}
                        }
                    },
                }
                for offset, probability in [(-30, 100), (30, 0)]
            ],
        }
    }
    risk = await MetNoProvider(
        MetClient({"met_no_locationforecast": payload})
    ).async_get_rain_risk(LOCATION, replace(_options(), rain_risk_horizon_hours=1))
    assert risk.max_probability == 100
    assert len(risk.hourly) == 2
    assert risk.hourly[0].interval_start == NOW - timedelta(minutes=30)
    assert risk.hourly[0].interval_end == NOW + timedelta(minutes=30)
    assert risk.window_complete is True


@pytest.mark.parametrize("kind", ["met", "smhi"])
async def test_missing_probability_is_not_zero(kind, freezer):
    freezer.move_to(NOW)
    payload = _met([0.0] * 14) if kind == "met" else _smhi([0.0] * 3)
    provider = (
        MetNoProvider(MetClient({"met_no_locationforecast": payload}))
        if kind == "met"
        else SmhiProvider(SmhiClient(payload))
    )
    risk = await provider.async_get_rain_risk(LOCATION, _options())
    assert risk.max_probability is None
    assert all(hour.probability is None for hour in risk.hourly)


@pytest.mark.parametrize(
    "message",
    [
        "Provider returned HTTP 404: endpoint not found",
        "Provider returned HTTP 400: invalid parameter",
    ],
)
async def test_smhi_generic_http_error_is_not_geographic(message):
    provider = SmhiProvider(SmhiClient(None, error=RainRadarApiError(message)))
    with pytest.raises(RainRadarApiError):
        await provider.async_get_precipitation_forecast(LOCATION, _options())
    assert provider.coverage_status != CoverageStatus.OUTSIDE_COVERAGE


async def test_smhi_concurrent_failure_is_shared_then_recovers(freezer):
    freezer.move_to(NOW)
    client = SmhiClient(None, error=RainRadarApiError("Provider returned HTTP 503"))
    provider = SmhiProvider(client)
    results = await asyncio.gather(
        provider.async_get_precipitation_forecast(LOCATION, _options()),
        provider.async_get_rain_risk(LOCATION, _options()),
        return_exceptions=True,
    )
    assert all(isinstance(result, RainRadarApiError) for result in results)
    assert len(client.calls) == 1
    client.error = None
    client.payload = _smhi([0.0] * 3)
    freezer.tick(timedelta(minutes=10))
    client.payload = _smhi([0.0] * 3, offset=10)
    forecast = await provider.async_get_precipitation_forecast(LOCATION, _options())
    assert forecast.rain_soon is False
    assert len(client.calls) == 2


@pytest.mark.parametrize("kind", ["met", "smhi"])
@pytest.mark.parametrize("probability", [0, None])
async def test_partial_risk_does_not_claim_zero(kind, probability, freezer):
    freezer.move_to(NOW)
    if kind == "met":
        payload = {
            "properties": {
                "meta": {"updated_at": NOW.isoformat()},
                "timeseries": [
                    {
                        "time": (NOW - timedelta(minutes=30)).isoformat(),
                        "data": {
                            "next_1_hours": {
                                "details": {"probability_of_precipitation": probability}
                            }
                        },
                    }
                ],
            }
        }
        provider = MetNoProvider(MetClient({"met_no_locationforecast": payload}))
    else:
        payload = _smhi([0.0])
        payload["timeSeries"][0]["data"]["probability_of_precipitation"] = probability
        provider = SmhiProvider(SmhiClient(payload))
    risk = await provider.async_get_rain_risk(LOCATION, _options())
    assert risk.max_probability is None
    assert risk.window_complete is False


async def test_smhi_old_model_reference_cannot_be_hidden_by_fresh_creation(freezer):
    freezer.move_to(NOW)
    payload = _smhi([1.2] * 3)
    payload["referenceTime"] = (NOW - timedelta(days=1)).isoformat()
    provider = SmhiProvider(SmhiClient(payload))
    forecast = await provider.async_get_precipitation_forecast(LOCATION, _options())
    risk = await provider.async_get_rain_risk(LOCATION, _options())
    assert forecast.is_stale is True
    assert forecast.current_precipitation is None
    assert risk.is_stale is True


@pytest.mark.parametrize("kind", ["met", "smhi"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
async def test_invalid_rates_are_unknown(kind, value, freezer):
    freezer.move_to(NOW)
    forecast = await _provider(kind, [value] * 14).async_get_precipitation_forecast(
        LOCATION, _options()
    )
    assert forecast.current_precipitation is None
    assert forecast.rain_now is None
    assert forecast.rain_soon is None


@pytest.mark.parametrize("kind", ["met", "smhi"])
async def test_risk_marks_expired_intervals_stale_even_with_fresh_metadata(
    kind, freezer
):
    freezer.move_to(NOW)
    if kind == "met":
        payload = {
            "properties": {
                "meta": {"updated_at": NOW.isoformat()},
                "timeseries": [
                    {
                        "time": (NOW - timedelta(days=1)).isoformat(),
                        "data": {
                            "next_1_hours": {
                                "details": {"probability_of_precipitation": 100}
                            }
                        },
                    }
                ],
            }
        }
        provider = MetNoProvider(MetClient({"met_no_locationforecast": payload}))
    else:
        payload = _smhi([1.0], offset=-1440)
        payload["timeSeries"][0]["data"]["probability_of_precipitation"] = 100
        provider = SmhiProvider(SmhiClient(payload))
    risk = await provider.async_get_rain_risk(LOCATION, _options())
    assert risk.max_probability is None
    assert risk.is_stale is True
    assert risk.window_complete is False


@pytest.mark.parametrize("kind", ["met", "smhi"])
async def test_zero_risk_requires_full_overlapping_window(kind, freezer):
    freezer.move_to(NOW)
    if kind == "met":
        payload = {
            "properties": {
                "meta": {"updated_at": NOW.isoformat()},
                "timeseries": [
                    {
                        "time": (NOW + timedelta(minutes=offset)).isoformat(),
                        "data": {
                            "next_1_hours": {
                                "details": {"probability_of_precipitation": 0}
                            }
                        },
                    }
                    for offset in [-30, 30]
                ],
            }
        }
        provider = MetNoProvider(MetClient({"met_no_locationforecast": payload}))
    else:
        payload = _smhi([0.0, 0.0])
        for sample in payload["timeSeries"]:
            sample["data"]["probability_of_precipitation"] = 0
        provider = SmhiProvider(SmhiClient(payload))
    risk = await provider.async_get_rain_risk(
        LOCATION, replace(_options(), rain_risk_horizon_hours=1)
    )
    assert risk.max_probability == 0
    assert risk.window_complete is True
    assert len(risk.hourly) == 2


@pytest.mark.parametrize("missing", [9999, "9999", 9999.0])
async def test_smhi_documented_missing_sentinel_is_unknown(missing, freezer):
    """SNOW1gv1 missingValue=9999 cannot become rain, 100 percent, or a symbol."""
    freezer.move_to(NOW)
    payload = _smhi([missing] * 3)
    for sample in payload["timeSeries"]:
        sample["data"]["probability_of_precipitation"] = missing
        sample["data"]["symbol_code"] = missing
    provider = SmhiProvider(SmhiClient(payload))
    forecast = await provider.async_get_precipitation_forecast(LOCATION, _options())
    risk = await provider.async_get_rain_risk(LOCATION, _options())
    assert forecast.current_precipitation is None
    assert forecast.rain_now is None
    assert forecast.rain_soon is None
    assert forecast.window_complete is False
    assert risk.max_probability is None
    assert all(
        hour.probability is None and hour.symbol_code is None for hour in risk.hourly
    )


async def test_smhi_missing_sentinel_breaks_partial_dry_coverage(freezer):
    """Known dry values on both sides cannot cover a missing SMHI interval."""
    freezer.move_to(NOW)
    payload = _smhi([0.0, 9999, 0.0])
    for sample in payload["timeSeries"]:
        sample["data"]["probability_of_precipitation"] = 0
    payload["timeSeries"][1]["data"]["probability_of_precipitation"] = 9999
    provider = SmhiProvider(SmhiClient(payload))
    forecast = await provider.async_get_precipitation_forecast(LOCATION, _options())
    risk = await provider.async_get_rain_risk(
        LOCATION, replace(_options(), rain_risk_horizon_hours=1)
    )
    assert forecast.current_precipitation == 0.0
    assert forecast.rain_soon is None
    assert forecast.window_complete is False
    assert risk.max_probability is None
    assert risk.window_complete is False
