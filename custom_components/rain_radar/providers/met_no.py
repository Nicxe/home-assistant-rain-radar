"""MET Norway provider implementation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import logging
from typing import Any
from urllib.parse import urlsplit

from homeassistant.util import dt as dt_util

from ..api import RainRadarApiClient
from ..const import (
    MET_NO_ATTRIBUTION,
    MET_NO_LOCATIONFORECAST_COMPLETE_URL,
    MET_NO_NOWCAST_COMPLETE_URL,
    MET_NO_RADAR_ANIMATION_URL,
    MET_NO_RADAR_AREA,
    MET_NO_RADAR_AVAILABLE_URL,
    MET_NO_RADAR_CONTENT,
    MET_NO_RADAR_LOCATIONS_URL,
    MET_NO_RADAR_PRODUCT,
    PROVIDER_MET_NO,
)
from .models import (
    CoverageStatus,
    Location,
    PrecipitationForecast,
    PrecipitationSample,
    RadarBounds,
    RadarColorStep,
    RadarFrame,
    RadarFrameSet,
    RadarImageSize,
    RainRadarOptions,
    RainRiskForecast,
    RainRiskHour,
)

_LOGGER = logging.getLogger(__name__)
_NORDIC_RADAR_BOUNDS = RadarBounds(
    south=52.295184,
    west=3.448806,
    north=71.520959,
    east=40.837085,
)
_NORDIC_RADAR_IMAGE_SIZE = RadarImageSize(width=659, height=761)
_NORDIC_RADAR_COLOR_SCALE = [
    RadarColorStep(label="Light", color="#a5e887"),
    RadarColorStep(label="Moderate", color="#ffff00"),
    RadarColorStep(label="Heavy", color="#ff9900"),
    RadarColorStep(label="Very heavy", color="#e6332a"),
]


def _get(obj: dict[str, Any] | None, *path: str) -> Any:
    cur: Any = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_probability(value: Any) -> int:
    numeric = _as_float(value)
    if numeric is None:
        return 0
    return round(max(0.0, min(100.0, numeric)))


def _coverage_from_payload(payload: dict[str, Any]) -> CoverageStatus:
    serialized = str(payload).lower()
    if "outside" in serialized and "coverage" in serialized:
        return CoverageStatus.OUTSIDE_COVERAGE
    if "no radar coverage" in serialized:
        return CoverageStatus.OUTSIDE_COVERAGE
    if ("radar_coverage" in serialized or "radarcoverage" in serialized) and (
        "ok" in serialized or "true" in serialized
    ):
        return CoverageStatus.OK
    return CoverageStatus.OK


class MetNoProvider:
    """MET Norway provider."""

    def __init__(self, client: RainRadarApiClient) -> None:
        """Initialize provider."""
        self.client = client
        self._coverage_status = CoverageStatus.UNKNOWN

    @property
    def provider_id(self) -> str:
        """Return provider identifier."""
        return PROVIDER_MET_NO

    @property
    def provider_name(self) -> str:
        """Return provider display name."""
        return "MET Norway"

    @property
    def attribution(self) -> str:
        """Return provider attribution."""
        return MET_NO_ATTRIBUTION

    @property
    def coverage_status(self) -> CoverageStatus:
        """Return latest known coverage status."""
        return self._coverage_status

    async def async_get_precipitation_forecast(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> PrecipitationForecast:
        """Fetch nowcast precipitation data."""
        payload, cache = await self.client.async_get_json(
            "met_no_nowcast",
            MET_NO_NOWCAST_COMPLETE_URL,
            params={
                "lat": round(location.latitude, 4),
                "lon": round(location.longitude, 4),
            },
        )
        if not isinstance(payload, dict):
            return PrecipitationForecast(
                coverage_status=CoverageStatus.UNKNOWN,
                cache=cache,
            )

        coverage = _coverage_from_payload(payload)
        self._coverage_status = coverage
        updated_at = _parse_datetime(_get(payload, "properties", "meta", "updated_at"))
        samples = _parse_precipitation_samples(payload)

        current = _current_precipitation(samples)
        rain_now = (
            current is not None and current >= options.rain_threshold
            if samples
            else None
        )
        rain_arrival = _arrival_minutes(samples, options.rain_threshold)
        rain_soon = (
            rain_arrival is not None
            and rain_arrival <= options.rain_soon_window_minutes
        )
        latest_time = max((sample.time for sample in samples), default=updated_at)

        return PrecipitationForecast(
            samples=samples,
            current_precipitation=current,
            rain_now=rain_now,
            rain_soon=rain_soon,
            rain_arrival_minutes=rain_arrival,
            updated_at=updated_at,
            latest_time=latest_time,
            coverage_status=coverage,
            is_stale=_is_stale(cache.expires_at),
            cache=cache,
        )

    async def async_get_rain_risk(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> RainRiskForecast:
        """Fetch locationforecast precipitation probability data."""
        payload, cache = await self.client.async_get_json(
            "met_no_locationforecast",
            MET_NO_LOCATIONFORECAST_COMPLETE_URL,
            params={
                "lat": round(location.latitude, 4),
                "lon": round(location.longitude, 4),
            },
        )
        if not isinstance(payload, dict):
            return RainRiskForecast(max_probability=None, cache=cache)

        updated_at = _parse_datetime(_get(payload, "properties", "meta", "updated_at"))
        hourly = _parse_rain_risk_hours(payload, options.rain_risk_horizon_hours)
        max_probability = max((hour.probability for hour in hourly), default=None)

        return RainRiskForecast(
            max_probability=max_probability,
            hourly=hourly,
            updated_at=updated_at,
            is_stale=_is_stale(cache.expires_at),
            cache=cache,
        )

    async def async_get_radar_frames(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> RadarFrameSet:
        """Fetch radar frame metadata when available."""
        try:
            payload, cache = await self.client.async_get_json(
                "met_no_radar_available",
                MET_NO_RADAR_AVAILABLE_URL,
                params={
                    "area": MET_NO_RADAR_AREA,
                    "content": MET_NO_RADAR_CONTENT,
                    "type": MET_NO_RADAR_PRODUCT,
                },
                request_timeout=10,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unable to fetch MET radar frame metadata: %s", err)
            return RadarFrameSet(
                animation_url=MET_NO_RADAR_ANIMATION_URL,
                image_size=_NORDIC_RADAR_IMAGE_SIZE,
                bounds=_NORDIC_RADAR_BOUNDS,
                product_id=MET_NO_RADAR_PRODUCT,
                color_scale=_NORDIC_RADAR_COLOR_SCALE,
                attribution=MET_NO_ATTRIBUTION,
                coverage_status=self._coverage_status,
            )

        bounds = _NORDIC_RADAR_BOUNDS
        try:
            locations_payload, _locations_cache = await self.client.async_get_json(
                "met_no_radar_locations",
                MET_NO_RADAR_LOCATIONS_URL,
                request_timeout=10,
            )
            bounds = _parse_radar_bounds(locations_payload)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Unable to fetch MET radar area metadata: %s", err)

        frames = _parse_radar_frames(payload)
        latest_time = max(
            (frame.time for frame in frames if frame.time is not None),
            default=None,
        )
        return RadarFrameSet(
            frames=frames,
            animation_url=MET_NO_RADAR_ANIMATION_URL,
            image_size=_NORDIC_RADAR_IMAGE_SIZE,
            bounds=bounds,
            product_id=MET_NO_RADAR_PRODUCT,
            color_scale=_NORDIC_RADAR_COLOR_SCALE,
            latest_time=latest_time,
            updated_at=cache.fetched_at,
            attribution=MET_NO_ATTRIBUTION,
            coverage_status=self._coverage_status,
            is_stale=_is_stale(cache.expires_at),
            cache=cache,
        )


def _parse_precipitation_samples(payload: dict[str, Any]) -> list[PrecipitationSample]:
    timeseries = _get(payload, "properties", "timeseries")
    if not isinstance(timeseries, list):
        return []

    samples: list[PrecipitationSample] = []
    for item in timeseries:
        if not isinstance(item, dict):
            continue
        time = _parse_datetime(item.get("time"))
        if time is None:
            continue
        rate = _as_float(_get(item, "data", "instant", "details", "precipitation_rate"))
        if rate is None:
            rate = _as_float(
                _get(item, "data", "next_1_hours", "details", "precipitation_amount")
            )
        samples.append(PrecipitationSample(time=time, precipitation_rate=rate))
    samples.sort(key=lambda sample: sample.time)
    return samples


def _parse_rain_risk_hours(
    payload: dict[str, Any],
    horizon_hours: int,
) -> list[RainRiskHour]:
    timeseries = _get(payload, "properties", "timeseries")
    if not isinstance(timeseries, list):
        return []

    now = datetime.now(UTC)
    end = now + timedelta(hours=horizon_hours)
    hourly: list[RainRiskHour] = []

    for item in timeseries:
        if not isinstance(item, dict):
            continue
        time = _parse_datetime(item.get("time"))
        if time is None or time <= now:
            continue
        if time > end:
            break

        probability = _clamp_probability(
            _get(
                item, "data", "next_1_hours", "details", "probability_of_precipitation"
            )
        )
        precipitation_amount = _as_float(
            _get(item, "data", "next_1_hours", "details", "precipitation_amount")
        )
        symbol = _get(item, "data", "next_1_hours", "summary", "symbol_code")

        hourly.append(
            RainRiskHour(
                time=time,
                probability=probability,
                precipitation_amount=precipitation_amount,
                symbol_code=symbol if isinstance(symbol, str) else None,
            )
        )
        if len(hourly) >= horizon_hours:
            break
    return hourly


def _parse_radar_frames(payload: dict[str, Any] | list[Any]) -> list[RadarFrame]:
    source = payload
    if isinstance(payload, dict):
        for key in ("frames", "items", "products", "files", "available"):
            value = payload.get(key)
            if isinstance(value, list):
                source = value
                break

    if not isinstance(source, list):
        return []

    frames: list[RadarFrame] = []
    for item in source:
        if isinstance(item, str):
            frame_time = _parse_datetime(item)
            if frame_time is None:
                continue
            url = _frame_url_for_time(frame_time)
            frame_id = _frame_id_for(frame_time, url)
            frames.append(
                RadarFrame(
                    frame_id=frame_id,
                    time=frame_time,
                    source_url=url,
                    image_cache_key=f"met_no_radar_image_{frame_id}",
                    label=dt_util.as_local(frame_time).strftime("%H:%M"),
                )
            )
            continue

        if not isinstance(item, dict):
            continue
        params = item.get("params")
        if isinstance(params, dict) and (
            params.get("content") != MET_NO_RADAR_CONTENT
            or params.get("area") != MET_NO_RADAR_AREA
            or params.get("type") != MET_NO_RADAR_PRODUCT
        ):
            continue

        url = item.get("url") or item.get("uri") or item.get("href")
        time = (
            _parse_datetime(item.get("time"))
            or _parse_datetime(params.get("time") if isinstance(params, dict) else None)
            or _parse_datetime(item.get("valid_time"))
            or _parse_datetime(item.get("updated_at"))
        )
        if not isinstance(url, str):
            if time is None:
                continue
            url = _frame_url_for_time(time)
        if not _is_met_radar_url(url):
            continue
        frame_id = _frame_id_for(time, url)
        frames.append(
            RadarFrame(
                frame_id=frame_id,
                time=time,
                source_url=url,
                image_cache_key=f"met_no_radar_image_{frame_id}",
                frame_type=str(
                    params.get("content")
                    if isinstance(params, dict) and params.get("content") is not None
                    else item.get("type", "image")
                ),
                label=str(item.get("label")) if item.get("label") is not None else None,
            )
        )
    frames.sort(key=lambda frame: frame.time or datetime.min.replace(tzinfo=UTC))
    return frames


def _is_met_radar_url(url: str) -> bool:
    split = urlsplit(url)
    return (
        split.scheme == "https"
        and split.netloc == "api.met.no"
        and split.path.startswith("/weatherapi/radar/2.0/")
    )


def _parse_radar_bounds(payload: dict[str, Any] | list[Any]) -> RadarBounds:
    if not isinstance(payload, dict):
        return _NORDIC_RADAR_BOUNDS
    features = payload.get("features")
    if not isinstance(features, list):
        return _NORDIC_RADAR_BOUNDS

    for feature in features:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        if (
            feature.get("id") != MET_NO_RADAR_AREA
            and properties.get("id") != MET_NO_RADAR_AREA
        ):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            continue
        points = coordinates[0] if geometry.get("type") == "Polygon" else coordinates
        if not isinstance(points, list):
            continue
        lon_values: list[float] = []
        lat_values: list[float] = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            lon = _as_float(point[0])
            lat = _as_float(point[1])
            if lon is None or lat is None:
                continue
            lon_values.append(lon)
            lat_values.append(lat)
        if not lon_values or not lat_values:
            continue
        bounds = RadarBounds(
            south=min(lat_values),
            west=min(lon_values),
            north=max(lat_values),
            east=max(lon_values),
        )
        if bounds.north > bounds.south and bounds.east > bounds.west:
            return bounds

    return _NORDIC_RADAR_BOUNDS


def _frame_id_for(frame_time: datetime | None, url: str) -> str:
    if frame_time is not None:
        suffix = frame_time.strftime("%Y%m%dT%H%M%SZ")
    else:
        suffix = hashlib.sha1(url.encode()).hexdigest()[:12]
    return (
        f"met-no-{MET_NO_RADAR_AREA}-{MET_NO_RADAR_PRODUCT.replace('_', '-')}-{suffix}"
    )


def _frame_url_for_time(frame_time: datetime) -> str:
    iso_time = frame_time.isoformat().replace("+00:00", "Z")
    return (
        "https://api.met.no/weatherapi/radar/2.0/"
        f"?type={MET_NO_RADAR_PRODUCT}&area={MET_NO_RADAR_AREA}"
        f"&content={MET_NO_RADAR_CONTENT}&time={iso_time}"
    )


def _current_precipitation(samples: list[PrecipitationSample]) -> float | None:
    if not samples:
        return None
    now = datetime.now(UTC)
    past_or_current = [sample for sample in samples if sample.time <= now]
    current = past_or_current[-1] if past_or_current else samples[0]
    return current.precipitation_rate


def _arrival_minutes(
    samples: list[PrecipitationSample],
    rain_threshold: float,
) -> int | None:
    now = datetime.now(UTC)
    for sample in samples:
        if sample.time < now:
            continue
        if sample.precipitation_rate is None:
            continue
        if sample.precipitation_rate >= rain_threshold:
            return max(0, round((sample.time - now).total_seconds() / 60))
    return None


def _is_stale(expires_at: datetime | None) -> bool:
    return expires_at is not None and expires_at < datetime.now(UTC)
