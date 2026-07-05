"""Regnradar provider implementation."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import logging
from typing import Any
from urllib.parse import urlsplit

from homeassistant.util import dt as dt_util

from ..api import RainRadarApiClient
from ..const import (
    DEFAULT_RADAR_AREA,
    PROVIDER_REGNRADAR,
    REGNRADAR_ATTRIBUTION,
    REGNRADAR_RADAR_AREAS,
    REGNRADAR_RADAR_URL,
)
from .base import RainRadarProvider
from .models import (
    CoverageStatus,
    Location,
    PrecipitationForecast,
    RadarBounds,
    RadarColorStep,
    RadarFrame,
    RadarFrameSet,
    RadarImageSize,
    RainRadarOptions,
    RainRiskForecast,
)

_LOGGER = logging.getLogger(__name__)

_REGNRADAR_BOUNDS = {
    "nordic": RadarBounds(
        south=53.0841628421789,
        west=-8.03410177882381,
        north=73.0558890312605,
        east=40.787194494374,
    ),
    "sweden": RadarBounds(
        south=53.6813981284917,
        west=5.2849968932444,
        north=70.0481652870767,
        east=29.7811924432583,
    ),
    "denmark": RadarBounds(
        south=52.1612155611617,
        west=2.99999999658801,
        north=60.2118665106867,
        east=20.7312188688115,
    ),
}
_REGNRADAR_IMAGE_SIZES = {
    "nordic": RadarImageSize(width=2392, height=2265),
    "sweden": RadarImageSize(width=623, height=908),
    "denmark": RadarImageSize(width=2195, height=1799),
}
_REGNRADAR_COLOR_SCALE = [
    RadarColorStep(label="Light", color="#0ea3dc"),
    RadarColorStep(label="Moderate", color="#84cf45"),
    RadarColorStep(label="Heavy", color="#ede73a"),
    RadarColorStep(label="Very heavy", color="#fe9e49"),
    RadarColorStep(label="Extreme", color="#fe625f"),
]


class RegnradarProvider:
    """Regnradar radar-image provider with delegated point forecast data."""

    def __init__(
        self,
        client: RainRadarApiClient,
        *,
        forecast_provider: RainRadarProvider,
    ) -> None:
        """Initialize provider."""
        self.client = client
        self.forecast_provider = forecast_provider
        self._radar_coverage_status = CoverageStatus.UNKNOWN

    @property
    def provider_id(self) -> str:
        """Return provider identifier."""
        return PROVIDER_REGNRADAR

    @property
    def provider_name(self) -> str:
        """Return provider display name."""
        return "Regnradar"

    @property
    def attribution(self) -> str:
        """Return provider attribution."""
        return (
            f"{REGNRADAR_ATTRIBUTION}; forecast data from "
            f"{self.forecast_provider.provider_name}"
        )

    @property
    def coverage_status(self) -> CoverageStatus:
        """Return latest known coverage status."""
        return self._radar_coverage_status

    async def async_get_precipitation_forecast(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> PrecipitationForecast:
        """Fetch normalized point precipitation forecast."""
        return await self.forecast_provider.async_get_precipitation_forecast(
            location, options
        )

    async def async_get_rain_risk(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> RainRiskForecast:
        """Fetch normalized rain-risk forecast."""
        return await self.forecast_provider.async_get_rain_risk(location, options)

    async def async_get_radar_frames(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> RadarFrameSet:
        """Fetch Regnradar coverage-bubble radar frame metadata."""
        area = _radar_area(options.radar_area)
        try:
            payload, cache = await self.client.async_get_json(
                "regnradar_radar",
                REGNRADAR_RADAR_URL,
                request_timeout=10,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unable to fetch Regnradar frame metadata: %s", err)
            return _empty_frame_set(area, self._radar_coverage_status)

        if not isinstance(payload, dict):
            self._radar_coverage_status = CoverageStatus.UNKNOWN
            return _empty_frame_set(area, CoverageStatus.UNKNOWN)

        area_payload = payload.get(area)
        if not isinstance(area_payload, dict):
            self._radar_coverage_status = CoverageStatus.OUTSIDE_COVERAGE
            return _empty_frame_set(area, CoverageStatus.OUTSIDE_COVERAGE)

        frames = _parse_radar_frames(area, area_payload)
        latest_time = _latest_observed_frame_time(frames)
        no_coverage = area_payload.get("no_coverage")
        coverage_status = (
            CoverageStatus.TEMPORARILY_UNAVAILABLE if not frames else CoverageStatus.OK
        )
        if isinstance(no_coverage, list) and no_coverage:
            coverage_status = CoverageStatus.OK
        self._radar_coverage_status = coverage_status

        return RadarFrameSet(
            frames=frames,
            image_size=_REGNRADAR_IMAGE_SIZES[area],
            bounds=_REGNRADAR_BOUNDS[area],
            projection_id="epsg3857_leaflet_image_overlay",
            product_id=f"regnradar_{area}",
            overlay_mode="regnradar_coverage",
            color_scale=_REGNRADAR_COLOR_SCALE,
            latest_time=latest_time,
            updated_at=cache.fetched_at,
            attribution=REGNRADAR_ATTRIBUTION,
            coverage_status=coverage_status,
            is_stale=_is_stale(cache.expires_at),
            cache=cache,
        )


def _empty_frame_set(area: str, coverage_status: CoverageStatus) -> RadarFrameSet:
    return RadarFrameSet(
        image_size=_REGNRADAR_IMAGE_SIZES[area],
        bounds=_REGNRADAR_BOUNDS[area],
        projection_id="epsg3857_leaflet_image_overlay",
        product_id=f"regnradar_{area}",
        overlay_mode="regnradar_coverage",
        color_scale=_REGNRADAR_COLOR_SCALE,
        attribution=REGNRADAR_ATTRIBUTION,
        coverage_status=coverage_status,
    )


def _radar_area(value: str | None) -> str:
    if value in REGNRADAR_RADAR_AREAS:
        return value
    return DEFAULT_RADAR_AREA


def _parse_radar_frames(area: str, payload: dict[str, Any]) -> list[RadarFrame]:
    images = payload.get("images")
    if not isinstance(images, list):
        return []

    frames: list[RadarFrame] = []
    for item in images:
        if not isinstance(item, dict):
            continue
        url = _normalize_image_url(item.get("image_url"))
        if url is None:
            continue
        frame_time = (
            _parse_datetime(item.get("time_utc"))
            or _parse_datetime(item.get("time_js"))
            or _parse_datetime(item.get("created_at"))
        )
        frame_type = str(item.get("type") or "obs")
        frame_id = _frame_id_for(area, frame_type, frame_time, url)
        frames.append(
            RadarFrame(
                frame_id=frame_id,
                time=frame_time,
                source_url=url,
                image_cache_key=f"regnradar_radar_image_{frame_id}",
                frame_type=frame_type,
                label=str(item.get("time_local"))
                if item.get("time_local") is not None
                else _label_for(frame_time),
                content_type="image/png",
            )
        )
    frames.sort(key=lambda frame: frame.time or datetime.min.replace(tzinfo=UTC))
    return frames


def _latest_observed_frame_time(frames: list[RadarFrame]) -> datetime | None:
    """Return the latest real radar frame time, excluding forecast images."""
    latest_observed = max(
        (
            frame.time
            for frame in frames
            if frame.time is not None
            and frame.frame_type.lower() not in {"fcst", "forecast"}
        ),
        default=None,
    )
    if latest_observed is not None:
        return latest_observed
    return max((frame.time for frame in frames if frame.time is not None), default=None)


def _normalize_image_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    url = f"https:{value}" if value.startswith("//") else value
    split = urlsplit(url)
    if (
        split.scheme != "https"
        or split.netloc != "api.regnradar.se"
        or not split.path.startswith("/radar/file/")
        or not split.path.endswith(".png")
    ):
        return None
    return url


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _label_for(frame_time: datetime | None) -> str | None:
    if frame_time is None:
        return None
    return dt_util.as_local(frame_time).strftime("%H:%M")


def _frame_id_for(
    area: str,
    frame_type: str,
    frame_time: datetime | None,
    url: str,
) -> str:
    if frame_time is not None:
        suffix = frame_time.strftime("%Y%m%dT%H%M%SZ")
    else:
        suffix = hashlib.sha1(url.encode()).hexdigest()[:12]
    return f"regnradar-{area}-{frame_type}-{suffix}"


def _is_stale(expires_at: datetime | None) -> bool:
    return expires_at is not None and expires_at < datetime.now(UTC)
