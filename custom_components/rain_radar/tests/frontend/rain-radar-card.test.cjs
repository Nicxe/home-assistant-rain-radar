const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

// Always exercise the bundled integration asset: CI has no /Volumes/config/www.
const source = readFileSync(join(__dirname, '../../www/rain-radar-card.js'), 'utf8');

function element() {
  const children = new Map();
  return {
    innerHTML: '', textContent: '', hidden: false, style: {}, dataset: {}, attributes: {},
    classList: { toggle() {}, add() {}, remove() {} },
    querySelector(selector) { if (!children.has(selector)) children.set(selector, element()); return children.get(selector); },
    querySelectorAll() { return []; },
    setAttribute(key, value) { this.attributes[key] = value; },
    addEventListener() {}, remove() {},
  };
}

function environment() {
  let now = Date.parse('2026-09-06T08:30:00Z');
  let timerId = 0;
  const timers = new Map();
  const registry = new Map();
  class FakeDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const scope = {
    HTMLElement: class { attachShadow() { this.shadowRoot = element(); } dispatchEvent() {} },
    Date: FakeDate, Intl, URL, Event, console,
    customElements: { get: (key) => registry.get(key), define: (key, value) => registry.set(key, value) },
    window: { customCards: [], setTimeout(callback, delay) { timers.set(++timerId, { callback, delay }); return timerId; }, clearTimeout(id) { timers.delete(id); }, setInterval() { return 1; }, clearInterval() {} },
    matchMedia: () => ({ matches: false }),
  };
  vm.createContext(scope);
  vm.runInContext(`${source}\nglobalThis.helpers = { forecastSamples, availableForecastMinutes, arrivalMinutesFromForecastSamples, forecastSummary, numberText, localTime, locationFromPayload };`, scope);
  const Card = registry.get('rain-radar-card');
  const card = new Card();
  card._config = card._normalizeConfig({ entity: 'binary_sensor.a_rain_soon' });
  card._ensureLayout = () => {};
  card._update = () => {};
  return { card, Card, Editor: registry.get('rain-radar-card-editor'), scope, timers, helpers: scope.helpers, advance: (ms) => { now += ms; } };
}

function state(entry, key, value = 'off', attributes = {}) {
  const domain = ['rain_soon', 'radar_coverage'].includes(key) ? 'binary_sensor' : 'sensor';
  const entity_id = `${domain}.${entry}_${key}`;
  return { entity_id, state: value, attributes: { rain_radar_entry_id: entry, rain_radar_entity_key: key, ...attributes } };
}
function hass(api, entry = 'a', time = '2026-09-06T08:20:00Z') {
  const states = [state(entry, 'rain_soon'), state(entry, 'radar_coverage', 'on'), state(entry, 'latest_radar_time', time), state(entry, 'precipitation_now', '0'), state(entry, 'provider', 'DMI')];
  return { states: Object.fromEntries(states.map((item) => [item.entity_id, item])), callApi: api, connected: true, locale: { language: 'sv', time_format: '24', number_format: 'decimal_comma' } };
}
function payload(id = '1') {
  return { frames: [{ id, type: 'obs', time: '2026-09-06T08:20:00Z', image_url: `/signed/${id}` }, { id: 'future', type: 'fcst', time: '2026-09-06T08:40:00Z', image_url: '/signed/future' }], location: { latitude: 57, longitude: 12 } };
}
const settle = async () => { for (let index = 0; index < 8; index++) await Promise.resolve(); };

test('new radar time refreshes an unchanged entry and ordinary HA updates do not duplicate requests', async () => {
  const { card } = environment(); let calls = 0;
  const api = async () => payload(String(++calls));
  card.hass = hass(api); await settle();
  assert.equal(calls, 1);
  assert.equal(card._activeFrame, 0, 'start at latest observation, not forecast');
  card.hass = hass(api); await settle(); assert.equal(calls, 1);
  card.hass = hass(api, 'a', '2026-09-06T08:25:00Z'); await settle();
  assert.equal(calls, 2); assert.equal(card._frames[0].id, '2');
});

test('initial error recovers with bounded timer retry for the same entry', async () => {
  const { card, timers, advance } = environment(); let calls = 0;
  card.hass = hass(async () => { if (++calls === 1) throw Error('temporary'); return payload(); });
  await settle(); assert.equal(card._frames.length, 0);
  card.hass = { ...card._hass }; await settle(); assert.equal(calls, 1);
  const timer = [...timers.values()].at(-1); assert.equal(timer.delay, 15000);
  advance(timer.delay); timer.callback(); await settle();
  assert.equal(calls, 2); assert.equal(card._frames.length, 2);
});

test('repeated errors back off at most once per interval, capped at five minutes', async () => {
  const { card, timers, advance } = environment(); let calls = 0;
  card.hass = hass(async () => { calls++; throw Error('offline'); }); await settle();
  for (let count = 0; count < 8; count++) {
    const timer = [...timers.values()].at(-1);
    assert.ok(timer.delay >= 15000 && timer.delay <= 300000);
    advance(timer.delay); timer.callback(); await settle();
  }
  assert.equal(calls, 9); assert.equal([...timers.values()].at(-1).delay, 300000);
});

