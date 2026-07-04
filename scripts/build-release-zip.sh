#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
rm -f custom_components/rain_radar.zip
cd custom_components/rain_radar
zip -r ../rain_radar.zip . \
  -x 'tests/*' \
  -x 'tests/**' \
  -x '__pycache__/*' \
  -x '*/__pycache__/*' \
  -x '*.pyc'
