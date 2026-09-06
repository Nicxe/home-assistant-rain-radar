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

Forecast providers are separate from radar imagery. MET Norway, SMHI and DMI are implemented forecast providers. DMI uses HARMONIE DINI precipitation intervals; its rain-risk value indicates whether the selected intensity threshold is reached, rather than a meteorological probability. See [DMI provider](dmi-future-provider.md).

Provider implementations must:

- Use async I/O through Home Assistant's HTTP client session.
- Send provider-required identification headers.
- Respect cache headers and conditional requests where supported.
- Return clear coverage and stale-data states.
- Avoid exposing raw provider payloads in entity attributes or diagnostics.
- Use documented public endpoints; keyless DMI responses must not trigger credential reauthentication.

## Source quality and delivery

Precipitation samples and probability periods preserve interval boundaries, source timestamps, data kind, resolution, window completeness and reason codes. MET and SMHI share interval validity rules. Missing values remain unknown; a negative rain answer requires complete coverage of the requested window. Weather-data validity is checked independently of HTTP cache expiry.

Radar and forecast delivery have separate status objects. Each exposes status, reason, last successful response, last attempt, next retry and source-data age. Healthy radar can initialize and continue while forecasts fail. Radar health considers frame count and frame age independently of geographic coverage.

The HTTP client reuses fresh responses, coalesces concurrent identical requests, and shares failures during backoff. Regnradar area metadata is shared between configured locations. Timestamped images are cached as immutable within bounded caches. DMI retains its model-cycle-aware request manager and Retry-After handling.