test('late responses from an old location cannot replace the selected location', async () => {
  const { card } = environment(); let release;
  card.hass = hass(() => new Promise((resolve) => { release = resolve; }));
  card._config.entity = 'binary_sensor.b_rain_soon';
  card.hass = hass(async () => payload('b'), 'b'); await settle();
  release(payload('a')); await settle();
  assert.equal(card._frameEntryId, 'b'); assert.equal(card._frames[0].id, 'b');
});

test('updates during a request trigger one follow-up for the latest version', async () => {
  const { card } = environment(); let release; let calls = 0;
  const api = () => ++calls === 1 ? new Promise((resolve) => { release = resolve; }) : Promise.resolve(payload('latest'));
  card.hass = hass(api); card.hass = hass(api, 'a', '2026-09-06T08:25:00Z');
  assert.equal(calls, 1); release(payload('old')); await settle();
  assert.equal(calls, 2); assert.equal(card._frames[0].id, 'latest');
});

test('signed URLs renew before expiry without another HA update', async () => {
  const { card, timers, advance } = environment(); let calls = 0;
  card.hass = hass(async () => ({ ...payload(String(++calls)), links_expire_at: '2026-09-06T08:40:00Z' })); await settle();
  const timer = [...timers.values()].at(-1); assert.equal(timer.delay, 9 * 60000);
  advance(timer.delay); timer.callback(); await settle();
  assert.equal(calls, 2); assert.equal(card._frames[0].image_url, '/signed/2');
});

test('disconnect cancels timers and pending results; reconnect fetches fresh metadata', async () => {
  const { card, timers } = environment(); let release;
  card.hass = hass(() => new Promise((resolve) => { release = resolve; }));
  card.disconnectedCallback(); release(payload('old')); await settle();
  assert.equal(card._frames.length, 0); assert.equal(timers.size, 0);
  card._hass.callApi = async () => payload('new'); card.connectedCallback(); await settle();
  assert.equal(card._frames[0].id, 'new'); assert.equal(timers.size, 1);
  card.disconnectedCallback(); assert.equal(timers.size, 0);
});

test('websocket reconnect refreshes the same entry', async () => {
  const { card } = environment(); let calls = 0; const api = async () => payload(String(++calls));
  card.hass = hass(api); await settle();
  card.hass = { ...hass(api), connected: false }; card.hass = hass(api); await settle();
  assert.equal(calls, 2);
});

test('radar coverage and numeric entity selections use the stable rain_soon companion', async () => {
  for (const selected of ['binary_sensor.a_radar_coverage', 'sensor.a_precipitation_now']) {
    const { card } = environment(); card._config.entity = selected; card.hass = hass(async () => payload()); await settle();
    assert.equal(card._context().main.entity_id, 'binary_sensor.a_rain_soon'); assert.equal(card._context().rainingSoon, false);
  }
});

test('missing point samples and image-only forecast never become zero/no rain', () => {
  const { helpers, card } = environment();
  const time = '2026-09-06T08:35:00Z';
  assert.equal(helpers.forecastSamples({ attributes: { forecast_samples: [{ time, precipitation_rate: null }] } }, 60).length, 0);
  assert.equal(helpers.forecastSamples({ attributes: { forecast_samples: [{ time, precipitation_rate: 0 }] } }, 60)[0].rate, 0);
  assert.equal(helpers.forecastSummary({ kind: 'forecast', imageUrl: '/image', rate: null }, 0.1), 'Radar forecast image');
  assert.equal(card._forecastSummary({ kind: 'forecast', imageUrl: '/image', rate: null }, 0.1), 'Radar forecast');
  assert.equal(helpers.locationFromPayload({ latitude: null, longitude: null }), null);
});

test('formatting follows HA language, time and number preferences including midnight dates', () => {
  const { helpers } = environment(); const time = '2026-09-06T13:05:00Z';
  assert.match(helpers.localTime(time, { language: 'sv', time_format: '12' }), /AM|PM|fm|em/i);
  assert.doesNotMatch(helpers.localTime(time, { language: 'en', time_format: '24' }), /AM|PM/i);
  assert.equal(helpers.numberText(1.2, ' mm/h', { language: 'en', number_format: 'decimal_comma' }), '1,2 mm/h');
  assert.equal(helpers.numberText(1.2, ' mm/h', { language: 'sv', number_format: 'comma_decimal' }), '1.2 mm/h');
  assert.equal(helpers.numberText(1234.5, '', { language: 'sv', number_format: 'none' }), '1234.5');
  assert.match(helpers.localTime('2026-09-07T00:05:00Z', { language: 'sv', time_format: '24' }, true), /sep/);
});

