"""Provider interface for Rain Radar."""

from __future__ import annotations

from typing import Protocol

from .models import (
    CoverageStatus,
    Location,
    PrecipitationForecast,
    RadarFrameSet,
    RainRadarOptions,
    RainRiskForecast,
)


class RainRadarProvider(Protocol):
    """Provider protocol used by the coordinator."""

    @property
    def provider_id(self) -> str:
        """Return provider identifier."""

    @property
    def provider_name(self) -> str:
        """Return provider display name."""

    @property
    def attribution(self) -> str:
        """Return provider attribution."""

    @property
    def coverage_status(self) -> CoverageStatus:
        """Return latest known coverage status."""

    async def async_get_precipitation_forecast(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> PrecipitationForecast:
        """Fetch normalized point precipitation forecast."""

    async def async_get_rain_risk(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> RainRiskForecast:
        """Fetch normalized rain-risk forecast."""

    async def async_get_radar_frames(
        self,
        location: Location,
        options: RainRadarOptions,
    ) -> RadarFrameSet:
        """Fetch normalized radar frame metadata."""
