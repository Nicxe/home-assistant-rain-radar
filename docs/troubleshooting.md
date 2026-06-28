# Troubleshooting

## Card Does Not Load

Restart Home Assistant and reload the browser once. Rain Radar registers `/local/rain-radar-card.js?v=...` as a JavaScript module automatically.

If the resource is missing, confirm that the integration is loaded and that `rain-radar-card.js` exists in `config/www/`.

## Entities Are Unavailable

Check that the configured location is inside MET Norway coverage and that the contact field is a valid email address or website URL.

Rain Radar marks data unavailable or stale when provider data cannot be trusted. This avoids misleading automations.

## Rate Limits Or Provider Errors

Rain Radar sends a descriptive User-Agent and uses cache headers and conditional requests. If MET Norway returns rate limits or temporary errors, wait for the next scheduled update.

## Debug Logging

Enable debug logging for `custom_components.rain_radar` when collecting details for a GitHub issue.

Do not edit Home Assistant `.storage` files directly.