test('source health distinguishes fresh radar and forecast failure without hiding imagery', async () => {
  const { card, Card } = environment();
  const current = hass(async () => payload());
  current.states['binary_sensor.a_rain_soon'].state = 'unavailable';
  current.states['sensor.a_precipitation_now'].state = 'unavailable';
  current.states['binary_sensor.a_rain_soon'].attributes.forecast_status = { status: 'temporarily_unavailable', reason: 'server_busy', next_retry: '2026-09-06T09:00:00Z' };
  current.states['binary_sensor.a_rain_soon'].attributes.radar_status = { status: 'ok', data_age_seconds: 600 };
  card.hass = current; await settle();
  Card.prototype._syncSourceStatus.call(card, card._context(), 'DMI');
  assert.match(card.shadowRoot.querySelector('.radar-health').textContent, /Tillgänglig/);
  assert.match(card.shadowRoot.querySelector('.forecast-health').textContent, /Tillfälligt otillgänglig.*upptagen.*Nästa försök/);
  assert.equal(card._context().isUnavailable, false);
});

test('hourly forecast arrival exposes an approximate interval', () => {
  const { card } = environment(); card._hass = hass(async () => payload());
  assert.equal(card._arrival(25, 60), 'Cirka 0–1 h');
  assert.equal(card._arrival(85, 60), 'Cirka 1–2 h');
  assert.equal(card._arrival(null, 60), 'Okänt');
});

test('editor retains its form node and focus on ordinary HA updates', () => {
  const { Editor } = environment(); const editor = new Editor();
  const form = editor.shadowRoot.querySelector('.editor-form'); form.focused = true;
  editor._hass = hass(async () => payload());
  let renders = 0; editor._render = () => { renders++; };
  editor.hass = hass(async () => payload()); editor.hass = hass(async () => payload());
  assert.equal(editor.shadowRoot.querySelector('.editor-form'), form);
  assert.equal(form.focused, true); assert.equal(renders, 0);
  editor._formValueChanged({ detail: { value: { title: 'A changed title' } } });
  assert.equal(renders, 0); assert.equal(editor._config.title, 'A changed title');
});

test('reduced motion suppresses autoplay and decorative marker is not keyboard interactive', async () => {
  const { card, scope, Card } = environment();
  scope.matchMedia = () => ({ matches: true }); card._config.default_animation_mode = 'playing';
  card.hass = hass(async () => payload()); await settle(); assert.equal(card._playing, false);
  let markerOptions;
  const L = { divIcon: (value) => value, marker: (_, options) => { markerOptions = options; return { addTo() { return this; } }; } };
  Card.prototype._syncLeafletMarker.call(card, L, { location: { latitude: 57, longitude: 12 } });
  assert.equal(markerOptions.keyboard, false); assert.equal(markerOptions.alt, 'Vald plats');
});

test('rendered status keeps unknown/stale/outside coverage distinct from dry and leaves healthy radar visible', async () => {
  for (const quality of ['unknown', 'stale', 'outside_coverage', 'temporarily_unavailable']) {
    const { card, Card } = environment(); const values = {};
    const current = hass(async () => payload());
    current.states['binary_sensor.a_rain_soon'].attributes.forecast_status = { status: quality };
    if (quality === 'stale') {
      current.states['sensor.a_precipitation_now'].attributes.is_stale = true;
    } else {
      current.states['binary_sensor.a_rain_soon'].state = quality === 'temporarily_unavailable' ? 'unavailable' : 'unknown';
      current.states['sensor.a_precipitation_now'].state = quality === 'temporarily_unavailable' ? 'unavailable' : 'unknown';
    }
    card.hass = current; await settle();
    card._layoutRendered = true; card._setMetaValue = (key, value) => { values[key] = value; };
    Card.prototype._update.call(card);
    assert.equal(values.status, 'Regnstatus okänd', quality);
    assert.equal(values.precipitation, 'Okänt', quality);
    assert.equal(card.shadowRoot.querySelector('.message').hidden, true);
    assert.equal(card.shadowRoot.querySelector('.info-status').textContent, 'Regnstatus okänd');
    assert.doesNotMatch(card.shadowRoot.querySelector('.info-panel strong').textContent, /MET Norway/);
  }
});

test('DMI threshold presentation and risk label use the actual configured window', async () => {
  const { card, Card } = environment(); const values = {}; const riskLabel = element();
  const current = hass(async () => payload());
  const risk = state('a', 'rain_risk_12h', '100', { window_hours: 6, probability_method: 'threshold' });
  current.states[risk.entity_id] = risk;
  card.hass = current; await settle(); card._layoutRendered = true;
  card._setMetaValue = (key, value) => { values[key] = value; };
  card.shadowRoot.querySelectorAll = (selector) => selector === '[data-meta-key="risk"] b' ? [riskLabel] : [];
  Card.prototype._update.call(card);
  assert.equal(riskLabel.textContent, 'Regnrisk (6 h):');
  assert.equal(values.risk, 'Regn över vald tröskel väntas (Tröskelutfall, inte sannolikhet)');
  assert.doesNotMatch(values.risk, /100%/);
});

