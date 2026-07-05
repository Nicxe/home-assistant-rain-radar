# SMHI Provider

SMHI is implemented as a forecast provider for Swedish point forecasts. Rain Radar uses SMHI Open Data SNOW1G for precipitation amount, precipitation probability, and weather symbol data.

Current behavior:

- Users can choose SMHI as the forecast provider in the config flow or options flow.
- SMHI forecast data drives rain now, rain soon, rain arrival, and rain risk entities.
- The Sweden radar source uses SMHI radar frames through Regnradar. Rain Radar does not fetch SMHI radar raster files directly.

Current limits:

- SMHI is forecast-only inside the provider abstraction.
- Radar image fetching still goes through Regnradar.
- Coordinates outside SMHI forecast coverage are reported as outside coverage instead of failing the whole integration.

Future direct SMHI radar work would still need a separate design for projection handling, raster/scientific data parsing, Home Assistant dependency constraints, and efficient dashboard rendering.
