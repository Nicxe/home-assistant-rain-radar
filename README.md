# Rain Radar

[![GitHub release](https://img.shields.io/github/v/release/Nicxe/home-assistant-rain-radar)](https://github.com/Nicxe/home-assistant-rain-radar/releases)
[![Downloads](https://img.shields.io/github/downloads/Nicxe/home-assistant-rain-radar/total)](https://github.com/Nicxe/home-assistant-rain-radar/releases)
[![Support](https://img.shields.io/badge/support-GitHub%20Issues-blue)](https://github.com/Nicxe/home-assistant-rain-radar/issues)

## Overview

Rain Radar is a custom Home Assistant integration for Nordic rain monitoring. The first release uses MET Norway data and includes both native Home Assistant entities for automations and a bundled Lovelace card for dashboards.

## Features

- See whether it is raining at your configured location.
- See whether rain is expected soon inside your configured forecast window.
- Get an estimated number of minutes until rain arrives.
- Track current precipitation intensity in mm/h.
- Track data freshness and active provider attribution.
- Use `sensor.<name>_rain_risk_12h` for the highest precipitation probability in the next 12 hours, migrated from MET Rain Risk.
- Add the bundled `rain-radar-card` to a dashboard without installing a separate card repository.

## Data Sources And Attribution

The first implementation uses open data from [MET Norway](https://api.met.no/). Rain Radar sends an identifying User-Agent and the contact string you configure during setup, as requested by MET Norway.

SMHI and DMI are documented as future provider options, but they are not implemented in the first release. Regnradar.se is not used as a data source.

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

Download `rain_radar.zip` from the latest GitHub release. Extract it and place the `rain_radar` folder in `config/custom_components/`, then restart Home Assistant.

## Configuration

Set up Rain Radar through Settings > Devices & services > Add Integration > Rain Radar.

[![Open your Home Assistant instance and start configuring Rain Radar](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start?domain=rain_radar)

Configuration fields:

- Location name, used for entity names.
- Latitude and longitude, defaulting to your Home Assistant location.
- Provider, currently MET Norway.
- Contact for MET Norway, required for API identification.
- Rain threshold in mm/h.
- Rain soon forecast window in minutes.
- Sampling radius, reserved for provider strategies.
- Rain risk horizon, defaulting to 12 hours for MET Rain Risk compatibility.

## Entities

Rain Radar creates these entities for each configured location:

- `binary_sensor.<name>_raining_now`: on when current precipitation is at or above the threshold.
- `binary_sensor.<name>_rain_soon`: on when rain is expected inside the configured forecast window.
- `binary_sensor.<name>_radar_coverage`: on when radar coverage is available.
- `sensor.<name>_precipitation_now`: current precipitation intensity in mm/h.
- `sensor.<name>_rain_risk_12h`: highest precipitation probability in the next 12 hours.
- `sensor.<name>_rain_arrival`: minutes until rain is expected, if known.
- `sensor.<name>_data_age`: age of the newest available data.
- `sensor.<name>_provider`: active provider name.
- `sensor.<name>_latest_radar_time`: timestamp of the latest radar frame when available.

The rain-risk sensor includes bounded hourly details with time, probability, precipitation amount, and symbol code. Raw provider payloads are not stored as entity attributes.

## Dashboard Card

The `rain-radar-card` is bundled with the integration. On startup, Rain Radar syncs the card to `config/www/rain-radar-card.js` and registers a Lovelace resource like `/local/rain-radar-card.js?v=...` as a JavaScript module.

After install or update, reload your browser once if the card is not visible immediately.

## Card Usage

Add the card through the dashboard UI editor and choose `Rain Radar Card`, or use manual YAML:

```yaml
type: custom:rain-radar-card
entity: binary_sensor.home_rain_soon
```

Optional card settings:

```yaml
type: custom:rain-radar-card
entity: binary_sensor.home_rain_soon
show_timeline: true
show_status_strip: true
height: 360
```

## Manual Card Fallback

Manual Lovelace resource setup should only be needed for troubleshooting:

- URL: `/local/rain-radar-card.js`
- Type: `JavaScript Module`

Do not edit Home Assistant `.storage` files directly.

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

## Migration From MET Rain Risk

The MET Rain Risk functionality is included in Rain Radar. The old integration domain was `met_rain_risk`; the new domain is `rain_radar`, so entity IDs change and automations or dashboards must be updated manually.

Typical replacement:

```yaml
entity_id: sensor.home_rain_risk_12h
```

If your old entity had a different location name, use the new Rain Radar entity ID shown in Home Assistant after setup.

## Troubleshooting

If the card does not appear, restart Home Assistant once and reload the browser. Confirm that the Lovelace resource exists as `/local/rain-radar-card.js?v=...` and is registered as a JavaScript module.

If entities are unavailable, check that your location is inside provider coverage and that the MET Norway contact field contains an email address or website URL. Provider rate limits and temporary MET outages are surfaced as unavailable or stale data instead of hidden values.

Enable debug logging for `custom_components.rain_radar` if you need more detail while reporting an issue.

## Screenshots

Screenshot placeholders will be replaced after the first public release is validated in a real dashboard.

## Release Assets And Versioning

Each release publishes `rain_radar.zip`. The bundled dashboard card is included inside the integration zip, and the integration and card share one version.

## Planned Provider Improvements

SMHI is planned as a future provider for stronger Sweden-specific radar support. DMI is planned as a future provider for Denmark and nearby southern Nordic use cases. Both require separate provider research and are intentionally not included in the first MET Norway release.

## Contributing

Use GitHub issues for bug reports, feature requests, and support questions. Pull requests should include relevant tests and should keep user-facing text in English.

