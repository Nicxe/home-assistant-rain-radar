"""Tests for Rain Radar frontend resource management."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.const import CONF_ID, CONF_TYPE
import pytest

from custom_components.rain_radar import frontend
from custom_components.rain_radar.const import (
    CARD_CANONICAL_BASE_URL,
    CARD_LEGACY_BASE_URL,
    FRONTEND_DATA_KEY,
)


class FakeResources:
    """Fake Lovelace resources collection."""

    def __init__(self, items: list[dict] | None = None) -> None:
        self._items = list(items or [])
        self.loaded = False
        self.create_calls = 0
        self.update_calls = 0

    async def async_load(self) -> None:
        self.loaded = True

    def async_items(self) -> list[dict]:
        return list(self._items)

    async def async_create_item(self, data: dict) -> dict:
        self.create_calls += 1
        item = {
            CONF_ID: f"generated-{self.create_calls}",
            "url": data["url"],
            CONF_TYPE: data.get("res_type", "module"),
        }
        self._items.append(item)
        return item

    async def async_update_item(self, item_id: str, updates: dict) -> dict:
        self.update_calls += 1
        for item in self._items:
            if item[CONF_ID] == item_id:
                item["url"] = updates["url"]
                item[CONF_TYPE] = updates.get("res_type", item[CONF_TYPE])
                return item
        raise KeyError(item_id)


@pytest.fixture
def fake_hass():
    """Return simple fake hass."""
    return SimpleNamespace(data={})


@pytest.mark.asyncio
async def test_ensure_resource_creates_when_missing(fake_hass, monkeypatch) -> None:
    """Test resource creation."""
    resources = FakeResources()
    fake_hass.data[LOVELACE_DATA] = SimpleNamespace(resources=resources)

    async def _fake_cache_key(_hass):
        return "0.0.0-123"

    monkeypatch.setattr(frontend, "_cache_key_for_dev", _fake_cache_key)

    ok = await frontend._async_ensure_card_resource(fake_hass)

    assert ok is True
    assert resources.create_calls == 1
    assert resources.async_items()[0]["url"] == f"{CARD_LEGACY_BASE_URL}?v=0.0.0-123"


@pytest.mark.asyncio
async def test_ensure_resource_updates_existing_canonical(
    fake_hass, monkeypatch
) -> None:
    """Test migration from canonical static URL to /local URL."""
    resources = FakeResources(
        [
            {
                CONF_ID: "abc",
                CONF_TYPE: "module",
                "url": f"{CARD_CANONICAL_BASE_URL}?v=old",
            }
        ]
    )
    fake_hass.data[LOVELACE_DATA] = SimpleNamespace(resources=resources)

    async def _fake_cache_key(_hass):
        return "0.0.0-456"

    monkeypatch.setattr(frontend, "_cache_key_for_dev", _fake_cache_key)

    ok = await frontend._async_ensure_card_resource(fake_hass)

    assert ok is True
    assert resources.update_calls == 1
    assert resources.async_items()[0]["url"] == f"{CARD_LEGACY_BASE_URL}?v=0.0.0-456"


@pytest.mark.asyncio
async def test_ensure_resource_is_idempotent(fake_hass, monkeypatch) -> None:
    """Test idempotent resource setup."""
    resources = FakeResources(
        [
            {
                CONF_ID: "abc",
                CONF_TYPE: "module",
                "url": f"{CARD_LEGACY_BASE_URL}?v=stable",
            }
        ]
    )
    fake_hass.data[LOVELACE_DATA] = SimpleNamespace(resources=resources)

    async def _fake_cache_key(_hass):
        return "stable"

    monkeypatch.setattr(frontend, "_cache_key_for_dev", _fake_cache_key)

    ok = await frontend._async_ensure_card_resource(fake_hass)

    assert ok is True
    assert resources.create_calls == 0
    assert resources.update_calls == 0


@pytest.mark.asyncio
async def test_ensure_resource_falls_back_without_lovelace(
    fake_hass, monkeypatch
) -> None:
    """Test graceful behavior when Lovelace is unavailable."""

    async def _fake_cache_key(_hass):
        return "fallback"

    monkeypatch.setattr(frontend, "_cache_key_for_dev", _fake_cache_key)

    assert await frontend._async_ensure_card_resource(fake_hass) is False


@pytest.mark.asyncio
async def test_setup_frontend_refreshes_card_after_integration_reload(
    fake_hass,
    monkeypatch,
) -> None:
    """Test card resync when cache key changes."""
    fake_hass.data[FRONTEND_DATA_KEY] = {"setup_done": True}
    calls: list[str] = []

    async def _fake_cache_key(_hass):
        return "0.0.0-new"

    async def _fake_sync(_hass):
        calls.append("sync")

    async def _fake_ensure(_hass):
        calls.append("resource")
        return True

    monkeypatch.setattr(frontend, "_cache_key_for_dev", _fake_cache_key)
    monkeypatch.setattr(frontend, "_async_sync_card_to_local_www", _fake_sync)
    monkeypatch.setattr(frontend, "_async_ensure_card_resource", _fake_ensure)

    await frontend.async_setup_frontend(fake_hass)

    assert calls == ["sync", "resource"]
    assert fake_hass.data[FRONTEND_DATA_KEY]["cache_key"] == "0.0.0-new"


@pytest.mark.asyncio
async def test_card_sync_runs_filesystem_work_in_executor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Test frontend asset sync keeps blocking filesystem work in the executor."""
    executor_calls = []

    def _fake_sync_frontend_assets(www_path: str) -> None:
        assert www_path == str(tmp_path / "www")

    async def _fake_executor(func, *args):
        executor_calls.append((func, args))
        return func(*args)

    fake_hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda path: str(tmp_path / path)),
        async_add_executor_job=_fake_executor,
    )
    monkeypatch.setattr(
        frontend,
        "_sync_frontend_assets_to_local_www",
        _fake_sync_frontend_assets,
    )

    await frontend._async_sync_card_to_local_www(fake_hass)

    assert executor_calls == [(_fake_sync_frontend_assets, (str(tmp_path / "www"),))]


