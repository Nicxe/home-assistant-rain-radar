#!/usr/bin/env bash
set -euo pipefail

"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sync-to-ha-dev.sh"

cat <<'MESSAGE'
Rain Radar is installed in the Home Assistant dev config directory.
Restart Home Assistant, add the integration through Settings > Devices & services,
then reload the browser once if the card is not visible immediately.
MESSAGE

