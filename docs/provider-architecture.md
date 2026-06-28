# Provider Architecture

Rain Radar uses a provider abstraction so future radar sources can be added without changing entity behavior.

The coordinator asks the active provider for three normalized data groups:

- Point precipitation forecast for rain-now, rain-soon, and rain-arrival entities.
- Rain-risk forecast for `sensor.<name>_rain_risk_12h`.
- Radar frame metadata for the bundled dashboard card.

Entities read normalized models only. They do not parse raw provider JSON.

The first provider is MET Norway. SMHI and DMI are planned future providers and should be added as separate provider modules after the MET Norway first release is stable.

Provider implementations must:

- Use async I/O through Home Assistant's HTTP client session.
- Send provider-required identification headers.
- Respect cache headers and conditional requests where supported.
- Return clear coverage and stale-data states.
- Avoid exposing raw provider payloads in entity attributes or diagnostics.
- Avoid unauthenticated endpoints.

