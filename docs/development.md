# Development

Rain Radar is developed as one repository containing both the Home Assistant integration and the bundled Lovelace card.

Local validation:

```bash
./scripts/run_ruff.sh
./scripts/run_tests.sh
node --experimental-default-type=module --check custom_components/rain_radar/www/rain-radar-card.js
```

Install into the local Home Assistant dev config:

```bash
./scripts/sync-to-ha-dev.sh
```

The script defaults to `/Volumes/config` because that is the local dev-instance path used in this workspace. Set `HA_CONFIG_DIR=/config` if your environment exposes the Home Assistant config directory there.

The card source of truth is `custom_components/rain_radar/www/rain-radar-card.js`. The integration syncs it into `config/www/rain-radar-card.js` on startup and registers the Lovelace module resource.

Do not edit Home Assistant `.storage` files directly.

