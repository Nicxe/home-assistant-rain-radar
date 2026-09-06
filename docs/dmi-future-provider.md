# DMI provider

DMI forecasts are implemented using the documented HARMONIE DINI Open Data EDR endpoint. Radar imagery for Denmark is separately supplied through Regnradar.

The provider derives mean precipitation intensity from valid accumulated-precipitation interval boundaries. Missing, decreasing or ambiguous boundaries remain unknown. Rain risk reports 100 when the selected rain threshold is reached within the forecast horizon, and 0 only when the available interval coverage supports a dry answer. This is a threshold result, not meteorological probability.

A shared request manager caches model data, coalesces requests and applies bounded stale handling, Retry-After, exponential backoff and jitter. A busy service may return HTTP 429; the card shows forecast availability separately from radar availability. Repeated manual reloads are unnecessary and can defeat useful runtime caching.

The filename is retained so existing documentation links continue to work.

DMI does not currently expose a verified model-production timestamp in the normalized response. Forecast data age therefore remains unknown; the separately shown fetch time remains available. The combined data-age sensor uses an actual radar timestamp when available instead of treating a newly fetched DMI model as newly produced weather data.
