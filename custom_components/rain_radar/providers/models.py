"""Normalized provider models for Rain Radar."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class CoverageStatus(StrEnum):
    """Provider coverage state for a configured location."""

    OK = "ok"
    OUTSIDE_COVERAGE = "outside_coverage"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    UNKNOWN = "unknown"


class ProviderHealth(StrEnum):
    """Provider data health."""

    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class Location:
    """Configured point location."""

    latitude: float
    longitude: float


@dataclass(frozen=True, slots=True)
class RainRadarOptions:
    """Runtime options used by providers."""

    contact: str
    forecast_provider: str
    radar_area: str
    rain_threshold: float
    rain_soon_window_minutes: int
    sample_radius_m: int
    rain_risk_horizon_hours: int


@dataclass(frozen=True, slots=True)
class CacheMetadata:
    """HTTP cache metadata for a provider response."""

    fetched_at: datetime | None = None
    expires_at: datetime | None = None
    etag: str | None = None
    last_modified: str | None = None
    from_cache: bool = False


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """Provider status exposed to diagnostics and entities."""

    provider_id: str
    provider_name: str
    attribution: str
    coverage_status: CoverageStatus = CoverageStatus.UNKNOWN
    health: ProviderHealth = ProviderHealth.OK
    message: str | None = None
    last_success: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class PrecipitationSample:
    """Precipitation value at a point in time."""

    time: datetime
    precipitation_rate: float | None


@dataclass(frozen=True, slots=True)
class PrecipitationForecast:
    """Normalized precipitation time series."""

    samples: list[PrecipitationSample] = field(default_factory=list)
    current_precipitation: float | None = None
    rain_now: bool | None = None
    rain_soon: bool | None = None
    rain_arrival_minutes: int | None = None
    updated_at: datetime | None = None
    latest_time: datetime | None = None
    coverage_status: CoverageStatus = CoverageStatus.UNKNOWN
    is_stale: bool = False
    cache: CacheMetadata = field(default_factory=CacheMetadata)


@dataclass(frozen=True, slots=True)
class RainRiskHour:
    """Hourly rain risk detail."""

    time: datetime
    probability: int
    precipitation_amount: float | None
    symbol_code: str | None


@dataclass(frozen=True, slots=True)
class RainRiskForecast:
    """Normalized precipitation probability forecast."""

    max_probability: int | None
    hourly: list[RainRiskHour] = field(default_factory=list)
    updated_at: datetime | None = None
    is_stale: bool = False
    cache: CacheMetadata = field(default_factory=CacheMetadata)


@dataclass(frozen=True, slots=True)
class RadarFrame:
    """Radar frame metadata for the dashboard card."""

    frame_id: str
    time: datetime | None
    source_url: str
    image_cache_key: str
    frame_type: str = "image"
    label: str | None = None
    content_type: str = "image/png"


@dataclass(frozen=True, slots=True)
class RadarImageSize:
    """Radar image dimensions in pixels."""

    width: int
    height: int


@dataclass(frozen=True, slots=True)
class RadarBounds:
    """Geographic bounds for a radar image."""

    south: float
    west: float
    north: float
    east: float


@dataclass(frozen=True, slots=True)
class RadarColorStep:
    """Radar color scale step."""

    label: str
    color: str


@dataclass(frozen=True, slots=True)
class RadarFrameSet:
    """Radar frame collection."""

    frames: list[RadarFrame] = field(default_factory=list)
    animation_url: str | None = None
    image_size: RadarImageSize = field(
        default_factory=lambda: RadarImageSize(width=659, height=761)
    )
    bounds: RadarBounds | None = None
    projection_id: str = "met_no_lambert_conformal_conic_nordic"
    product_id: str = "5level_reflectivity"
    overlay_mode: str = "precipitation_mask"
    color_scale: list[RadarColorStep] = field(default_factory=list)
    latest_time: datetime | None = None
    updated_at: datetime | None = None
    attribution: str | None = None
    coverage_status: CoverageStatus = CoverageStatus.UNKNOWN
    is_stale: bool = False
    cache: CacheMetadata = field(default_factory=CacheMetadata)
