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


def test_bundled_card_registers_custom_card() -> None:
    """Test card custom element registration strings."""
    card_text = Path(frontend._card_file_path()).read_text(encoding="utf-8")

    assert "customElements.define(CARD_TYPE, RainRadarCard)" in card_text
    assert "window.customCards.push" in card_text
    assert "type: CARD_TYPE" in card_text


def test_bundled_card_has_no_remote_cdn_imports() -> None:
    """Test card does not import remote JavaScript."""
    card_text = Path(frontend._card_file_path()).read_text(encoding="utf-8")

    assert "import " not in card_text
    assert "unpkg.com" not in card_text
    assert "cdn.jsdelivr.net" not in card_text