test('unverified Regnradar legend never claims a numeric or qualitative intensity scale', () => {
  const { card } = environment(); card._config.show_legend = true;
  card._syncLegend({ radarMetadata: { productId: 'regnradar_dk', colorScale: [{ label: 'Extreme', color: '#fe625f' }] } });
  const html = card.shadowRoot.querySelector('.product-legend').innerHTML;
  assert.match(html, /Regnradar/); assert.match(html, /No verified intensity scale/); assert.doesNotMatch(html, /Extreme/);
});

test('selecting an unrelated entity clears old-location radar and invalidates its response', async () => {
  const { card, timers } = environment(); card.hass = hass(async () => payload()); await settle();
  card._config.entity = 'sensor.unrelated'; card.hass = { ...card._hass }; await settle();
  assert.equal(card._frames.length, 0); assert.equal(card._location, null); assert.equal(timers.size, 0);
});

test('editor echoed configuration does not recreate form after a field edit', () => {
  const { Editor } = environment(); const editor = new Editor();
  editor._config = editor._normalizeConfig({ entity: 'sensor.a_precipitation_now' });
  editor._formValueChanged({ detail: { value: { title: 'Updated title' } } });
  let renders = 0; editor._render = () => { renders++; };
  editor.setConfig({ ...editor._config }); assert.equal(renders, 0);
});

test('legacy mixed-source provider state still identifies the actual DMI forecast and outage', async () => {
  const { card, Card } = environment();
  const current = hass(async () => payload());
  current.states['sensor.a_provider'] = state('a', 'provider', 'Regnradar', { forecast_provider_id: 'dmi', coverage_status: 'temporarily_unavailable' });
  current.states['binary_sensor.a_rain_soon'].state = 'unavailable';
  current.states['sensor.a_precipitation_now'].state = 'unavailable';
  card.hass = current; await settle(); card._layoutRendered = true;
  Card.prototype._update.call(card);
  assert.match(card.shadowRoot.querySelector('.forecast-health').textContent, /^DMI prognos: Tillfälligt otillgänglig/);
});

test('cold-start unavailable selection resolves its entry and companions from registry device identity', async () => {
  const { card, Card } = environment(); let calls = 0;
  const current = hass(async (_, path) => { calls++; assert.equal(path, 'rain_radar/a/frames'); return payload(); });
  // Deliberately renamed entities: neither a name nor an entity-id suffix identifies the role.
  const unavailable = { entity_id: 'binary_sensor.my_custom_status', state: 'unavailable', attributes: { friendly_name: 'Valfri plats' } };
  const arrival = { entity_id: 'sensor.renamed_arrival', state: 'unavailable', attributes: {} };
  delete current.states['binary_sensor.a_rain_soon'];
  current.states[unavailable.entity_id] = unavailable; current.states[arrival.entity_id] = arrival;
  current.states['sensor.a_provider'].attributes.forecast_provider_id = 'dmi';
  current.states['sensor.a_precipitation_now'].state = 'unavailable';
  current.entities = Object.fromEntries(Object.values(current.states).map((entity) => [entity.entity_id, {
    device_id: 'device-a', platform: 'rain_radar',
    translation_key: entity === unavailable ? 'rain_soon' : entity === arrival ? 'rain_arrival' : entity.attributes.rain_radar_entity_key,
  }]));
  card._config.entity = unavailable.entity_id;
  card.hass = current; await settle(); card._layoutRendered = true;
  assert.equal(calls, 1); assert.equal(card._frames.length, 2);
  assert.equal(card._context().main, unavailable); assert.equal(card._context().arrival, arrival);
  assert.equal(card._context().entryId, 'a');
  Card.prototype._update.call(card);
  assert.match(card.shadowRoot.querySelector('.forecast-health').textContent, /^DMI prognos: Tillfälligt otillgänglig/);
  assert.equal(card.shadowRoot.querySelector('.message').hidden, true);
});

test('device registry supports a cold start with no attributed companion and avoids ambiguous entries', async () => {
  for (const entries of [['a'], ['a', 'unrelated']]) {
    const { card } = environment(); let calls = 0;
    const current = { states: { 'binary_sensor.renamed': { entity_id: 'binary_sensor.renamed', state: 'unavailable', attributes: {} } },
      entities: { 'binary_sensor.renamed': { platform: 'rain_radar', device_id: 'device-a', translation_key: 'rain_soon' } },
      devices: { 'device-a': { config_entries: entries } }, callApi: async () => { calls++; return payload(); } };
    card._config.entity = 'binary_sensor.renamed'; card.hass = current; await settle();
    assert.equal(calls, entries.length === 1 ? 1 : 0);
  }
});

