#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
rm -f custom_components/rain_radar.zip
cd custom_components
zip -r rain_radar.zip rain_radar \
  -x 'rain_radar/tests/*' \
  -x 'rain_radar/tests/**' \
  -x '*/__pycache__/*' \
  -x '*.pyc'

