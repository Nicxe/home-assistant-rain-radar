# Migration From MET Rain Risk

Rain Radar includes the useful MET Rain Risk behavior in the new `rain_radar` integration domain.

The old integration used `met_rain_risk` and created a rain-risk sensor for the next 12 hours. Rain Radar creates `sensor.<name>_rain_risk_12h` with the same user-facing value: the maximum precipitation probability in the configured horizon, defaulting to 12 hours.

Entity IDs change because this is a new integration. Update automations and dashboards manually after adding Rain Radar through the Home Assistant UI.

Example old automation reference:

```yaml
entity_id: sensor.met_rain_risk_rain_risk_next_12h
```

Example new automation reference:

```yaml
entity_id: sensor.home_rain_risk_12h
```

The new sensor keeps bounded hourly details with probability, precipitation amount, symbol code, and time. It does not store raw MET Norway payloads in entity attributes.

After migrating, remove the old MET Rain Risk integration only when your automations and dashboards have been updated.