test('registry companions from a different location cannot supply the selected rain status', async () => {
  const { card } = environment(); const current = hass(async () => payload());
  const other = { entity_id: 'binary_sensor.other_place', state: 'on', attributes: {} };
  current.states[other.entity_id] = other;
  current.entities = { [other.entity_id]: { platform: 'rain_radar', device_id: 'device-b', translation_key: 'rain_soon' } };
  current.devices = { 'device-b': { config_entries: ['b'] } };
  delete current.states['binary_sensor.a_rain_soon'];
  card._config.entity = 'sensor.a_provider'; card.hass = current; await settle();
  assert.equal(card._context().main, undefined); assert.equal(card._context().rainingSoon, false);
});

test('source reasons explain stale, incomplete, absent and failed data in Swedish and English', () => {
  const { card } = environment();
  const explanations = {
    stale_data: ['Väderdata är för gamla', 'The weather data is too old'],
    incomplete_window: ['Prognosen täcker inte hela tidsfönstret', 'Forecast data does not cover the whole time window'],
    missing_timestamp: ['Leverantören saknar observationstid', 'The provider has not supplied an observation time'],
    no_frames: ['Inga radarbilder finns tillgängliga', 'No radar images are available'],
    timeout: ['Leverantören svarade inte i tid', 'The provider did not respond in time'],
    network: ['Kunde inte ansluta till leverantören', 'Could not connect to the provider'],
    invalid_response: ['Leverantören skickade data som inte kunde läsas', 'The provider returned unreadable data'],
    request_failed: ['Hämtningen från leverantören misslyckades', 'The provider request failed'],
  };
  for (const [index, language] of ['sv', 'en'].entries()) {
    card._hass = { locale: { language } };
    for (const [reason, expected] of Object.entries(explanations)) {
      assert.equal(card._t(`reason_${reason}`), expected[index]);
      card._syncSourceStatus({ radarStatus: { status: 'stale', reason }, forecastStatus: {}, timelineFrames: [] }, 'DMI');
      assert.ok(card.shadowRoot.querySelector('.radar-health').textContent.includes(expected[index]));
    }
  }
});

test('editor localizes its first hass assignment and rerenders only when the display language changes', () => {
  const { Editor } = environment(); const editor = new Editor();
  editor._config = editor._normalizeConfig({ entity: 'sensor.a_provider' });
  let renders = 0;
  editor._render = () => { renders++; Editor.prototype._render.call(editor); };
  editor._render(); // HA initially calls setConfig before supplying hass.
  const current = hass(async () => payload());
  editor.hass = current;
  assert.equal(renders, 2);
  const form = editor.shadowRoot.querySelector('.editor-form');
  assert.equal(form.computeLabel({ label: 'Show provider info panel' }), 'Visa källinformation');
  assert.equal(form.computeLabel({ label: 'Entity' }), 'Rain Radar-plats (entitet)');
  assert.equal(form.schema.find((field) => field.name === 'arrival_format').selector.select.options[0].label, 'Automatiskt');
  assert.match(editor.shadowRoot.innerHTML, /Attribut och detaljer/);
  assert.match(editor.shadowRoot.innerHTML, /Flytta upp/);
  editor.hass = { ...current }; assert.equal(renders, 2);
  editor.hass = { ...current, language: 'sv', locale: { language: 'en' } };
  assert.equal(renders, 3, 'HA locale overrides the legacy language');
  assert.equal(form.computeLabel({ label: 'Show provider info panel' }), 'Show provider info panel');
  assert.equal(form.schema.find((field) => field.name === 'arrival_format').selector.select.options[0].label, 'Auto');
  assert.match(editor.shadowRoot.innerHTML, /Attributes and details/);
  editor.hass = { ...current, locale: { language: 'en-GB' } };
  assert.equal(renders, 3, 'equivalent English language settings preserve focus');
});

test('hourly interval coverage includes the current and final partial hours and is bounded to the requested window', () => {
  const { helpers, advance } = environment(); advance((5 * 60 + 8) * 60000); // 13:38 UTC
  const interval = (start, end, rate = 0) => ({ time: `2026-09-06T${end}:00:00Z`, interval_start: `2026-09-06T${start}:00:00Z`, interval_end: `2026-09-06T${end}:00:00Z`, precipitation_rate: rate });
  const precipitation = { attributes: { forecast_samples: [interval('13', '14'), interval('14', '15')] } };
  const selected = helpers.forecastSamples(precipitation, 60);
  assert.equal(selected.length, 2);
  assert.equal(helpers.availableForecastMinutes(selected), 60, '13:38–14:38 is completely covered');
  assert.equal(helpers.availableForecastMinutes(helpers.forecastSamples(precipitation, 30)), 30);
  assert.equal(helpers.availableForecastMinutes(helpers.forecastSamples({ attributes: { forecast_samples: [interval('13', '14')] } }, 60)), 22);
  assert.equal(helpers.availableForecastMinutes(helpers.forecastSamples({ attributes: { forecast_samples: [interval('14', '15')] } }, 60)), 0, 'missing current coverage is not called available');
  const long = { attributes: { forecast_samples: ['13', '14', '15', '16', '17'].map((start) => interval(start, String(Number(start) + 1))) } };
  assert.equal(helpers.availableForecastMinutes(helpers.forecastSamples(long, 999)), 180, 'card horizon remains bounded');
});