def test_bundled_card_registers_custom_card() -> None:
    """Test card custom element registration strings."""
    card_text = Path(frontend._card_file_path()).read_text(encoding="utf-8")

    assert "customElements.define(CARD_TYPE, RainRadarCard)" in card_text
    assert "window.customCards.push" in card_text
    assert "type: CARD_TYPE" in card_text


def test_bundled_card_exposes_layer_and_forecast_options() -> None:
    """Test card includes configurable map layers and forecast availability display."""
    card_text = Path(frontend._card_file_path()).read_text(encoding="utf-8")

    assert "show_legend: false" in card_text
    assert "show_info_panel: false" in card_text
    assert "show_location_marker: true" in card_text
    assert "show_forecast: true" in card_text
    assert "tile_url: DEFAULT_TILE_URL" in card_text
    assert "tile_attribution: DEFAULT_TILE_ATTRIBUTION" in card_text
    assert "rainSoonStatus" in card_text
    assert "locationNameFromEntity" in card_text
    assert "const statusLabel = this._t(status.labelKey)" in card_text
    assert 'this._setMetaValue("status", statusLabel)' in card_text
    assert 'friendly_name || "Rain Radar"' not in card_text
    assert "coverage_opacity: DEFAULT_COVERAGE_OPACITY" in card_text
    assert "animation_interval_ms: DEFAULT_ANIMATION_INTERVAL_MS" in card_text
    assert "arrival_format: DEFAULT_ARRIVAL_FORMAT" in card_text
    assert "arrivalText" in card_text
    assert "minuteValue" in card_text
    assert "durationMinutes" in card_text
    assert "clockTimeAfterMinutes" in card_text
    assert "arrivalMinutesFromForecastSamples" in card_text
    assert "const arrivalState = stateText(arrival, null)" in card_text
    assert "const arrivalMinutes = arrival" in card_text
    assert "? minuteValue(arrivalState)" in card_text
    assert "context.arrivalMinutes" in card_text
    assert "const minutes = Number(value)" not in card_text
    assert 'name: "arrival_format"' in card_text
    assert '{ value: "duration_time", label: "Hours, minutes and time" }' in card_text
    assert "buildTimelineFrames" in card_text
    assert "radarFrameType" in card_text
    assert "isObservedRadarFrame" in card_text
    assert '["fcst", "forecast"].includes(radarFrameType(frame))' in card_text
    assert "latestRadarText" in card_text
    assert (
        'this._setMetaValue("latest_radar", latestRadar || this._t("unknown"))'
        in card_text
    )
    assert "this._setMetaVisibility(context, { latestRadar })" in card_text
    assert "stateText(radarTime, null)" in card_text
    assert "availableForecastMinutes" in card_text
    assert "if (!Number.isFinite(sample.rate)) break" in card_text
    assert "Forecast data +${context.forecastMinutesAvailable} min" in card_text
    assert "has-forecast" in card_text
    assert "repeating-linear-gradient" in card_text
    assert "forecastSegment.hidden = !hasForecast" in card_text
    assert (
        'forecastSegment.title = "Point forecast data, not radar imagery"' in card_text
    )
    assert "locationFromPayload" in card_text
    assert "boundsFromPayload" in card_text
    assert "Forecast +60 min" not in card_text
    assert "latestObserved.imageUrl" not in card_text
    assert "latestObserved.id" not in card_text
    assert 'forecast: "show_forecast"' in card_text
    assert "Extend timeline with forecast" not in card_text


