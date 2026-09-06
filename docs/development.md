# Development

Rain Radar is developed as one repository containing both the Home Assistant integration and the bundled Lovelace card.

Local validation:

```bash
./scripts/run_ruff.sh
./scripts/run_tests.sh
node --check custom_components/rain_radar/www/rain-radar-card.js
node --test custom_components/rain_radar/tests/frontend/*.test.cjs
```

For repository-only development, install into a Home Assistant dev config:

```bash
./scripts/sync-to-ha-dev.sh
```

The script defaults to `/Volumes/config` because that is the local dev-instance path used in this workspace. Set `HA_CONFIG_DIR=/config` if your environment exposes the Home Assistant config directory there.

The card source of truth is `custom_components/rain_radar/www/rain-radar-card.js`. The integration syncs it into `config/www/rain-radar-card.js` on startup and registers the Lovelace module resource.

Do not edit Home Assistant `.storage` files directly.


For the local development workflow used here, author changes in `/Volumes/config/custom_components/rain_radar`, synchronize the bundled card immediately to `/Volumes/config/www/rain-radar-card.js`, and then copy the validated integration tree to this repository. Inspect initial differences before copying. Do not run the repository-to-HA script over newer development work.

The declared minimum is Home Assistant 2026.3.0, the first release using Python 3.14. Verify against that version in an isolated environment as well as the running development instance when changing compatibility requirements. The system Python test installation may contain an older Home Assistant version and alone does not verify this minimum.