test('missing values and gaps stop interval coverage without inventing dry data', () => {
  const { helpers, advance } = environment(); advance((5 * 60 + 8) * 60000);
  const first = { time: '2026-09-06T14:00:00Z', interval_start: '2026-09-06T13:00:00Z', interval_end: '2026-09-06T14:00:00Z', precipitation_rate: 0 };
  for (const second of [
    { time: '2026-09-06T15:00:00Z', interval_start: '2026-09-06T14:00:00Z', interval_end: '2026-09-06T15:00:00Z', precipitation_rate: null },
    { time: '2026-09-06T15:00:00Z', interval_start: '2026-09-06T14:15:00Z', interval_end: '2026-09-06T15:00:00Z', precipitation_rate: 0 },
  ]) {
    const selected = helpers.forecastSamples({ attributes: { forecast_samples: [first, second] } }, 60);
    assert.equal(helpers.availableForecastMinutes(selected), 22);
  }
});

test('arrival fallback treats ongoing rain intervals as current, even when their timestamp is the end', () => {
  const { helpers, advance } = environment(); advance((5 * 60 + 8) * 60000);
  const precipitation = { attributes: { forecast_samples: [{ time: '2026-09-06T14:00:00Z', interval_start: '2026-09-06T13:00:00Z', interval_end: '2026-09-06T14:00:00Z', precipitation_rate: 1 }] } };
  assert.equal(helpers.arrivalMinutesFromForecastSamples(precipitation, 0.1), 0);
});

test('MET nowcast outage preserves a fresh independent Locationforecast risk value', async () => {
  const { card, Card } = environment(); const values = {}; const current = hass(async () => payload());
  current.states['binary_sensor.a_rain_soon'].state = 'unavailable';
  current.states['sensor.a_precipitation_now'].state = 'unavailable';
  current.states['binary_sensor.a_rain_soon'].attributes.forecast_status = { status: 'temporarily_unavailable', reason: 'network_error' };
  const risk = state('a', 'rain_risk_12h', '70', { is_stale: false, window_complete: true });
  current.states[risk.entity_id] = risk; current.states['sensor.a_provider'].attributes.forecast_provider_id = 'met_no';
  card.hass = current; await settle(); card._layoutRendered = true; card._setMetaValue = (key, value) => { values[key] = value; };
  Card.prototype._update.call(card);
  assert.equal(values.risk, '70%'); assert.equal(values.precipitation, 'Okänt'); assert.equal(values.status, 'Regnstatus okänd');
  assert.match(card.shadowRoot.querySelector('.forecast-health').textContent, /Delvis tillgänglig.*Korttidsprognos saknas.*Regnriskdata tillgängliga/);
});

test('an incomplete or stale long-term risk never hides fresh current precipitation and rain status', async () => {
  for (const quality of ['unknown', 'stale']) {
    const { card, Card } = environment(); const values = {}; const current = hass(async () => payload());
    current.states['binary_sensor.a_rain_soon'].state = 'on';
    current.states['binary_sensor.a_rain_soon'].attributes.forecast_status = { status: quality, reason: quality === 'stale' ? 'stale_data' : 'incomplete_window' };
    current.states['sensor.a_precipitation_now'].state = '1.2';
    Object.assign(current.states['sensor.a_precipitation_now'].attributes, { is_stale: false, window_complete: true });
    const risk = state('a', 'rain_risk_12h', quality === 'stale' ? '70' : 'unknown', { is_stale: quality === 'stale', window_complete: false });
    current.states[risk.entity_id] = risk; current.states['sensor.a_provider'].attributes.forecast_provider_id = 'met_no';
    card.hass = current; await settle(); card._layoutRendered = true; card._setMetaValue = (key, value) => { values[key] = value; };
    Card.prototype._update.call(card);
    assert.equal(values.precipitation, '1,2 mm/h'); assert.equal(values.status, 'Regn snart'); assert.equal(values.risk, 'Okänt');
    assert.match(card.shadowRoot.querySelector('.forecast-health').textContent, /Delvis tillgänglig.*Korttidsprognos tillgänglig.*Regnriskdata saknas/);
  }
});

test('positive risk from an incomplete window remains visible with a qualification while zero remains unknown', async () => {
  for (const riskNumber of ['40', '0']) {
    const { card, Card } = environment(); const values = {}; const current = hass(async () => payload());
    current.states['binary_sensor.a_rain_soon'].attributes.forecast_status = { status: 'unknown', reason: 'incomplete_window' };
    const risk = state('a', 'rain_risk_12h', riskNumber, { is_stale: false, window_complete: false });
    current.states[risk.entity_id] = risk; current.states['sensor.a_provider'].attributes.forecast_provider_id = 'met_no';
    card.hass = current; await settle(); card._layoutRendered = true; card._setMetaValue = (key, value) => { values[key] = value; };
    Card.prototype._update.call(card);
    assert.equal(values.risk, riskNumber === '40' ? '40% · Ofullständigt tidsfönster' : 'Okänt');
  }
});

