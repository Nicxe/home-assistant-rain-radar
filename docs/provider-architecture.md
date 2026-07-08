# Provider Architecture

Rain Radar uses a provider abstraction so forecast providers and radar frame sources can evolve without changing entity behavior.

The coordinator asks the active provider for three normalized data groups:

- Point precipitation forecast for rain-now, rain-soon, and rain-arrival entities.
- Rain-risk forecast for `sensor.<name>_rain_risk_12h`.
- Radar frame metadata for the bundled dashboard card.

Entities read normalized models only. They do not parse raw provider JSON.

Radar frame metadata is fetched through Regnradar. The configured radar source selects the Regnradar area:

- `nordic`: MET radar frames through Regnradar, including forecast frames when Regnradar exposes them.
- `sweden`: SMHI radar frames through Regnradar.
- `denmark`: DMI radar frames through Regnradar.

Forecast providers are separate from radar imagery. MET Norway and SMHI are implemented forecast providers for rain-now, rain-soon, rain-arrival, and rain-risk calculations. DMI remains future work for Denmark-specific forecast data.

Provider implementations must:

- Use async I/O through Home Assistant's HTTP client session.
- Send provider-required identification headers.
- Respect cache headers and conditional requests where supported.
- Return clear coverage and stale-data states.
- Avoid exposing raw provider payloads in entity attributes or diagnostics.
- Avoid unauthenticated endpoints.
