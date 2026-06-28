#!/usr/bin/env bash
set -euo pipefail

./scripts/run_ruff.sh
./scripts/run_tests.sh custom_components/rain_radar/tests
node --experimental-default-type=module --check custom_components/rain_radar/www/rain-radar-card.js

