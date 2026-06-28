#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HA_CONFIG_DIR="${HA_CONFIG_DIR:-/Volumes/config}"

mkdir -p "${HA_CONFIG_DIR}/custom_components/rain_radar"
rsync -a --delete --inplace \
  "${REPO_DIR}/custom_components/rain_radar/" \
  "${HA_CONFIG_DIR}/custom_components/rain_radar/"

echo "Synced Rain Radar to ${HA_CONFIG_DIR}/custom_components/rain_radar"