test('dry arrival text is limited to a proven complete time window and localized', async () => {
  for (const complete of [true, false, undefined]) {
    for (const language of ['sv', 'en']) {
      const { card, Card } = environment(); const values = {}; const current = hass(async () => payload());
      current.locale.language = language;
      Object.assign(current.states['sensor.a_precipitation_now'].attributes, { window_complete: complete, rain_soon_window_minutes: 60, is_stale: false });
      const arrival = state('a', 'rain_arrival', 'unknown'); current.states[arrival.entity_id] = arrival;
      card.hass = current; await settle(); card._layoutRendered = true; card._setMetaValue = (key, value) => { values[key] = value; };
      Card.prototype._update.call(card);
      assert.equal(values.arrival, complete === true ? language === 'sv' ? 'Ingen regnankomst inom 60 min' : 'No rain arrival within 60 min' : language === 'sv' ? 'Okänt' : 'Unknown');
    }
  }
});

function overlayEnvironment() {
  const env = environment(); const { card, scope } = env;
  const layers = []; const animationFrames = new Map();
  let animationTime = 0; let animationId = 0;
  scope.performance = { now: () => animationTime };
  scope.window.location = { origin: "http://homeassistant.local" };
  scope.requestAnimationFrame = (callback) => { animationFrames.set(++animationId, callback); return animationId; };
  scope.cancelAnimationFrame = (id) => { animationFrames.delete(id); };
  scope.window.requestAnimationFrame = scope.requestAnimationFrame;
  scope.window.cancelAnimationFrame = scope.cancelAnimationFrame;
  scope.setTimeout = scope.window.setTimeout; scope.clearTimeout = scope.window.clearTimeout;
  const L = { imageOverlay(url, bounds, options) {
    const events = new Map(); const image = element();
    const layer = {
      url, bounds, options, opacity: options.opacity, removed: false, opacityHistory: [],
      once(name, callback) { events.set(name, callback); return this; },
      off(name) { events.delete(name); return this; },
      emit(name) { const callback = events.get(name); events.delete(name); callback?.(); },
      addTo(map) { this.map = map; return this; },
      setOpacity(value) { this.opacity = value; this.opacityHistory.push(value); image.style.opacity = value; return this; },
      getElement() { return image; }, bringToFront() { return this; },
      remove() { this.removed = true; return this; },
    };
    layers.push(layer); return layer;
  } };
  card._map = {};
  card._prepareOverlayUrl = async (_, imageUrl) => ({ url: `blob:${imageUrl}` });
  card._withTimeout = (promise) => promise;
  const context = (signature = 'first', id = 'image1') => ({
    entryId: 'a', activeFrame: { id, kind: 'observed', timeMs: 1000, imageUrl: `/api/rain_radar/a/frames/${id}/image?authSig=${signature}` },
    radarMetadata: { productId: 'regnradar_se', overlayMode: 'regnradar_coverage' },
    radarBounds: { south: 52, west: 3, north: 71, east: 41 },
  });
  async function stepPaint(elapsed = 16) {
    animationTime += elapsed; env.advance(elapsed);
    const pending = [...animationFrames.values()]; animationFrames.clear();
    pending.forEach((callback) => callback(animationTime));
    await settle();
  }
  async function paint() {
    for (let frame = 0; frame < 45; frame++) await stepPaint();
  }
  async function showInitial() {
    const rendering = card._syncRadarOverlay(L, context()); await settle();
    assert.equal(layers.length, 1); layers[0].emit('load'); await settle(); await paint(); await rendering;
    assert.equal(card._radarLayer, layers[0]); assert.equal(layers[0].opacity, 1);
    return layers[0];
  }
  return { ...env, L, layers, context, paint, stepPaint, animationFrames, showInitial };
}

test('signature renewal for the same radar frame preserves its visible image layer', async () => {
  const { card, L, layers, context, showInitial } = overlayEnvironment();
  const visible = await showInitial();
  const refreshed = card._syncRadarOverlay(L, context('renewed-signature')); await settle();
  assert.equal(layers.length, 1, 'authentication renewal is not a new weather image');
  assert.equal(card._radarLayer, visible); assert.equal(visible.removed, false); assert.equal(visible.opacity, 1);
  await refreshed;
});

test('repeated HA updates while the same radar frame loads share one pending render', async () => {
  const { card, L, layers, context, paint } = overlayEnvironment();
  const first = card._syncRadarOverlay(L, context()); await settle();
  assert.equal(layers.length, 1);
  const second = card._syncRadarOverlay(L, context()); await settle();
  assert.equal(layers.length, 1, 'the loading Leaflet layer must not be restarted by the same frame');
  layers[0].emit('load'); await settle(); await paint(); await Promise.all([first, second]);
  assert.equal(card._radarLayer, layers[0]); assert.equal(layers[0].removed, false); assert.equal(layers[0].opacity, 1);
});

