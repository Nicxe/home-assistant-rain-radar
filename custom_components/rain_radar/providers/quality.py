"""Shared interval validity and missing-value rules for point forecasts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from .models import (
    CacheMetadata,
    CoverageStatus,
    PrecipitationForecast,
    PrecipitationSample,
    RainRadarOptions,
    RainRiskForecast,
    RainRiskHour,
)


def window_is_complete(
    intervals: list[tuple[datetime, datetime]], start: datetime, end: datetime
) -> bool:
    """Require continuous known values throughout the requested window."""
    cursor = start
    for interval_start, interval_end in sorted(intervals):
        if interval_end <= cursor:
            continue
        if interval_start > cursor:
            return False
        cursor = interval_end
        if cursor >= end:
            return True
    return False


def _source_is_stale(
    updated_at: datetime | None, now: datetime, maximum_age: timedelta
) -> bool:
    return updated_at is not None and (
        now - updated_at > maximum_age or updated_at > now + timedelta(minutes=5)
    )


def precipitation_forecast(
    samples: list[PrecipitationSample],
    options: RainRadarOptions,
    cache: CacheMetadata,
    updated_at: datetime | None,
    coverage: CoverageStatus,
    *,
    data_kind: str,
    resolution_minutes: int,
    maximum_age: timedelta,
    current_grace: timedelta = timedelta(0),
    source_reference: datetime | None = None,
) -> PrecipitationForecast:
    """Keep unknown, old, rainy and completely dry windows distinct."""
    now = datetime.now(UTC)
    end = now + timedelta(minutes=options.rain_soon_window_minutes)
    valid = [
        sample
        for sample in samples
        if sample.interval_start is not None
        and sample.interval_end is not None
        and sample.interval_start < sample.interval_end
    ]
    latest = max((sample.interval_end for sample in valid), default=None)
    stale = (
        _source_is_stale(updated_at, now, maximum_age)
        or _source_is_stale(source_reference, now, maximum_age)
        or (latest is not None and latest <= now)
    )
    current_sample = next(
        (
            sample
            for sample in valid
            if sample.interval_start <= now < sample.interval_end
        ),
        None,
    )
    if current_sample is None and current_grace:
        current_sample = next(
            (
                sample
                for sample in reversed(valid)
                if sample.time <= now and now - sample.time <= current_grace
            ),
            None,
        )
    current = current_sample.precipitation_rate if current_sample is not None else None
    complete = window_is_complete(
        [
            (sample.interval_start, sample.interval_end)
            for sample in valid
            if sample.precipitation_rate is not None
        ],
        now,
        end,
    )
    rainy = [
        sample
        for sample in valid
        if sample.interval_end > now
        and sample.precipitation_rate is not None
        and sample.precipitation_rate >= options.rain_threshold
    ]
    rain_now = current >= options.rain_threshold if current is not None else None
    arrival = (
        0
        if rain_now
        else max(0, round((rainy[0].interval_start - now).total_seconds() / 60))
        if rainy
        else None
    )
    rain_soon = (
        True
        if rain_now or any(sample.interval_start < end for sample in rainy)
        else False
        if complete
        else None
    )
    if stale or coverage == CoverageStatus.OUTSIDE_COVERAGE:
        current = rain_now = rain_soon = arrival = None
        complete = False
    return PrecipitationForecast(
        samples=samples,
        current_precipitation=current,
        rain_now=rain_now,
        rain_soon=rain_soon,
        rain_arrival_minutes=arrival,
        updated_at=updated_at,
        latest_time=max((sample.time for sample in samples), default=updated_at),
        coverage_status=coverage,
        is_stale=stale,
        cache=cache,
        observation_time=current_sample.time
        if current_sample is not None and data_kind == "nowcast"
        else None,
        data_kind=data_kind,
        resolution_minutes=resolution_minutes,
        window_complete=complete,
        reason="stale_data" if stale else "incomplete_window" if not complete else None,
    )


def rain_risk_forecast(
    hourly: list[RainRiskHour],
    horizon_hours: int,
    cache: CacheMetadata,
    updated_at: datetime | None,
    *,
    latest_time: datetime | None = None,
    source_reference: datetime | None = None,
) -> RainRiskForecast:
    """Summarize overlapping intervals without inventing missing probabilities."""
    now = datetime.now(UTC)
    complete = window_is_complete(
        [
            (hour.interval_start, hour.interval_end)
            for hour in hourly
            if hour.probability is not None
            and hour.interval_start is not None
            and hour.interval_end is not None
        ],
        now,
        now + timedelta(hours=horizon_hours),
    )
    stale = (
        _source_is_stale(updated_at, now, timedelta(hours=12))
        or _source_is_stale(source_reference, now, timedelta(hours=12))
        or (latest_time is not None and latest_time <= now)
    )
    maximum = max(
        (hour.probability for hour in hourly if hour.probability is not None),
        default=None,
    )
    if stale or (maximum == 0 and not complete):
        maximum = None
    return RainRiskForecast(
        max_probability=maximum,
        hourly=hourly,
        updated_at=updated_at,
        is_stale=stale,
        cache=cache,
        data_kind="model",
        resolution_minutes=60,
        window_complete=complete and not stale,
        reason="stale_data" if stale else "incomplete_window" if not complete else None,
    )
