# Rain Radar


[![Buy me a Coffee](https://img.shields.io/badge/Support-Buy%20me%20a%20coffee-fdd734?logo=buy-me-a-coffee)](https://www.buymeacoffee.com/NiklasV) 

[![Last commit](https://img.shields.io/github/last-commit/Nicxe/home-assistant-rain-radar)](#) [![Version](https://img.shields.io/github/v/release/Nicxe/home-assistant-rain-radar)](#) ![GitHub Downloads (all assets, latest release)](https://img.shields.io/github/downloads/nicxe/home-assistant-rain-radar/latest/total)


Rain Radar is a custom Home Assistant integration for Nordic rain monitoring. It combines Regnradar radar imagery with MET Norway point forecasts so dashboards and automations can answer practical questions such as whether it is raining now, whether rain is expected soon, and how high the rain risk is in the next hours.

<img width="530" height="648" alt="CleanShot 2026-07-04 at 22 34 34" src="https://github.com/user-attachments/assets/5b2c69a7-7c76-49da-8550-0c17a5ca82ce" />


The integration includes Home Assistant entities, a config flow, options flow, diagnostics, localized Home Assistant setup text, authenticated radar image endpoints, and a bundled Lovelace dashboard card.

## Features

- Monitor current rain intensity at a configured location.
- Detect when rain is expected inside a configurable look-ahead window.
- Estimate how many minutes remain until rain arrives.
- Track the highest forecast rain probability for the configured horizon.
- Show radar frames, coverage, forecast markers, and location context in the bundled dashboard card.
- Use radar coverage, data age, provider status, and latest radar time as diagnostic signals.
- Configure one or more locations through the Home Assistant UI.
- Keep the card bundled with the integration, with no separate frontend repository required.

## Data Sources

Radar imagery comes from [Regnradar/Vackertväder](https://regnradar.se/). Forecast-based entities currently use open data from [MET Norway](https://api.met.no/).

Regnradar is used for the radar map overlay. MET Norway is currently the only selectable forecast provider for rain arrival, rain soon, precipitation, and rain risk calculations. SMHI and DMI forecast providers are planned for future releases but are not active yet.

## Installation

### HACS Custom Repository

Rain Radar is not a HACS default repository yet. Install it as a custom repository:

1. Open HACS in Home Assistant.
2. Open the three-dot menu.
3. Select Custom repositories.
4. Add `https://github.com/Nicxe/home-assistant-rain-radar`.
5. Select category `Integration`.
6. Install Rain Radar.
7. Restart Home Assistant.

[![Open your Home Assistant instance and open the HACS custom repository flow](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Nicxe&repository=home-assistant-rain-radar&category=integration)

### Manual Installation

Download `rain_radar.zip` from the latest GitHub release. Extract the archive and place the `rain_radar` folder in `config/custom_components/`, then restart Home Assistant.

## Configuration

Set up Rain Radar through Settings > Devices & services > Add Integration > Rain Radar.

[![Open your Home Assistant instance and start configuring Rain Radar](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=rain_radar)

| Field | Default | Notes |
| --- | --- | --- |
| Location name | `Home` | Used as the base name for the created entities. |
| Latitude | Home Assistant latitude | Must be between `-90` and `90`. |
| Longitude | Home Assistant longitude | Must be between `-180` and `180`. |
| Forecast provider | MET Norway | Used for rain arrival, rain soon, precipitation, and rain risk. |
| Radar area | Nordic | Regnradar area used for radar frames and map coverage. |
| Rain threshold | `0.1` mm/h | Minimum intensity that counts as rain. |
| Rain soon window | `60` minutes | Look-ahead window for the rain soon binary sensor. |
| Sampling radius | `1000` meters | Reserved for provider strategies. MET Norway currently uses point forecasts. |
| Rain risk horizon | `12` hours | Forecast horizon inspected by the rain risk sensor. |

The same values can be changed later from the integration options. Setup and options text is available in English, Swedish, Finnish, Norwegian Bokmål, and Danish.

## Entities

Rain Radar creates these entities for each configured location. Entity IDs use the configured location name by default, but Home Assistant may adjust them to avoid duplicates.

| Entity | Type | State | Main attributes |
| --- | --- | --- | --- |
| `binary_sensor.<name>_raining_now` | Binary sensor | `on` when current precipitation is at or above the threshold | Attribution, entry ID, stale status |
| `binary_sensor.<name>_rain_soon` | Binary sensor | `on` when rain is expected inside the configured window | Attribution, entry ID, stale status |
| `binary_sensor.<name>_radar_coverage` | Binary sensor | `on` when radar coverage is available | Attribution, entry ID, stale status |
| `sensor.<name>_precipitation_now` | Sensor | Current precipitation intensity in mm/h | Forecast samples, threshold, window, stale status |
| `sensor.<name>_rain_risk_12h` | Sensor | Highest precipitation probability in percent | Hourly probability, precipitation amount, symbol code, update status |
| `sensor.<name>_rain_arrival` | Sensor | Minutes until expected rain, when known | Attribution and entry ID |
| `sensor.<name>_data_age` | Sensor | Age of the newest available data in minutes | Attribution and entry ID |
| `sensor.<name>_provider` | Sensor | Active provider name | Provider IDs, status, coverage status, attribution |
| `sensor.<name>_latest_radar_time` | Sensor | Timestamp of the latest radar frame | Attribution and entry ID |

Raw provider payloads are not stored as entity attributes. Forecast details are bounded to the values needed by dashboards and automations.

## Dashboard Card

The `rain-radar-card` is bundled with the integration. On startup, Rain Radar copies the bundled frontend assets to `config/www`, registers `/local/rain-radar-card.js?v=...` as a Lovelace JavaScript module, and exposes authenticated radar frame endpoints for the card.

After installing or updating the integration, reload your browser once if the card is not visible immediately.

Add the card through the dashboard UI editor and choose `Rain Radar Card`. The editor includes an entity selector, so select one of the Rain Radar entities for the location you want to show. `binary_sensor.<name>_rain_soon` is a good default because the card can find companion Rain Radar entities from the same integration entry.

Minimal YAML:

```yaml
type: custom:rain-radar-card
entity: binary_sensor.home_rain_soon
```

Common card options:

```yaml
type: custom:rain-radar-card
entity: binary_sensor.home_rain_soon
title: Rain risk
show_icon: true
show_status: true
show_precipitation: true
show_arrival: true
show_risk: true
show_latest_radar: true
show_provider: true
show_coverage: true
show_timeline: true
show_map: true
show_status_strip: true
show_location_marker: true
show_forecast: true
map_zoom_controls: true
map_scroll_wheel: false
forecast_minutes: 60
arrival_format: auto
height: 420
```

The card supports an expandable details section. Summary rows can stay visible, individual metadata rows can be enabled or disabled, and the map can be hidden when only the summary is needed.

The metadata row order can be customized with `meta_order`. Use `divider` to separate always-visible summary rows from rows that belong under the expandable details section.

```yaml
type: custom:rain-radar-card
entity: binary_sensor.home_rain_soon
meta_order:
  - status
  - arrival
  - precipitation
  - risk
  - divider
  - latest_radar
  - coverage
  - provider
  - forecast
  - timeline
  - map
```

Manual Lovelace resource setup should only be needed for troubleshooting. If you add it manually, use URL `/local/rain-radar-card.js` and type `JavaScript Module`. Do not edit Home Assistant `.storage` files directly.

## Automation Examples

Rain soon notification:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.home_rain_soon
    to: "on"
actions:
  - action: notify.mobile_app_phone
    data:
      message: "Rain is expected soon."
mode: single
```

Rain intensity threshold:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.home_precipitation_now
    above: 0.5
actions:
  - action: notify.mobile_app_phone
    data:
      message: "Rain intensity is increasing."
mode: single
```

Rain risk threshold:

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.home_rain_risk_12h
    above: 70
actions:
  - action: notify.mobile_app_phone
    data:
      message: "There is a high chance of rain in the next 12 hours."
mode: single
```



## Contributing

Use GitHub issues for bug reports, feature requests, and support questions. Pull requests should include relevant tests and keep user-facing text in English.