test('previous radar image remains fully visible until the next image has loaded', async () => {
  const { card, L, layers, context, paint, showInitial } = overlayEnvironment();
  const previous = await showInitial();
  const rendering = card._syncRadarOverlay(L, context('new', 'image2')); await settle();
  assert.equal(layers.length, 2); assert.equal(previous.opacity, 1); assert.equal(previous.removed, false);
  assert.equal(card._radarLayer, previous, 'keep the old image authoritative while waiting');
  assert.equal(layers[1].opacity, 0);
  layers[1].emit('load'); await settle(); await paint(); await rendering;
  assert.equal(card._radarLayer, layers[1]); assert.equal(layers[1].opacity, 1);
});

test('a failed next image does not blank or replace the last successfully shown radar image', async () => {
  const { card, L, layers, context, showInitial } = overlayEnvironment();
  const previous = await showInitial();
  const rendering = card._syncRadarOverlay(L, context('new', 'image2')); await settle();
  layers[1].emit('error'); await rendering;
  assert.equal(card._radarLayer, previous); assert.equal(previous.opacity, 1); assert.equal(previous.removed, false);
  assert.equal(layers[1].removed, true);
});

test('returning to the visible frame cancels a different pending frame before it can replace the selection', async () => {
  const { card, L, layers, context, paint, showInitial } = overlayEnvironment();
  const previous = await showInitial();
  const pending = card._syncRadarOverlay(L, context('new', 'image2')); await settle();
  await card._syncRadarOverlay(L, context());
  layers[1].emit('load'); await settle(); await paint(); await pending;
  assert.equal(card._radarLayer, previous, 'late loading frame must not override the frame selected again');
  assert.equal(previous.opacity, 1); assert.equal(layers[1].removed, true);
});

function assertBlendWeight(card, expected = 1) {
  const sum = [...card._radarBlendLayers.values()].reduce((total, weight) => total + weight, 0);
  assert.ok(Math.abs(sum - expected) < 0.000001, `visible radar layer weights total ${sum}, expected ${expected}`);
  for (const [layer, weight] of card._radarBlendLayers) {
    assert.ok(weight >= 0 && weight <= 1); assert.equal(layer.opacity, weight);
  }
}

test('radar blend conserves full image weight throughout a fade and when a rapid next frame interrupts it', async () => {
  const { card, L, context, showInitial, stepPaint, paint } = overlayEnvironment();
  const first = await showInitial();
  const second = L.imageOverlay('blob:second', context().radarBounds, { opacity: 0 }).addTo(card._map);
  card._fadeRadarLayers(second); await stepPaint(100);
  assertBlendWeight(card);
  assert.ok(card._radarBlendLayers.get(first) > 0 && card._radarBlendLayers.get(second) > 0, 'both images contribute during the fade');
  const beforeInterruption = new Map(card._radarBlendLayers);
  const third = L.imageOverlay('blob:third', context().radarBounds, { opacity: 0 }).addTo(card._map);
  card._fadeRadarLayers(third);
  for (const [layer, weight] of beforeInterruption) assert.equal(card._radarBlendLayers.get(layer), weight, 'starting another fade must not jump the existing image brightness');
  await stepPaint(100); assertBlendWeight(card);
  assert.ok(card._radarBlendLayers.get(third) > 0);
  await paint(); assertBlendWeight(card);
  assert.equal(card._radarBlendLayers.size, 1); assert.equal(card._radarBlendLayers.get(third), 1);
  assert.equal(first.removed, true); assert.equal(second.removed, true);
});

test('reduced-motion preference switches loaded images immediately without a fade animation', async () => {
  const { card, scope, L, context, showInitial, animationFrames } = overlayEnvironment();
  const first = await showInitial(); scope.matchMedia = () => ({ matches: true });
  const next = L.imageOverlay('blob:next', context().radarBounds, { opacity: 0 }).addTo(card._map);
  card._fadeRadarLayers(next);
  assert.equal(animationFrames.size, 0); assert.equal(card._radarBlendLayers.size, 1);
  assert.equal(card._radarBlendLayers.get(next), 1); assert.equal(next.opacity, 1); assert.equal(first.removed, true);
});

test('destroying the map cancels an active radar fade and removes all contributing image layers', async () => {
  const { card, L, context, showInitial, stepPaint, animationFrames } = overlayEnvironment();
  const first = await showInitial();
  const next = L.imageOverlay('blob:next', context().radarBounds, { opacity: 0 }).addTo(card._map);
  card._fadeRadarLayers(next); await stepPaint(100);
  assert.ok(animationFrames.size > 0);
  card._destroyMap();
  assert.equal(animationFrames.size, 0); assert.equal(card._radarBlendLayers.size, 0);
  assert.equal(first.removed, true); assert.equal(next.removed, true);
});