def test_bundled_card_uses_leaflet_osm_map_with_provider_overlays() -> None:
    """Test card uses an interactive Leaflet OSM map and provider radar overlays."""
    card_text = Path(frontend._card_file_path()).read_text(encoding="utf-8")

    assert "/local/rain_radar/vendor/leaflet/leaflet.css" in card_text
    assert "/local/rain_radar/vendor/leaflet/leaflet-src.esm.js" in card_text
    assert "L.tileLayer" in card_text
    assert "L.imageOverlay" in card_text
    assert "L.layerGroup" in card_text
    assert "L.rectangle" in card_text
    assert "WEB_MERCATOR_WORLD_BOUNDS" in card_text
    assert "rain-radar-outside-coverage" in card_text
    assert "_syncOutsideCoverageLayer" in card_text
    assert "_removeOutsideCoverageLayer" in card_text
    assert "canvasMaskRadarOverlayToObjectUrl" in card_text
    assert "canvasSoftenRegnradarCoverageToObjectUrl" in card_text
    assert "isFiveLevelReflectivityPixel" in card_text
    assert "isRegnradarCoverageShadePixel" in card_text
    assert "RADAR_OVERLAY_CACHE_LIMIT" in card_text
    assert "_prepareOverlayUrl" in card_text
    assert "_preloadUpcomingOverlays" in card_text
    assert "_waitForLayerLoad" in card_text
    assert "overlayMode: payload.overlay_mode" in card_text
    assert "regnradar_coverage" in card_text
    assert "URL.revokeObjectURL" in card_text
    assert "this._animationIntervalMs()" in card_text
    assert "DEFAULT_ANIMATION_INTERVAL_MS = 550" in card_text
    assert "selector: { number: { min: 250, max: 2000, step: 50" in card_text
    assert "1150" not in card_text
    assert "Radar: Regnradar/Vackertväder" in card_text
    assert "scrollWheelZoom: this._config.map_scroll_wheel === true" in card_text
    assert 'MAP_TILE_REFERRER_POLICY = "strict-origin-when-cross-origin"' in card_text
    assert "referrerPolicy: MAP_TILE_REFERRER_POLICY" in card_text
    assert "interactive: false" in card_text
    assert "tile.openstreetmap.org" in card_text
    assert "L.CRS.Simple" not in card_text
    assert "RADAR_PIXEL_TRANSFORM" not in card_text
    assert "unpkg.com" not in card_text
    assert "cdn.jsdelivr.net" not in card_text
    assert "regnradar.se" not in card_text.lower()


def test_bundled_card_crossfades_after_next_layer_loads() -> None:
    """Test frame changes do not fade out the current layer before the next one loads."""
    card_text = Path(frontend._card_file_path()).read_text(encoding="utf-8")

    assert (
        'if (!this._radarLayer) {\n      this._showMapStatus("Loading radar layer");'
        in card_text
    )
    assert "const layerLoaded = this._waitForLayerLoad(nextLayer)" in card_text
    assert "nextLayer.addTo(this._map)" in card_text
    assert "await this._withTimeout(" in card_text
    assert 'nextLayer.once("load", fadeIn)' not in card_text
    assert "requestAnimationFrame(fadeIn)" not in card_text

    layer_load_index = card_text.index(
        "const layerLoaded = this._waitForLayerLoad(nextLayer)"
    )
    fade_out_index = card_text.index("previousLayer?.setOpacity(0)")
    assert layer_load_index < fade_out_index


def test_bundled_leaflet_assets_exist() -> None:
    """Test Leaflet runtime assets are bundled locally."""
    asset_root = Path(frontend._card_file_path()).parent / "vendor" / "leaflet"

    assert (asset_root / "leaflet.css").is_file()
    assert (asset_root / "leaflet.js").is_file()
    assert (asset_root / "leaflet-src.esm.js").is_file()
