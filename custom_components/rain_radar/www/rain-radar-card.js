const CARD_TYPE = "rain-radar-card";
const EDITOR_TYPE = "rain-radar-card-editor";

const LEAFLET_CSS_HREF = "/local/rain_radar/vendor/leaflet/leaflet.css";
const LEAFLET_ESM_URL = "/local/rain_radar/vendor/leaflet/leaflet-src.esm.js";
const LEAFLET_JS_SRC = "/local/rain_radar/vendor/leaflet/leaflet.js";
const DEFAULT_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const DEFAULT_TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const MAP_TILE_REFERRER_POLICY = "strict-origin-when-cross-origin";
const DEFAULT_RADAR_ATTRIBUTION = "Radar: Regnradar/Vackertväder";
const DEFAULT_RADAR_OPACITY = 0.78;
const DEFAULT_COVERAGE_OPACITY = 0.42;
const DEFAULT_ANIMATION_INTERVAL_MS = 550;
const DEFAULT_ARRIVAL_FORMAT = "auto";
const RADAR_OVERLAY_CACHE_LIMIT = 14;
const RADAR_LAYER_LOAD_TIMEOUT_MS = 1200;
const WEB_MERCATOR_WORLD_BOUNDS = {
  south: -85.05112878,
  west: -180,
  north: 85.05112878,
  east: 180,
};
const DEFAULT_RADAR_BOUNDS = {
  south: 52.295184,
  west: 3.448806,
  north: 71.520959,
  east: 40.837085,
};
const META_KEYS = [
  "status",
  "arrival",
  "precipitation",
  "risk",
  "latest_radar",
  "coverage",
  "provider",
  "forecast",
  "timeline",
  "map",
];
const SPECIAL_META_KEYS = ["divider"];
const DEFAULT_META_ORDER = [
  "status",
  "arrival",
  "precipitation",
  "risk",
  "divider",
  "latest_radar",
  "coverage",
  "provider",
  "forecast",
  "timeline",
  "map",
];
const META_CONFIG_KEYS = {
  status: "show_status",
  arrival: "show_arrival",
  precipitation: "show_precipitation",
  risk: "show_risk",
  latest_radar: "show_latest_radar",
  coverage: "show_coverage",
  provider: "show_provider",
  forecast: "show_forecast",
  timeline: "show_timeline",
  map: "show_map",
};
const DEFAULT_CONFIG = {
  title: "",
  show_icon: true,
  severity_background: false,
  show_status: true,
  show_precipitation: true,
  show_arrival: true,
  show_risk: true,
  show_latest_radar: true,
  show_provider: true,
  show_coverage: true,
  show_map: true,
  meta_order: DEFAULT_META_ORDER,
  show_timeline: true,
  show_status_strip: true,
  show_legend: false,
  show_info_panel: false,
  show_location_marker: true,
  show_forecast: true,
  map_zoom_controls: true,
  map_scroll_wheel: false,
  tile_url: DEFAULT_TILE_URL,
  tile_attribution: DEFAULT_TILE_ATTRIBUTION,
  min_zoom: 3,
  max_zoom: 10,
  default_zoom: 6,
  default_animation_mode: "paused",
  height: 420,
  forecast_minutes: 60,
  radar_opacity: DEFAULT_RADAR_OPACITY,
  coverage_opacity: DEFAULT_COVERAGE_OPACITY,
  animation_interval_ms: DEFAULT_ANIMATION_INTERVAL_MS,
  arrival_format: DEFAULT_ARRIVAL_FORMAT,
};

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    const replacements = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    };
    return replacements[char];
  });
}

function parseTime(value) {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function localTime(value) {
  const date = parseTime(value);
  if (!date) return "Unknown";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function relativeMinutes(value) {
  const number = minuteValue(value);
  if (number === null) return "Unknown";
  if (number <= 0) return "Now";
  if (number === 1) return "1 min";
  return `${Math.round(number)} min`;
}

function durationMinutes(value) {
  const valueMinutes = minuteValue(value);
  if (valueMinutes === null) return "Unknown";
  const minutes = Math.round(valueMinutes);
  if (minutes <= 0) return "Now";
  if (minutes < 60) return minutes === 1 ? "1 min" : `${minutes} min`;

  const hours = Math.floor(minutes / 60);
  const remainder = minutes % 60;
  return remainder > 0 ? `${hours} h ${remainder} min` : `${hours} h`;
}

function clockTimeAfterMinutes(value) {
  const valueMinutes = minuteValue(value);
  if (valueMinutes === null) return "";
  const minutes = Math.round(valueMinutes);
  if (minutes <= 0) return "Now";
  return localTime(new Date(Date.now() + minutes * 60 * 1000));
}

function arrivalText(value, format = DEFAULT_ARRIVAL_FORMAT) {
  const minutes = minuteValue(value);
  if (minutes === null) return "Unknown";
  if (minutes <= 0) return "Now";

  const rounded = Math.round(minutes);
  const duration = durationMinutes(rounded);
  const clock = clockTimeAfterMinutes(rounded);
  switch (format) {
    case "minutes":
      return relativeMinutes(rounded);
    case "duration":
      return duration;
    case "time":
      return clock || duration;
    case "duration_time":
      return clock ? `${duration} (${clock})` : duration;
    case "auto":
    default:
      return rounded >= 60 && clock ? `${duration} (${clock})` : relativeMinutes(rounded);
  }
}

function minuteValue(value) {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed || ["unknown", "unavailable", "none", "null"].includes(trimmed.toLowerCase())) {
      return null;
    }
    const normalized = trimmed.replace(",", ".");
    const number = Number(normalized);
    if (Number.isFinite(number)) return number;
    const parsed = Number.parseFloat(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function numberText(value, suffix = "") {
  const number = Number(value);
  if (!Number.isFinite(number)) return "Unknown";
  return `${Math.round(number * 10) / 10}${suffix}`;
}

function stateText(stateObj, fallback = "Unknown") {
  if (!stateObj || stateObj.state === "unknown" || stateObj.state === "unavailable") {
    return fallback;
  }
  return stateObj.state;
}

function rainSoonStatus(main) {
  if (main?.state === "on") {
    return {
      label: "Rain soon",
      labelKey: "rain_soon",
      icon: "mdi:weather-pouring",
      wet: true,
      className: "rain-on",
    };
  }
  if (main?.state === "off") {
    return {
      label: "No rain soon",
      labelKey: "no_rain_soon",
      icon: "mdi:weather-partly-cloudy",
      wet: false,
      className: "rain-off",
    };
  }
  return {
    label: "Rain status unknown",
    labelKey: "rain_unknown",
    icon: "mdi:weather-cloudy-alert",
    wet: false,
    className: "rain-unknown",
  };
}

function locationNameFromEntity(main) {
  const name = String(main?.attributes?.friendly_name || "").trim();
  if (!name) return "";
  return name.replace(/\s+rain\s+soon$/i, "").trim();
}

function findEntityBySuffix(hass, entryId, suffix) {
  return Object.values(hass.states).find((entity) => {
    const attrs = entity.attributes || {};
    return attrs.rain_radar_entry_id === entryId && entity.entity_id.endsWith(suffix);
  });
}

function dispatchConfigChanged(element, config) {
  const event = new Event("config-changed", {
    bubbles: true,
    composed: true,
  });
  event.detail = { config };
  element.dispatchEvent(event);
}

function normalizeMetaOrder(rawOrder) {
  const allowed = [...META_KEYS, ...SPECIAL_META_KEYS];
  const source = Array.isArray(rawOrder) && rawOrder.length ? rawOrder : DEFAULT_META_ORDER;
  const normalized = source
    .map((key) => String(key))
    .filter((key, index, order) => allowed.includes(key) && order.indexOf(key) === index);
  if (!normalized.includes("divider")) normalized.push("divider");
  META_KEYS.forEach((key) => {
    if (!normalized.includes(key)) normalized.push(key);
  });
  return normalized;
}

function splitMetaOrder(rawOrder) {
  const order = normalizeMetaOrder(rawOrder);
  const dividerIndex = order.indexOf("divider");
  return {
    order,
    inlineKeys: dividerIndex >= 0 ? order.slice(0, dividerIndex) : order,
    detailsKeys: dividerIndex >= 0 ? order.slice(dividerIndex + 1) : [],
  };
}

function locationFromPayload(payload) {
  const latitude = Number(payload?.latitude);
  const longitude = Number(payload?.longitude);
  if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
  return { latitude, longitude };
}

function locationKey(location) {
  if (!location) return "";
  return `${location.latitude.toFixed(5)},${location.longitude.toFixed(5)}`;
}

function boundsFromPayload(bounds) {
  const south = Number(bounds?.south);
  const west = Number(bounds?.west);
  const north = Number(bounds?.north);
  const east = Number(bounds?.east);
  if (
    !Number.isFinite(south) ||
    !Number.isFinite(west) ||
    !Number.isFinite(north) ||
    !Number.isFinite(east) ||
    north <= south ||
    east <= west
  ) {
    return { ...DEFAULT_RADAR_BOUNDS };
  }
  return { south, west, north, east };
}

function leafletBounds(bounds) {
  return [
    [bounds.south, bounds.west],
    [bounds.north, bounds.east],
  ];
}

function frameTimeMs(frame) {
  const date = parseTime(frame?.time);
  return date ? date.getTime() : 0;
}

function radarFrameType(frame) {
  return String(frame?.type || frame?.frameType || frame?.frame?.type || "obs").toLowerCase();
}

function isObservedRadarFrame(frame) {
  return !["fcst", "forecast"].includes(radarFrameType(frame));
}

function forecastSamples(precipitation, minutes) {
  const samples = precipitation?.attributes?.forecast_samples;
  if (!Array.isArray(samples)) return [];

  const now = Date.now();
  const windowMs =
    clamp(Number(minutes) || DEFAULT_CONFIG.forecast_minutes, 15, 180) * 60 * 1000;
  const parsed = samples
    .map((sample) => {
      const time = parseTime(sample.time);
      const rate = Number(sample.precipitation_rate);
      if (!time) return null;
      return {
        time,
        timeMs: time.getTime(),
        rate: Number.isFinite(rate) ? rate : null,
      };
    })
    .filter((sample) => sample && sample.timeMs >= now - 2 * 60 * 1000)
    .filter((sample) => sample.timeMs <= now + windowMs)
    .sort((a, b) => a.timeMs - b.timeMs);

  const available = [];
  for (const sample of parsed) {
    if (!Number.isFinite(sample.rate)) break;
    available.push(sample);
    if (available.length >= 16) break;
  }
  return available;
}

function arrivalMinutesFromForecastSamples(precipitation, threshold) {
  const samples = precipitation?.attributes?.forecast_samples;
  if (!Array.isArray(samples)) return null;

  const now = Date.now();
  const rainThreshold = Number.isFinite(Number(threshold)) ? Number(threshold) : 0.1;
  const nextRain = samples
    .map((sample) => {
      const time = parseTime(sample.time);
      const rate = Number(sample.precipitation_rate);
      if (!time || !Number.isFinite(rate) || rate < rainThreshold) return null;
      return time.getTime();
    })
    .filter((timeMs) => timeMs !== null && timeMs >= now - 2 * 60 * 1000)
    .sort((a, b) => a - b)[0];

  if (!Number.isFinite(nextRain)) return null;
  return Math.max(0, Math.round((nextRain - now) / 60000));
}

function buildTimelineFrames(frames) {
  return Array.isArray(frames)
    ? frames
        .filter((frame) => frame?.image_url)
        .map((frame) => ({
          kind: isObservedRadarFrame(frame) ? "observed" : "forecast",
          frame,
          frameType: radarFrameType(frame),
          imageUrl: frame.image_url,
          id: frame.id,
          label: frame.label || localTime(frame.time),
          time: parseTime(frame.time),
          timeMs: frameTimeMs(frame),
          rate: null,
        }))
        .sort((a, b) => a.timeMs - b.timeMs)
    : [];
}

function availableForecastMinutes(samples) {
  if (!samples.length) return 0;

  const latestForecast = samples[samples.length - 1];
  const minutes = Math.round((latestForecast.timeMs - Date.now()) / 60000);
  return Math.max(1, minutes);
}

function precipitationColor(rate, threshold) {
  const value = Number(rate);
  if (!Number.isFinite(value) || value <= 0) return "rgba(112, 128, 144, 0.78)";
  if (value >= threshold * 8) return "#d7301f";
  if (value >= threshold * 4) return "#fc8d2a";
  if (value >= threshold) return "#0693c7";
  return "rgba(112, 128, 144, 0.78)";
}

function frameLabel(frame, fallback) {
  return frame?.label || localTime(frame?.time || fallback);
}

function latestRadarText(timelineFrames, radarTime) {
  const sensorTime = stateText(radarTime, null);
  if (parseTime(sensorTime)) return localTime(sensorTime);

  const latestFrame = [...(timelineFrames || [])]
    .filter((frame) => isObservedRadarFrame(frame) && frame?.imageUrl)
    .sort((a, b) => a.timeMs - b.timeMs)
    .at(-1);
  if (!latestFrame) return "";
  return frameLabel(latestFrame, latestFrame.time);
}

function framePhase(frame) {
  if (!frame) return "Waiting";
  return frame.kind === "forecast" ? "Forecast" : "Radar";
}

function forecastSummary(frame, threshold) {
  if (!frame || frame.kind !== "forecast") return "";
  const rate = Number(frame.rate);
  if (!Number.isFinite(rate) && frame.imageUrl) return "Radar forecast image";
  if (!Number.isFinite(rate) || rate < threshold) return "No rain forecast";
  return `${numberText(rate, " mm/h")} forecast`;
}

function isFiveLevelReflectivityPixel(red, green, blue) {
  const isRed = red >= 190 && green <= 105 && blue <= 105;
  const isOrange = red >= 210 && green >= 95 && green <= 185 && blue <= 90;
  const isYellow = red >= 210 && green >= 205 && blue <= 105;
  const isGreen = green >= 145 && red >= 95 && red <= 190 && blue <= 145;
  return isRed || isOrange || isYellow || isGreen;
}

function isRegnradarPrecipitationPixel(red, green, blue) {
  const isBlue = blue >= 150 && green >= 120 && red <= 120;
  const isCyan = blue >= 130 && green >= 165 && red <= 115;
  return isFiveLevelReflectivityPixel(red, green, blue) || isBlue || isCyan;
}

function isRegnradarCoverageShadePixel(red, green, blue, alpha) {
  if (alpha < 16) return false;
  const maxChannel = Math.max(red, green, blue);
  const minChannel = Math.min(red, green, blue);
  return maxChannel - minChannel <= 38 && maxChannel <= 248;
}

async function loadImage(url) {
  return await new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Radar image failed to load"));
    image.src = url;
  });
}

function canvasToObjectUrl(canvas, errorMessage) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error(errorMessage));
        return;
      }
      resolve(URL.createObjectURL(blob));
    }, "image/png");
  });
}

async function canvasMaskRadarOverlayToObjectUrl(imageUrl, opacity) {
  const image = await loadImage(imageUrl);
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth || image.width;
  canvas.height = image.naturalHeight || image.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("Canvas is unavailable");

  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
  const data = imageData.data;
  const alpha = Math.round(clamp(opacity, 0.2, 0.95) * 255);

  for (let index = 0; index < data.length; index += 4) {
    if (isFiveLevelReflectivityPixel(data[index], data[index + 1], data[index + 2])) {
      data[index + 3] = alpha;
    } else {
      data[index + 3] = 0;
    }
  }
  context.putImageData(imageData, 0, 0);

  return await canvasToObjectUrl(canvas, "Radar mask could not be created");
}

async function canvasSoftenRegnradarCoverageToObjectUrl(
  imageUrl,
  precipitationOpacity,
  coverageOpacity
) {
  const image = await loadImage(imageUrl);
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth || image.width;
  canvas.height = image.naturalHeight || image.height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) throw new Error("Canvas is unavailable");

  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const imageData = context.getImageData(0, 0, canvas.width, canvas.height);
  const data = imageData.data;
  const precipitationScale = clamp(precipitationOpacity, 0.2, 1);
  const coverageScale = clamp(coverageOpacity, 0.12, 0.8);

  for (let index = 0; index < data.length; index += 4) {
    const alpha = data[index + 3];
    if (alpha === 0) continue;

    if (isRegnradarPrecipitationPixel(data[index], data[index + 1], data[index + 2])) {
      data[index + 3] = Math.round(alpha * precipitationScale);
    } else if (
      isRegnradarCoverageShadePixel(data[index], data[index + 1], data[index + 2], alpha)
    ) {
      data[index + 3] = Math.round(alpha * coverageScale);
    } else {
      data[index + 3] = Math.round(alpha * Math.max(coverageScale, 0.5));
    }
  }
  context.putImageData(imageData, 0, 0);

  return await canvasToObjectUrl(canvas, "Radar coverage overlay could not be created");
}

class RainRadarCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._hass = null;
    this._frames = [];
    this._activeFrame = 0;
    this._playing = false;
    this._timer = null;
    this._frameEntryId = null;
    this._location = null;
    this._radarBounds = { ...DEFAULT_RADAR_BOUNDS };
    this._radarMetadata = {};
    this._defaultModeApplied = false;
    this._layoutRendered = false;
    this._map = null;
    this._tileLayer = null;
    this._outsideCoverageLayer = null;
    this._outsideCoverageKey = "";
    this._mapContainer = null;
    this._mapLocationKey = "";
    this._mapConfigKey = "";
    this._mapUpdateToken = 0;
    this._overlayToken = 0;
    this._radarLayer = null;
    this._radarLayerKey = "";
    this._overlayCache = new Map();
    this._overlayCacheGeneration = 0;
    this._locationMarker = null;
    this._forecastMarker = null;
    this._userMovedMap = false;
    this._expanded = false;
  }

  static getStubConfig() {
    return {
      type: `custom:${CARD_TYPE}`,
      entity: "binary_sensor.home_rain_soon",
      title: "Risk för regn",
      show_icon: true,
      show_map: true,
      show_timeline: true,
      show_legend: false,
      show_info_panel: false,
      show_location_marker: true,
      show_forecast: true,
      map_zoom_controls: true,
      map_scroll_wheel: false,
      tile_url: DEFAULT_TILE_URL,
      default_zoom: 6,
      meta_order: DEFAULT_META_ORDER,
    };
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TYPE);
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Please define a Rain Radar entity.");
    }
    this._config = this._normalizeConfig(config);
    this._expanded = false;
    this._layoutRendered = false;
    this._destroyMap();
    this._ensureLayout();
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    const stateObj = hass.states[this._config?.entity];
    const entryId = stateObj?.attributes?.rain_radar_entry_id;
    if (entryId && entryId !== this._frameEntryId) {
      this._frameEntryId = entryId;
      this._loadFrames(entryId);
    }
    this._ensureLayout();
    this._update();
  }

  disconnectedCallback() {
    this._stop();
    this._destroyMap();
  }

  getCardSize() {
    return this._expanded ? 6 : 2;
  }

  getGridOptions() {
    return {
      columns: 12,
      min_columns: 1,
      max_columns: 12,
      min_rows: 1,
    };
  }

  async _loadFrames(entryId) {
    if (!this._hass?.callApi) return;
    try {
      const payload = await this._hass.callApi("GET", `rain_radar/${entryId}/frames`);
      this._frames = Array.isArray(payload.frames) ? payload.frames : [];
      this._location = locationFromPayload(payload.location);
      this._radarBounds = boundsFromPayload(payload.bounds);
      this._radarMetadata = {
        attribution: payload.attribution || DEFAULT_RADAR_ATTRIBUTION,
        coverageStatus: payload.coverage_status,
        expiresAt: payload.expires_at,
        imageSize: payload.image_size || null,
        productId: payload.product_id || "5level_reflectivity",
        projectionId: payload.projection_id || "",
        overlayMode: payload.overlay_mode || "precipitation_mask",
        colorScale: Array.isArray(payload.color_scale) ? payload.color_scale : [],
        isStale: payload.is_stale === true,
      };
      this._activeFrame = Math.max(0, this._frames.length - 1);
      this._preloadFrames();
      this._update();
      if (
        !this._defaultModeApplied &&
        this._config?.default_animation_mode === "playing" &&
        this._context().timelineFrames.length > 1
      ) {
        this._defaultModeApplied = true;
        this._start();
      }
    } catch (error) {
      this._frames = [];
      this._location = null;
      this._radarBounds = { ...DEFAULT_RADAR_BOUNDS };
      this._radarMetadata = {};
      this._update();
    }
  }

  _preloadFrames() {
    if (typeof Image === "undefined") return;
    this._frames.slice(-4).forEach((frame) => {
      if (!frame?.image_url) return;
      const image = new Image();
      image.decoding = "async";
      image.src = frame.image_url;
    });
  }

  _setActiveFrame(index) {
    const context = this._context();
    const count = context.timelineFrames.length;
    if (!count) return;
    const next = (index + count) % count;
    if (next === this._activeFrame) return;
    this._activeFrame = next;
    const nextContext = this._context();
    this._syncFrameUi(nextContext.timelineFrames[next], nextContext);
    if (this._isMapVisible()) {
      this._preloadUpcomingOverlays(nextContext);
      this._scheduleMapUpdate(nextContext);
    }
  }

  _start() {
    if (this._context().timelineFrames.length <= 1 || this._playing) return;
    this._playing = true;
    if (this._isMapVisible()) {
      this._preloadUpcomingOverlays(this._context());
    }
    this._timer = window.setInterval(() => {
      this._setActiveFrame(this._activeFrame + 1);
    }, this._animationIntervalMs());
    this._update();
  }

  _togglePlay() {
    if (this._playing) {
      this._stop();
      this._update();
      return;
    }
    this._start();
  }

  _stop() {
    this._playing = false;
    if (this._timer) {
      window.clearInterval(this._timer);
      this._timer = null;
    }
  }

  _step(delta) {
    this._setActiveFrame(this._activeFrame + delta);
  }

  _recenter() {
    const context = this._context();
    if (!this._map || !context.location) return;
    this._userMovedMap = false;
    this._map.setView(
      [context.location.latitude, context.location.longitude],
      this._defaultZoom()
    );
  }

  _context() {
    const hass = this._hass;
    const main = hass?.states?.[this._config?.entity];
    const entryId = main?.attributes?.rain_radar_entry_id;
    const precipitation = entryId
      ? findEntityBySuffix(hass, entryId, "_precipitation_now")
      : null;
    const arrival = entryId ? findEntityBySuffix(hass, entryId, "_rain_arrival") : null;
    const risk = entryId ? findEntityBySuffix(hass, entryId, "_rain_risk_12h") : null;
    const provider = entryId ? findEntityBySuffix(hass, entryId, "_provider") : null;
    const radarTime = entryId
      ? findEntityBySuffix(hass, entryId, "_latest_radar_time")
      : null;
    const coverage = entryId
      ? findEntityBySuffix(hass, entryId, "_radar_coverage")
      : null;
    const forecastWindow = clamp(
      Number(this._config?.forecast_minutes || DEFAULT_CONFIG.forecast_minutes),
      15,
      180
    );
    const threshold = Number(precipitation?.attributes?.rain_threshold ?? 0.1);
    const arrivalState = stateText(arrival, null);
    const arrivalMinutes = arrival
      ? minuteValue(arrivalState)
      : arrivalMinutesFromForecastSamples(precipitation, threshold);
    const forecastData = this._config?.show_forecast
      ? forecastSamples(precipitation, forecastWindow)
      : [];
    const timelineFrames = buildTimelineFrames(this._frames);
    if (this._activeFrame >= timelineFrames.length) {
      this._activeFrame = Math.max(0, timelineFrames.length - 1);
    }
    const activeFrame = timelineFrames[this._activeFrame] || null;

    return {
      main,
      entryId,
      precipitation,
      arrival,
      arrivalMinutes,
      risk,
      provider,
      radarTime,
      coverage,
      timelineFrames,
      activeFrame,
      forecastWindow,
      forecastMinutesAvailable: availableForecastMinutes(forecastData),
      threshold,
      isUnavailable: !main || main.state === "unavailable",
      rainingSoon: main?.state === "on",
      coverageOk: coverage?.state === "on",
      height: Math.max(300, Number(this._config?.height || DEFAULT_CONFIG.height)),
      location: this._location,
      radarBounds: this._radarBounds,
      radarMetadata: this._radarMetadata,
    };
  }

  _layoutTemplate() {
    return `
      <style>
        :host {
          --rain-radar-bg-strong: 20%;
          --rain-radar-bg-soft: 10%;
          --rain-radar-border-radius: 8px;
          --rain-radar-outer-padding: 0px;
          display: block;
          color: var(--primary-text-color);
        }

        [hidden] {
          display: none !important;
        }

        ha-card {
          padding: 0;
          background: transparent;
          box-shadow: none;
          border: none;
          --ha-card-background: transparent;
          --ha-card-border-width: 0;
          --ha-card-border-color: transparent;
        }

        .alerts {
          display: flex;
          flex-direction: column;
          gap: 8px;
          padding: 0 var(--rain-radar-outer-padding, 0px);
        }

        .alert {
          display: grid;
          grid-template-columns: auto 1fr;
          gap: 12px;
          align-items: start;
          padding: 12px;
          border-radius: var(--rain-radar-border-radius, 8px);
          border: 1px solid var(--divider-color);
          background: var(--card-background-color);
          position: relative;
          z-index: 0;
          isolation: isolate;
        }

        .alert.bg-severity {
          background: linear-gradient(
            90deg,
            color-mix(in srgb, var(--rain-radar-accent) var(--rain-radar-bg-strong, 20%), var(--card-background-color)) 0%,
            color-mix(in srgb, var(--rain-radar-accent) var(--rain-radar-bg-soft, 10%), var(--card-background-color)) 55%,
            var(--card-background-color) 100%
          );
        }

        .alert::before {
          content: "";
          position: absolute;
          left: 0;
          top: 0;
          bottom: 0;
          width: 4px;
          border-top-left-radius: inherit;
          border-bottom-left-radius: inherit;
          background: var(--rain-radar-accent, var(--primary-color));
        }

        .alert.rain-on {
          --rain-radar-accent: var(--rain-radar-rain, var(--primary-color, #03a9f4));
        }

        .alert.rain-off {
          --rain-radar-accent: var(--rain-radar-dry, #78909c);
        }

        .alert.rain-unknown {
          --rain-radar-accent: var(--warning-color, #ffb300);
        }

        .icon {
          width: 32px;
          height: 32px;
          margin-inline-start: 4px;
          margin-top: 2px;
          color: var(--rain-radar-accent, var(--primary-color));
        }

        .icon-col {
          display: flex;
          align-items: flex-start;
        }

        .content {
          display: flex;
          flex-direction: column;
          gap: 4px;
          min-width: 0;
          align-self: stretch;
        }

        .title {
          display: flex;
          gap: 8px;
          align-items: center;
          min-width: 0;
        }

        .district {
          font-weight: 600;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          flex: 1 1 auto;
          min-width: 0;
        }

        .meta {
          color: var(--secondary-text-color);
          font-size: 0.9em;
          display: flex;
          flex-wrap: wrap;
          gap: 8px 12px;
          min-width: 0;
        }

        .meta span {
          min-width: 0;
          overflow-wrap: anywhere;
        }

        .details {
          margin-top: 6px;
        }

        .details-content {
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .details-toggle {
          color: var(--primary-color);
          cursor: pointer;
          user-select: none;
          font-size: 0.9em;
          white-space: nowrap;
        }

        .toggle-col {
          display: flex;
          justify-content: flex-end;
          align-items: center;
          margin-left: auto;
        }

        .radar {
          min-height: 300px;
          overflow: hidden;
          position: relative;
          background: color-mix(in srgb, var(--primary-background-color, #111) 18%, #667078);
          border: 1px solid var(--divider-color);
          border-radius: var(--rain-radar-border-radius, 8px);
          isolation: isolate;
        }

        .radar-map {
          height: 100%;
          min-height: 300px;
          position: relative;
          width: 100%;
          z-index: 0 !important;
        }

        .radar-map.leaflet-container {
          z-index: 0 !important;
          background: #73808a;
          font: inherit;
        }

        .radar-map .leaflet-top,
        .radar-map .leaflet-bottom,
        .radar-map .leaflet-control {
          z-index: 1000 !important;
        }

        .radar-map .leaflet-control-zoom {
          box-shadow: none;
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          overflow: hidden;
          margin: 10px;
        }

        .radar-map .leaflet-control-zoom a {
          background: color-mix(in srgb, var(--card-background-color, #fff) 92%, transparent);
          color: var(--primary-text-color);
          border-bottom: 1px solid var(--divider-color);
        }

        .radar-map .leaflet-control-zoom a:last-child {
          border-bottom: none;
        }

        .radar-map .leaflet-control-attribution {
          display: block;
          max-width: calc(100% - 16px);
          margin: 0;
          padding: 2px 6px;
          overflow-wrap: anywhere;
          font-size: 10px;
          line-height: 1.2;
          color: var(--secondary-text-color);
          background: color-mix(in srgb, var(--card-background-color, #fff) 94%, transparent);
        }

        .radar-map .leaflet-control-attribution a {
          color: inherit;
          text-decoration: underline;
        }

        .radar-map.hide-provider-legend .leaflet-control-attribution {
          max-width: min(70%, 520px);
        }

        .rain-radar-overlay {
          image-rendering: auto;
          transition: opacity 360ms ease;
          will-change: opacity;
        }

        .rain-radar-outside-coverage {
          transition: fill-opacity 180ms ease;
        }

        .rain-location-pin {
          height: 34px;
          width: 34px;
        }

        .rain-location-pin::before {
          animation: marker-pulse 1800ms ease-out infinite;
          background: rgba(3, 169, 244, 0.24);
          border-radius: 999px;
          content: "";
          height: 26px;
          left: 4px;
          position: absolute;
          top: 4px;
          width: 26px;
        }

        .rain-location-pin::after {
          background: var(--primary-color, #03a9f4);
          border: 3px solid white;
          border-radius: 999px 999px 999px 2px;
          box-shadow: 0 2px 12px rgba(0,0,0,0.35);
          content: "";
          height: 16px;
          left: 7px;
          position: absolute;
          top: 4px;
          transform: rotate(-45deg);
          width: 16px;
        }

        .rain-location-pin span {
          background: white;
          border-radius: 999px;
          height: 5px;
          left: 14px;
          position: absolute;
          top: 11px;
          width: 5px;
          z-index: 1;
        }

        @keyframes marker-pulse {
          0% { opacity: 0.65; transform: scale(0.55); }
          100% { opacity: 0; transform: scale(2.1); }
        }

        .map-status,
        .message {
          align-items: center;
          color: white;
          display: flex;
          inset: 0;
          justify-content: center;
          padding: 18px;
          pointer-events: none;
          position: absolute;
          text-align: center;
          z-index: 2;
        }

        .map-status {
          background: color-mix(in srgb, var(--card-background-color, #fff) 82%, transparent);
          color: var(--secondary-text-color);
          opacity: 0;
          transition: opacity 160ms ease;
        }

        .map-status.show {
          opacity: 1;
        }

        .message {
          background: rgba(0,0,0,0.16);
        }

        .map-tools {
          display: grid;
          gap: 8px;
          position: absolute;
          right: 12px;
          top: 12px;
          z-index: 2;
        }

        .map-tool {
          backdrop-filter: blur(8px);
          background: color-mix(in srgb, var(--card-background-color, #fff) 88%, transparent);
          box-shadow: 0 2px 12px rgba(0,0,0,0.20);
        }

        .info-panel {
          backdrop-filter: blur(10px);
          background: rgba(12, 23, 26, 0.64);
          border: 1px solid rgba(255,255,255,0.18);
          border-radius: 8px;
          bottom: 12px;
          color: white;
          left: 12px;
          max-width: min(72%, 360px);
          padding: 10px 12px;
          position: absolute;
          z-index: 2;
        }

        .info-panel strong {
          display: block;
          font-size: 0.92rem;
          line-height: 1.2;
          margin-bottom: 3px;
        }

        .info-panel span {
          color: rgba(255,255,255,0.82);
          display: block;
          font-size: 0.78rem;
          line-height: 1.35;
        }

        .timeline {
          display: grid;
          gap: 8px;
        }

        .timeline-head {
          align-items: baseline;
          display: flex;
          gap: 10px;
          justify-content: space-between;
        }

        .timeline-phase {
          color: var(--primary-text-color);
          font-size: 0.82rem;
          font-weight: 700;
        }

        .timeline-detail {
          color: var(--secondary-text-color);
          font-size: 0.76rem;
          text-align: right;
        }

        .timeline-band {
          display: grid;
          min-height: 18px;
          overflow: hidden;
          border-radius: 5px;
          border: 1px solid var(--divider-color);
          background: var(--secondary-background-color);
        }

        .timeline-band.has-forecast {
          box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--warning-color, #ffb300) 32%, transparent);
        }

        .timeline-segment {
          align-items: center;
          display: flex;
          min-width: 0;
          padding: 0 8px;
          font-size: 0.68rem;
          line-height: 18px;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .timeline-segment.observed {
          background: color-mix(in srgb, var(--secondary-background-color, #f4f4f4) 78%, #607d8b);
          color: var(--secondary-text-color);
        }

        .timeline-segment.forecast {
          background:
            repeating-linear-gradient(
              135deg,
              color-mix(in srgb, var(--warning-color, #ffb300) 34%, transparent) 0,
              color-mix(in srgb, var(--warning-color, #ffb300) 34%, transparent) 6px,
              color-mix(in srgb, var(--primary-color, #03a9f4) 18%, transparent) 6px,
              color-mix(in srgb, var(--primary-color, #03a9f4) 18%, transparent) 12px
            );
          border-left: 1px solid color-mix(in srgb, var(--warning-color, #ffb300) 52%, transparent);
          color: var(--primary-text-color);
          font-weight: 650;
          justify-content: center;
        }

        .timeline-segment.forecast::before {
          background: var(--warning-color, #ffb300);
          border-radius: 999px;
          content: "";
          flex: 0 0 auto;
          height: 7px;
          margin-right: 6px;
          width: 7px;
        }

        .controls {
          align-items: center;
          display: grid;
          gap: 10px;
          grid-template-columns: auto auto auto minmax(0, 1fr) auto;
        }

        button {
          align-items: center;
          background: var(--secondary-background-color);
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          color: var(--primary-text-color);
          cursor: pointer;
          display: inline-flex;
          height: 36px;
          justify-content: center;
          min-width: 36px;
          padding: 0 10px;
        }

        button:hover {
          background: color-mix(in srgb, var(--secondary-background-color) 74%, var(--primary-color));
        }

        button:disabled {
          cursor: default;
          opacity: 0.45;
        }

        input[type="range"] {
          accent-color: var(--primary-color);
          width: 100%;
        }

        .frame-time {
          color: var(--secondary-text-color);
          font-size: 0.78rem;
          min-width: 54px;
          text-align: right;
          white-space: nowrap;
        }

        @media (max-width: 520px) {
          .alert {
            gap: 10px;
            padding: 12px 10px;
          }

          .timeline-head {
            align-items: flex-start;
            flex-direction: column;
            gap: 2px;
          }

          .timeline-detail {
            text-align: left;
          }

          .controls {
            grid-template-columns: auto auto auto minmax(0, 1fr);
          }

          .frame-time {
            grid-column: 1 / -1;
            text-align: left;
          }
        }
      </style>
      <ha-card>
        <div class="alerts">
          <div class="alert rain-unknown">
            <div class="icon-col">
              <ha-icon class="icon" icon="mdi:weather-cloudy-alert"></ha-icon>
            </div>
            <div class="content">
              <div class="title">
                <div class="district"></div>
                <div class="toggle-col">
                  <div
                    class="details-toggle"
                    role="button"
                    tabindex="0"
                    aria-expanded="false"
                  ></div>
                </div>
              </div>
              ${this._metaSectionHtml("inline")}
              <div class="details" hidden>
                <div class="details-content">
                  ${this._metaSectionHtml("details")}
                </div>
              </div>
            </div>
          </div>
        </div>
      </ha-card>
    `;
  }

  _metaSectionHtml(section) {
    const { inlineKeys, detailsKeys } = splitMetaOrder(this._config?.meta_order);
    const keys = section === "details" ? detailsKeys : inlineKeys;
    const blocks = [];
    let metaSpans = [];
    const flushMeta = () => {
      if (!metaSpans.length) return;
      blocks.push(`<div class="meta">${metaSpans.join("")}</div>`);
      metaSpans = [];
    };

    keys.forEach((key) => {
      if (key === "divider") return;
      if (key === "map") {
        flushMeta();
        blocks.push(this._mapBlockHtml());
        return;
      }
      if (key === "timeline") {
        flushMeta();
        blocks.push(this._timelineBlockHtml());
        return;
      }
      metaSpans.push(this._metaSpanHtml(key));
    });
    flushMeta();
    return blocks.join("");
  }

  _metaSpanHtml(key) {
    return `
      <span data-meta-key="${key}">
        <b>${escapeHtml(this._metaLabel(key))}:</b>
        <span class="meta-value ${key}"></span>
      </span>
    `;
  }

  _mapBlockHtml() {
    return `
      <div class="radar" data-meta-key="map">
        <div class="radar-map"></div>
        <div class="map-tools">
          <button type="button" class="map-tool recenter" title="${escapeHtml(this._t("recenter_map"))}">
            <ha-icon icon="mdi:crosshairs-gps"></ha-icon>
          </button>
        </div>
        <div class="map-status"></div>
        <div class="message">
          <div>${escapeHtml(this._t("data_unavailable"))}</div>
        </div>
        <div class="info-panel">
          <strong></strong>
          <span class="info-status"></span>
          <span class="info-attribution"></span>
        </div>
      </div>
    `;
  }

  _timelineBlockHtml() {
    return `
      <div class="timeline" data-meta-key="timeline">
        <div class="timeline-head">
          <div>
            <div class="timeline-phase"></div>
            <div class="timeline-detail"></div>
          </div>
          <div class="frame-time"></div>
        </div>
        <div class="timeline-band">
          <div class="timeline-segment observed">Radar</div>
          <div class="timeline-segment forecast">Forecast</div>
        </div>
        <div class="controls">
          <button type="button" class="play" title="${escapeHtml(this._t("play"))}">
            <ha-icon icon="mdi:play"></ha-icon>
          </button>
          <button type="button" class="step-back" title="${escapeHtml(this._t("previous_frame"))}">
            <ha-icon icon="mdi:step-backward"></ha-icon>
          </button>
          <button type="button" class="step-forward" title="${escapeHtml(this._t("next_frame"))}">
            <ha-icon icon="mdi:step-forward"></ha-icon>
          </button>
          <input type="range" min="0" max="0" value="0" aria-label="${escapeHtml(this._t("timeline"))}">
        </div>
      </div>
    `;
  }

  _bindLayoutEvents() {
    this.shadowRoot.querySelector(".play")?.addEventListener("click", () => this._togglePlay());
    this.shadowRoot.querySelector(".step-back")?.addEventListener("click", () => this._step(-1));
    this.shadowRoot.querySelector(".step-forward")?.addEventListener("click", () => this._step(1));
    this.shadowRoot.querySelector(".recenter")?.addEventListener("click", () => this._recenter());
    this.shadowRoot.querySelector("input[type='range']")?.addEventListener("input", (event) => {
      this._setActiveFrame(Number(event.target.value));
    });
    const detailsToggle = this.shadowRoot.querySelector(".details-toggle");
    detailsToggle?.addEventListener("click", (event) => this._toggleDetails(event));
    detailsToggle?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      this._toggleDetails(event);
    });
  }

  _ensureLayout() {
    if (!this.shadowRoot || !this._config || this._layoutRendered) return;
    this.shadowRoot.innerHTML = this._layoutTemplate();
    this._bindLayoutEvents();
    this._layoutRendered = true;
  }

  _update() {
    if (!this.shadowRoot || !this._config || !this._layoutRendered) return;
    const context = this._context();
    const {
      main,
      precipitation,
      arrival,
      risk,
      provider,
      radarTime,
      activeFrame,
      timelineFrames,
      forecastWindow,
      threshold,
      isUnavailable,
      rainingSoon,
      coverageOk,
      height,
      radarMetadata,
    } = context;

    const status = rainSoonStatus(main);
    const statusLabel = this._t(status.labelKey);
    const alert = this.shadowRoot.querySelector(".alert");
    alert.classList.toggle("rain-on", status.className === "rain-on");
    alert.classList.toggle("rain-off", status.className === "rain-off");
    alert.classList.toggle("rain-unknown", status.className === "rain-unknown");
    alert.classList.toggle("bg-severity", this._config.severity_background === true);

    const iconColumn = this.shadowRoot.querySelector(".icon-col");
    iconColumn.hidden = this._config.show_icon === false;
    const icon = this.shadowRoot.querySelector(".icon");
    icon?.setAttribute("icon", status.icon);

    const title = this._config.title || this._t("default_title");
    this.shadowRoot.querySelector(".district").textContent = title;

    const staleText = radarMetadata?.isStale ? ` ${this._t("stale_radar")}` : "";
    const locationName = locationNameFromEntity(main);
    const providerName = stateText(provider, "MET Norway");
    const coverageText = coverageOk ? this._t("coverage_active") : this._t("coverage_unknown");
    const latestRadar = latestRadarText(timelineFrames, radarTime);
    const forecastText =
      this._config.show_forecast && context.forecastMinutesAvailable
        ? `${this._t("forecast_data")} +${context.forecastMinutesAvailable} min`
        : this._t("forecast_unavailable");

    this._setMetaValue("status", statusLabel);
    this._setMetaValue(
      "precipitation",
      numberText(
      stateText(precipitation, null),
      " mm/h"
      )
    );
    this._setMetaValue("arrival", arrivalText(
      context.arrivalMinutes,
      this._config.arrival_format
    ));
    this._setMetaValue("risk", numberText(
      stateText(risk, null),
      "%"
    ));
    this._setMetaValue("latest_radar", latestRadar || this._t("unknown"));
    this._setMetaValue("coverage", `${coverageText}${staleText}`);
    this._setMetaValue("provider", locationName ? `${locationName} - ${providerName}` : providerName);
    this._setMetaValue("forecast", forecastText);
    this._setMetaVisibility(context, { latestRadar });
    this._syncDetailsState(context, { latestRadar });

    const radar = this.shadowRoot.querySelector(".radar");
    radar.style.height = `${height}px`;
    this.shadowRoot.querySelector(".message").hidden = !isUnavailable;
    this.shadowRoot.querySelector(".info-panel").hidden = !this._config.show_info_panel;
    this.shadowRoot.querySelector(".info-panel strong").textContent =
      activeFrame?.kind === "forecast" ? "MET Norway nowcast forecast" : "MET Norway radar";
    this.shadowRoot.querySelector(".info-status").textContent =
      activeFrame?.kind === "forecast"
        ? forecastSummary(activeFrame, threshold) || "Forecast point sample"
        : rainingSoon
          ? "Rain is expected inside the configured window."
          : "No rain is expected inside the configured window.";
    this.shadowRoot.querySelector(".info-attribution").textContent =
      provider?.attributes?.attribution ||
      main?.attributes?.attribution ||
      radarMetadata?.attribution ||
      this._t("data_from_met");

    this._syncFrameUi(activeFrame, context);
    this._syncTimelineBand(context);
    if (this._isMapVisible()) {
      this._preloadUpcomingOverlays(context);
      this._scheduleMapUpdate(context);
    } else if (this._map) {
      this._destroyMap();
    }
  }

  _setMetaValue(key, value) {
    this.shadowRoot.querySelectorAll(`[data-meta-key="${key}"] .meta-value`).forEach((element) => {
      element.textContent = value;
    });
  }

  _setMetaVisibility(context, dynamic = {}) {
    META_KEYS.forEach((key) => {
      const visible = this._isMetaShown(key) && this._hasMetaContent(key, context, dynamic);
      this.shadowRoot.querySelectorAll(`[data-meta-key="${key}"]`).forEach((element) => {
        element.hidden = !visible;
      });
    });

    this.shadowRoot.querySelectorAll(".meta").forEach((group) => {
      const visibleChildren = Array.from(group.querySelectorAll("[data-meta-key]")).some(
        (element) => !element.hidden
      );
      group.hidden = !visibleChildren;
    });
  }

  _hasMetaContent(key, context, dynamic = {}) {
    if (key === "latest_radar") return !!dynamic.latestRadar;
    if (key === "map") return true;
    if (key === "timeline") return (context.timelineFrames || []).length > 0;
    return true;
  }

  _syncDetailsState(context, dynamic = {}) {
    const details = this.shadowRoot.querySelector(".details");
    const toggle = this.shadowRoot.querySelector(".details-toggle");
    if (!details || !toggle) return;

    const { detailsKeys } = splitMetaOrder(this._config?.meta_order);
    const expandable = detailsKeys.some((key) =>
      this._isMetaShown(key) && this._hasMetaContent(key, context, dynamic)
    );
    if (!expandable) this._expanded = false;

    details.hidden = !expandable || !this._expanded;
    toggle.hidden = !expandable;
    toggle.textContent = this._expanded ? this._t("hide_details") : this._t("show_details");
    toggle.title = toggle.textContent;
    toggle.setAttribute("aria-expanded", String(this._expanded));
  }

  _toggleDetails(event) {
    event?.stopPropagation?.();
    this._expanded = !this._expanded;
    this._update();
  }

  _isMetaShown(key) {
    const configKey = META_CONFIG_KEYS[key];
    if (!configKey) return true;
    if (
      ["status", "arrival", "precipitation", "risk", "latest_radar"].includes(key) &&
      this._config.show_status_strip === false
    ) {
      return false;
    }
    return this._config[configKey] !== false;
  }

  _isMetaInVisibleSection(key) {
    const { inlineKeys, detailsKeys } = splitMetaOrder(this._config?.meta_order);
    return inlineKeys.includes(key) || (this._expanded && detailsKeys.includes(key));
  }

  _isMapVisible() {
    return this._isMetaShown("map") && this._isMetaInVisibleSection("map");
  }

  _metaLabel(key) {
    const labels = {
      status: this._t("status"),
      arrival: this._t("arrival"),
      precipitation: this._t("precipitation"),
      risk: this._t("risk"),
      latest_radar: this._t("latest_radar"),
      coverage: this._t("coverage"),
      provider: this._t("provider"),
      forecast: this._t("forecast"),
      timeline: this._t("timeline"),
      map: this._t("map"),
    };
    return labels[key] || key;
  }

  _syncFrameUi(activeFrame, context) {
    if (!this.shadowRoot) return;
    const timelineFrames = context.timelineFrames || [];
    const hasTimeline = this._config.show_timeline && timelineFrames.length > 1;
    const forecastDetail =
      this._config.show_forecast && context.forecastMinutesAvailable
        ? `Forecast data +${context.forecastMinutesAvailable} min`
        : "";
    const detail = forecastSummary(activeFrame, context.threshold) || forecastDetail;
    this.shadowRoot.querySelector(".timeline-phase").textContent = framePhase(activeFrame);
    this.shadowRoot.querySelector(".timeline-detail").textContent = detail;
    this.shadowRoot.querySelector(".frame-time").textContent = frameLabel(
      activeFrame,
      context.radarTime?.state
    );

    const input = this.shadowRoot.querySelector("input[type='range']");
    input.max = String(Math.max(0, timelineFrames.length - 1));
    input.value = String(this._activeFrame);
    input.disabled = !hasTimeline;

    const playButton = this.shadowRoot.querySelector(".play");
    playButton.disabled = !hasTimeline;
    playButton.title = this._playing ? "Pause" : "Play";
    playButton.querySelector("ha-icon")?.setAttribute(
      "icon",
      this._playing ? "mdi:pause" : "mdi:play"
    );
    this.shadowRoot.querySelector(".step-back").disabled = !hasTimeline;
    this.shadowRoot.querySelector(".step-forward").disabled = !hasTimeline;
  }

  _syncTimelineBand(context) {
    const band = this.shadowRoot.querySelector(".timeline-band");
    const timelineFrames = context.timelineFrames || [];
    const observed = timelineFrames.length;
    const forecastMinutes = Number(context.forecastMinutesAvailable) || 0;
    const hasForecast = this._config.show_forecast && forecastMinutes > 0;
    band.hidden = !this._config.show_timeline || (observed < 2 && !hasForecast);
    if (band.hidden) return;
    band.classList.toggle("has-forecast", hasForecast);
    band.style.gridTemplateColumns = hasForecast
      ? `${Math.max(2, observed)}fr minmax(112px, 0.36fr)`
      : "1fr";
    const observedSegment = band.querySelector(".observed");
    observedSegment.hidden = observed === 0;
    observedSegment.textContent = observed > 1 ? "Radar frames" : "Radar";
    const forecastSegment = band.querySelector(".forecast");
    forecastSegment.hidden = !hasForecast;
    forecastSegment.textContent = `Forecast data +${forecastMinutes} min`;
    forecastSegment.title = "Point forecast data, not radar imagery";
  }

  _scheduleMapUpdate(context) {
    const token = ++this._mapUpdateToken;
    this._ensureLeafletAndRenderMap(context, token).catch((error) => {
      if (token !== this._mapUpdateToken) return;
      this._showMapStatus("Map failed to load");
      // eslint-disable-next-line no-console
      console.debug("Rain Radar map failed to load", error);
    });
  }

  async _ensureLeafletAndRenderMap(context, token) {
    const container = this.shadowRoot?.querySelector(".radar-map");
    if (!container) return;

    container.classList.toggle("hide-provider-legend", !this._config.show_legend);
    this._ensureLeafletCssInShadowRoot();
    if (!this._map) this._showMapStatus("Loading map");

    const L = await this._ensureLeaflet();
    if (token !== this._mapUpdateToken) return;

    const location = context.location;
    const currentLocationKey = locationKey(location);
    const currentMapConfigKey = this._mapConfigKeyForConfig();
    if (
      !this._map ||
      this._mapContainer !== container ||
      this._mapConfigKey !== currentMapConfigKey
    ) {
      this._destroyMap();
      this._map = L.map(container, {
        minZoom: this._minZoom(),
        maxZoom: this._maxZoom(),
        zoomSnap: 0.25,
        zoomDelta: 0.5,
        zoomControl: this._config.map_zoom_controls !== false,
        attributionControl: true,
        scrollWheelZoom: this._config.map_scroll_wheel === true,
        doubleClickZoom: true,
        dragging: true,
        touchZoom: true,
        boxZoom: false,
        keyboard: false,
        tap: false,
      });
      this._syncTileLayer(L);
      this._map.attributionControl.addAttribution(
        context.radarMetadata?.attribution || DEFAULT_RADAR_ATTRIBUTION
      );
      if (location) {
        this._map.setView([location.latitude, location.longitude], this._defaultZoom());
      } else {
        this._map.fitBounds(leafletBounds(context.radarBounds), { padding: [12, 12] });
      }
      this._map.on("dragstart zoomstart", () => {
        this._userMovedMap = true;
      });
      this._mapContainer = container;
      this._mapLocationKey = currentLocationKey;
      this._mapConfigKey = currentMapConfigKey;
      this._userMovedMap = false;
    } else {
      this._syncTileLayer(L);
      if (this._mapLocationKey !== currentLocationKey) {
        this._mapLocationKey = currentLocationKey;
        if (location && !this._userMovedMap) {
          this._map.setView([location.latitude, location.longitude], this._defaultZoom());
        }
      }
    }

    this._syncOutsideCoverageLayer(L, context);
    this._syncLeafletMarker(L, context);
    await this._syncRadarOverlay(L, context);

    requestAnimationFrame(() => {
      try {
        this._map?.invalidateSize();
      } catch (error) {
        // Ignore transient Leaflet sizing errors while HA is laying out cards.
      }
    });

    this._hideMapStatus();
  }

  _syncTileLayer(L) {
    if (!this._map || !this._config.tile_url) return;
    if (this._tileLayer && this._tileLayer._rainRadarTileUrl === this._config.tile_url) {
      return;
    }
    if (this._tileLayer) {
      this._tileLayer.remove();
      this._tileLayer = null;
    }
    this._tileLayer = L.tileLayer(this._config.tile_url, {
      minZoom: this._minZoom(),
      maxZoom: this._maxZoom(),
      attribution: this._config.tile_attribution || DEFAULT_TILE_ATTRIBUTION,
      detectRetina: true,
      referrerPolicy: MAP_TILE_REFERRER_POLICY,
    }).addTo(this._map);
    this._tileLayer._rainRadarTileUrl = this._config.tile_url;
    this._tileLayer.on("tileerror", () => {
      this._showMapStatus("Map tiles unavailable");
    });
  }

  _syncOutsideCoverageLayer(L, context) {
    if (!this._map || !context.radarBounds) return;
    const bounds = context.radarBounds;
    const opacity = this._coverageOpacity();
    const layerKey = `${JSON.stringify(bounds)}|${opacity.toFixed(2)}`;
    if (this._outsideCoverageLayer && this._outsideCoverageKey === layerKey) return;

    this._removeOutsideCoverageLayer();
    const world = WEB_MERCATOR_WORLD_BOUNDS;
    const rectangles = [
      { south: bounds.north, west: world.west, north: world.north, east: world.east },
      { south: world.south, west: world.west, north: bounds.south, east: world.east },
      { south: bounds.south, west: world.west, north: bounds.north, east: bounds.west },
      { south: bounds.south, west: bounds.east, north: bounds.north, east: world.east },
    ]
      .map((rect) => ({
        south: clamp(rect.south, world.south, world.north),
        west: clamp(rect.west, world.west, world.east),
        north: clamp(rect.north, world.south, world.north),
        east: clamp(rect.east, world.west, world.east),
      }))
      .filter((rect) => rect.north > rect.south && rect.east > rect.west);

    this._outsideCoverageLayer = L.layerGroup(
      rectangles.map((rect) =>
        L.rectangle(leafletBounds(rect), {
          stroke: false,
          fill: true,
          fillColor: "#596266",
          fillOpacity: opacity,
          interactive: false,
          className: "rain-radar-outside-coverage",
        })
      )
    ).addTo(this._map);
    this._outsideCoverageKey = layerKey;
  }

  _syncLeafletMarker(L, context) {
    const location = context.location;
    if (this._config.show_location_marker && location) {
      const latLng = [location.latitude, location.longitude];
      if (!this._locationMarker) {
        this._locationMarker = L.marker(latLng, {
          interactive: false,
          icon: L.divIcon({
            className: "rain-location-pin",
            html: "<span></span>",
            iconAnchor: [17, 28],
            iconSize: [34, 34],
          }),
          zIndexOffset: 500,
        }).addTo(this._map);
      } else {
        this._locationMarker.setLatLng(latLng);
      }
    } else if (this._locationMarker) {
      this._locationMarker.remove();
      this._locationMarker = null;
    }

    if (context.activeFrame?.kind === "forecast" && location) {
      const color = precipitationColor(context.activeFrame.rate, context.threshold);
      const latLng = [location.latitude, location.longitude];
      if (!this._forecastMarker) {
        this._forecastMarker = L.circleMarker(latLng, {
          radius: 18,
          color,
          weight: 2,
          fillColor: color,
          fillOpacity: 0.22,
          interactive: false,
        }).addTo(this._map);
      } else {
        this._forecastMarker.setLatLng(latLng);
        this._forecastMarker.setStyle({
          color,
          fillColor: color,
          fillOpacity: 0.22,
        });
      }
    } else if (this._forecastMarker) {
      this._forecastMarker.remove();
      this._forecastMarker = null;
    }
  }

  async _syncRadarOverlay(L, context) {
    const frame = context.activeFrame;
    const imageUrl = frame?.imageUrl || null;
    const desiredOpacity = this._frameRadarOpacity(frame);
    const coverageOpacity = this._coverageOpacity();

    if (!imageUrl) {
      this._removeRadarLayer();
      return;
    }

    const overlayMode = context.radarMetadata?.overlayMode || "precipitation_mask";
    const layerKey = this._overlayLayerKey(
      overlayMode,
      imageUrl,
      desiredOpacity,
      coverageOpacity,
      context.radarBounds
    );
    if (this._radarLayer && this._radarLayerKey === layerKey) {
      this._radarLayer.setOpacity(1);
      return;
    }

    const token = ++this._overlayToken;
    if (!this._radarLayer) {
      this._showMapStatus("Loading radar layer");
    }
    let overlayEntry = null;
    try {
      overlayEntry = await this._prepareOverlayUrl(
        layerKey,
        imageUrl,
        overlayMode,
        desiredOpacity,
        coverageOpacity
      );
    } catch (error) {
      if (token === this._overlayToken && !this._radarLayer) {
        this._showMapStatus("Radar layer unavailable");
        throw error;
      }
      return;
    }
    if (token !== this._overlayToken) {
      return;
    }

    const previousLayer = this._radarLayer;
    const nextLayer = L.imageOverlay(overlayEntry.url, leafletBounds(context.radarBounds), {
      opacity: 0,
      interactive: false,
      className: "rain-radar-overlay",
      attribution: context.radarMetadata?.attribution || DEFAULT_RADAR_ATTRIBUTION,
    });

    const layerLoaded = this._waitForLayerLoad(nextLayer);
    nextLayer.addTo(this._map);
    try {
      await this._withTimeout(
        layerLoaded,
        RADAR_LAYER_LOAD_TIMEOUT_MS,
        "Radar layer render timed out"
      );
    } catch (error) {
      try {
        nextLayer.remove();
      } catch (removeError) {
        // Ignore stale Leaflet layers.
      }
      if (!previousLayer) throw error;
      return;
    }
    if (token !== this._overlayToken) {
      try {
        nextLayer.remove();
      } catch (removeError) {
        // Ignore stale Leaflet layers.
      }
      return;
    }

    const crossfade = () => {
      try {
        nextLayer.setOpacity(1);
        previousLayer?.setOpacity(0);
      } catch (error) {
        // Ignore if Leaflet removed the layer during a fast frame change.
      }
    };
    requestAnimationFrame(crossfade);

    if (previousLayer) {
      try {
        window.setTimeout(() => {
          previousLayer.remove();
        }, 420);
      } catch (error) {
        try {
          previousLayer.remove();
        } catch (removeError) {
          // Ignore stale Leaflet layers.
        }
      }
    }

    this._radarLayer = nextLayer;
    this._radarLayerKey = layerKey;
    this._trimOverlayCache([layerKey]);
  }

  _overlayLayerKey(overlayMode, imageUrl, radarOpacity, coverageOpacity, bounds) {
    return `${overlayMode}|${imageUrl}|${radarOpacity.toFixed(2)}|${coverageOpacity.toFixed(
      2
    )}|${JSON.stringify(bounds)}`;
  }

  async _prepareOverlayUrl(layerKey, imageUrl, overlayMode, radarOpacity, coverageOpacity) {
    const cached = this._overlayCache.get(layerKey);
    if (cached?.url) {
      cached.lastUsed = Date.now();
      return cached;
    }
    if (cached?.promise) {
      return await cached.promise;
    }

    const generation = this._overlayCacheGeneration;
    const promise = (async () => {
      const objectUrl =
        overlayMode === "regnradar_coverage"
          ? await canvasSoftenRegnradarCoverageToObjectUrl(
              imageUrl,
              radarOpacity,
              coverageOpacity
            )
          : await canvasMaskRadarOverlayToObjectUrl(imageUrl, radarOpacity);

      if (generation !== this._overlayCacheGeneration) {
        URL.revokeObjectURL(objectUrl);
        throw new Error("Radar overlay cache was reset");
      }

      const entry = {
        url: objectUrl,
        objectUrl,
        lastUsed: Date.now(),
      };
      this._overlayCache.set(layerKey, entry);
      this._trimOverlayCache([layerKey]);
      return entry;
    })();

    this._overlayCache.set(layerKey, {
      promise,
      lastUsed: Date.now(),
    });
    try {
      return await promise;
    } catch (error) {
      if (this._overlayCache.get(layerKey)?.promise === promise) {
        this._overlayCache.delete(layerKey);
      }
      throw error;
    }
  }

  _preloadUpcomingOverlays(context) {
    const frames = context?.timelineFrames || [];
    if (!frames.length) return;

    const overlayMode = context.radarMetadata?.overlayMode || "precipitation_mask";
    const coverageOpacity = this._coverageOpacity();
    [0, 1, 2, 3].forEach((offset) => {
      const frame = frames[(this._activeFrame + offset) % frames.length];
      if (!frame?.imageUrl) return;
      const radarOpacity = this._frameRadarOpacity(frame);
      const layerKey = this._overlayLayerKey(
        overlayMode,
        frame.imageUrl,
        radarOpacity,
        coverageOpacity,
        context.radarBounds
      );
      if (this._overlayCache.has(layerKey)) return;
      this._prepareOverlayUrl(
        layerKey,
        frame.imageUrl,
        overlayMode,
        radarOpacity,
        coverageOpacity
      ).catch(() => {
        // The visible layer path reports user-facing failures.
      });
    });
  }

  _waitForLayerLoad(layer) {
    return new Promise((resolve, reject) => {
      layer.once("load", resolve);
      layer.once("error", () => reject(new Error("Radar layer failed to render")));
    });
  }

  _trimOverlayCache(keepKeys = []) {
    const keep = new Set([this._radarLayerKey, ...keepKeys].filter(Boolean));
    const removable = Array.from(this._overlayCache.entries())
      .filter(([key, entry]) => !keep.has(key) && entry.objectUrl)
      .sort((a, b) => a[1].lastUsed - b[1].lastUsed);

    while (this._overlayCache.size > RADAR_OVERLAY_CACHE_LIMIT && removable.length) {
      const [key, entry] = removable.shift();
      URL.revokeObjectURL(entry.objectUrl);
      this._overlayCache.delete(key);
    }
  }

  _clearOverlayCache() {
    this._overlayCacheGeneration += 1;
    this._overlayCache.forEach((entry) => {
      if (entry.objectUrl) URL.revokeObjectURL(entry.objectUrl);
    });
    this._overlayCache.clear();
  }

  _removeRadarLayer() {
    this._overlayToken += 1;
    if (this._radarLayer) {
      this._radarLayer.remove();
      this._radarLayer = null;
    }
    this._radarLayerKey = "";
  }

  _removeOutsideCoverageLayer() {
    if (this._outsideCoverageLayer) {
      this._outsideCoverageLayer.remove();
      this._outsideCoverageLayer = null;
    }
    this._outsideCoverageKey = "";
  }

  _ensureLeaflet() {
    window.__rainRadarLeafletPromise = window.__rainRadarLeafletPromise || null;
    if (window.L && window.L.map) return Promise.resolve(window.L);
    if (window.__rainRadarLeafletPromise) return window.__rainRadarLeafletPromise;

    window.__rainRadarLeafletPromise = (async () => {
      try {
        const mod = await this._withTimeout(
          import(LEAFLET_ESM_URL),
          12000,
          "Leaflet ESM import timed out"
        );
        const L = mod?.default || mod?.L || mod;
        if (L && L.map) {
          window.L = window.L || L;
          return L;
        }
        throw new Error("Leaflet ESM loaded but did not expose L.map");
      } catch (err) {
        const jsId = "rain-radar-leaflet-js";
        return await new Promise((resolve, reject) => {
          try {
            if (window.L && window.L.map) {
              resolve(window.L);
              return;
            }
            let script = document.getElementById(jsId);
            if (!script) {
              script = document.createElement("script");
              script.id = jsId;
              script.src = LEAFLET_JS_SRC;
              script.async = true;
              document.head.appendChild(script);
            }
            script.addEventListener("load", () => resolve(window.L));
            script.addEventListener("error", () =>
              reject(err || new Error("Failed to load Leaflet"))
            );
          } catch (error) {
            reject(err || error);
          }
        });
      }
    })();

    return window.__rainRadarLeafletPromise;
  }

  _withTimeout(promise, ms, message) {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error(message || "Timed out")), ms);
      promise
        .then((value) => {
          clearTimeout(timeout);
          resolve(value);
        })
        .catch((error) => {
          clearTimeout(timeout);
          reject(error);
        });
    });
  }

  _ensureLeafletCssInShadowRoot() {
    const id = "rain-radar-leaflet-css-shadow";
    if (!this.shadowRoot || this.shadowRoot.querySelector(`#${id}`)) return;
    const link = document.createElement("link");
    link.id = id;
    link.rel = "stylesheet";
    link.href = LEAFLET_CSS_HREF;
    this.shadowRoot.appendChild(link);
  }

  _destroyMap() {
    this._mapUpdateToken += 1;
    this._overlayToken += 1;
    this._removeRadarLayer();
    this._removeOutsideCoverageLayer();
    this._clearOverlayCache();
    try {
      this._map?.remove?.();
    } catch (error) {
      // Ignore Leaflet cleanup errors while HA removes the card.
    }
    this._map = null;
    this._tileLayer = null;
    this._outsideCoverageLayer = null;
    this._outsideCoverageKey = "";
    this._mapContainer = null;
    this._mapLocationKey = "";
    this._mapConfigKey = "";
    this._locationMarker = null;
    this._forecastMarker = null;
    this._userMovedMap = false;
  }

  _showMapStatus(message) {
    const status = this.shadowRoot?.querySelector(".map-status");
    if (!status) return;
    status.textContent = message;
    status.classList.add("show");
  }

  _hideMapStatus() {
    const status = this.shadowRoot?.querySelector(".map-status");
    if (!status) return;
    status.classList.remove("show");
    status.textContent = "";
  }

  _minZoom() {
    return clamp(Number(this._config?.min_zoom ?? DEFAULT_CONFIG.min_zoom), 1, 18);
  }

  _maxZoom() {
    return clamp(Number(this._config?.max_zoom ?? DEFAULT_CONFIG.max_zoom), 3, 19);
  }

  _defaultZoom() {
    return clamp(Number(this._config?.default_zoom ?? DEFAULT_CONFIG.default_zoom), 1, 19);
  }

  _radarOpacity() {
    const value = Number(this._config?.radar_opacity);
    return clamp(Number.isFinite(value) ? value : DEFAULT_RADAR_OPACITY, 0.2, 0.95);
  }

  _frameRadarOpacity(frame) {
    const opacity = this._radarOpacity();
    return frame?.kind === "forecast" ? opacity * 0.55 : opacity;
  }

  _coverageOpacity() {
    const value = Number(this._config?.coverage_opacity);
    return clamp(Number.isFinite(value) ? value : DEFAULT_COVERAGE_OPACITY, 0.12, 0.8);
  }

  _animationIntervalMs() {
    const value = Number(this._config?.animation_interval_ms);
    return Math.round(
      clamp(Number.isFinite(value) ? value : DEFAULT_ANIMATION_INTERVAL_MS, 250, 2000)
    );
  }

  _normalizeConfig(config) {
    const normalized = { ...DEFAULT_CONFIG, ...config };
    const statusDefaults = ["show_status", "show_arrival", "show_precipitation", "show_risk", "show_latest_radar"];
    if (config.show_status_strip === false) {
      statusDefaults.forEach((key) => {
        if (config[key] === undefined) normalized[key] = false;
      });
    }
    if (normalized.show_map === undefined) normalized.show_map = true;
    if (normalized.show_icon === undefined) normalized.show_icon = true;
    if (normalized.show_provider === undefined) normalized.show_provider = true;
    if (normalized.show_coverage === undefined) normalized.show_coverage = true;
    if (normalized.severity_background === undefined) normalized.severity_background = false;
    normalized.meta_order = normalizeMetaOrder(normalized.meta_order);
    return normalized;
  }

  _t(key) {
    const lang = (this._hass?.language || globalThis.navigator?.language || "en").toLowerCase();
    const translations = {
      en: {
        default_title: "Rain risk",
        status: "Status",
        arrival: "Arrival",
        precipitation: "Precipitation",
        risk: "12h risk",
        latest_radar: "Latest radar",
        coverage: "Coverage",
        provider: "Provider",
        forecast: "Forecast",
        timeline: "Timeline",
        map: "Map",
        rain_soon: "Rain soon",
        no_rain_soon: "No rain soon",
        rain_unknown: "Rain status unknown",
        coverage_active: "Radar coverage active",
        coverage_unknown: "Coverage unknown",
        stale_radar: "- stale radar data",
        forecast_data: "Forecast data",
        forecast_unavailable: "No forecast data",
        unknown: "Unknown",
        show_details: "Show details",
        hide_details: "Hide details",
        recenter_map: "Recenter map",
        data_unavailable: "Rain Radar data is unavailable for this location right now.",
        data_from_met: "Data from MET Norway",
        play: "Play",
        previous_frame: "Previous frame",
        next_frame: "Next frame",
      },
      sv: {
        default_title: "Risk för regn",
        status: "Status",
        arrival: "Ankomst",
        precipitation: "Nederbörd",
        risk: "12 h risk",
        latest_radar: "Senaste radar",
        coverage: "Täckning",
        provider: "Källa",
        forecast: "Prognos",
        timeline: "Tidslinje",
        map: "Karta",
        rain_soon: "Regn snart",
        no_rain_soon: "Inget regn snart",
        rain_unknown: "Regnstatus okänd",
        coverage_active: "Radartäckning aktiv",
        coverage_unknown: "Täckning okänd",
        stale_radar: "- gammal radardata",
        forecast_data: "Prognosdata",
        forecast_unavailable: "Ingen prognosdata",
        unknown: "Okänt",
        show_details: "Visa detaljer",
        hide_details: "Dölj detaljer",
        recenter_map: "Centrera kartan",
        data_unavailable: "Rain Radar-data är inte tillgänglig för den här platsen just nu.",
        data_from_met: "Data från MET Norway",
        play: "Spela",
        previous_frame: "Föregående bild",
        next_frame: "Nästa bild",
      },
    };
    const dictionary = lang.startsWith("sv") ? translations.sv : translations.en;
    return dictionary[key] || translations.en[key] || key;
  }

  _mapConfigKeyForConfig() {
    return [
      this._config.tile_url,
      this._config.tile_attribution,
      this._minZoom(),
      this._maxZoom(),
      this._config.map_zoom_controls !== false,
      this._config.map_scroll_wheel === true,
    ].join("|");
  }
}

class RainRadarCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
  }

  setConfig(config) {
    this._config = this._normalizeConfig(config);
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _valueChanged(key, value) {
    const config = { ...this._config, [key]: value };
    this._config = config;
    dispatchConfigChanged(this, config);
    this._render();
  }

  _normalizeConfig(config) {
    const normalized = {
      ...DEFAULT_CONFIG,
      ...config,
      meta_order: normalizeMetaOrder(config?.meta_order),
    };
    if (config?.show_status_strip === false) {
      ["show_status", "show_arrival", "show_precipitation", "show_risk", "show_latest_radar"].forEach((key) => {
        if (config[key] === undefined) normalized[key] = false;
      });
    }
    return normalized;
  }

  _render() {
    if (!this.shadowRoot) return;
    this.shadowRoot.innerHTML = `
      <style>
        .editor {
          display: grid;
          gap: 12px;
        }
        label {
          color: var(--primary-text-color);
          display: grid;
          font-size: 0.9rem;
          gap: 6px;
        }
        input, select {
          background: var(--card-background-color);
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          color: var(--primary-text-color);
          min-height: 36px;
          padding: 0 10px;
        }
        .toggles {
          display: grid;
          gap: 8px;
        }
        .toggles label {
          align-items: center;
          display: flex;
          gap: 8px;
        }
        .meta-fields {
          border-top: 1px solid var(--divider-color);
          display: grid;
          gap: 4px;
          padding-top: 12px;
        }
        .meta-fields-title {
          color: var(--secondary-text-color);
          font-size: 0.9rem;
          margin-bottom: 2px;
        }
        .meta-row {
          align-items: center;
          display: grid;
          gap: 8px;
          grid-template-columns: 1fr auto auto;
          min-height: 36px;
        }
        .meta-divider-row {
          color: var(--secondary-text-color);
          grid-template-columns: 1fr auto;
        }
        .order-actions {
          display: flex;
          gap: 4px;
        }
        .order-btn {
          background: var(--secondary-background-color);
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          color: var(--primary-text-color);
          cursor: pointer;
          min-height: 32px;
          min-width: 32px;
          padding: 0;
        }
        .order-btn[disabled] {
          cursor: default;
          opacity: 0.4;
        }
      </style>
      <div class="editor">
        <ha-form class="editor-form"></ha-form>
        <div class="meta-fields">
          <div class="meta-fields-title">Attributes and details</div>
          ${this._metaRowsHtml()}
        </div>
      </div>
    `;
    const form = this.shadowRoot.querySelector(".editor-form");
    if (form) {
      form.hass = this._hass;
      form.data = this._formData();
      form.schema = this._formSchema();
      form.computeLabel = this._computeLabel;
      form.addEventListener("value-changed", (event) => this._formValueChanged(event));
    }
    this.shadowRoot.querySelectorAll("input, select").forEach((input) => {
      input.addEventListener("change", (event) => {
        const target = event.target;
        if (target.dataset.metaKey) return;
        if (target.type === "checkbox") {
          this._valueChanged(target.name, target.checked);
        } else if (target.type === "number") {
          this._valueChanged(target.name, Number(target.value));
        } else {
          this._valueChanged(target.name, target.value);
        }
      });
    });
    this.shadowRoot.querySelectorAll("[data-meta-key]").forEach((input) => {
      input.addEventListener("change", (event) => {
        this._toggleMeta(event.target.dataset.metaKey, event.target.checked);
      });
    });
    this.shadowRoot.querySelectorAll("[data-move-key]").forEach((button) => {
      button.addEventListener("click", () => {
        this._moveMeta(button.dataset.moveKey, Number(button.dataset.delta));
      });
    });
  }

  _formSchema() {
    return [
      {
        name: "entity",
        label: "Entity",
        required: true,
        selector: {
          entity: {
            filter: [{ domain: "binary_sensor" }, { domain: "sensor" }],
          },
        },
      },
      { name: "title", label: "Title", selector: { text: {} } },
      { name: "show_icon", label: "Show icon", selector: { boolean: {} } },
      { name: "severity_background", label: "Status background", selector: { boolean: {} } },
      { name: "show_map", label: "Show map", selector: { boolean: {} } },
      { name: "show_legend", label: "Show attribution details", selector: { boolean: {} } },
      { name: "show_info_panel", label: "Show provider info panel", selector: { boolean: {} } },
      { name: "show_location_marker", label: "Show location marker", selector: { boolean: {} } },
      { name: "map_zoom_controls", label: "Show map zoom controls", selector: { boolean: {} } },
      { name: "map_scroll_wheel", label: "Allow scroll wheel zoom", selector: { boolean: {} } },
      {
        name: "default_zoom",
        label: "Default zoom",
        selector: { number: { min: 3, max: 12, step: 1, mode: "box" } },
      },
      {
        name: "min_zoom",
        label: "Minimum zoom",
        selector: { number: { min: 1, max: 12, step: 1, mode: "box" } },
      },
      {
        name: "max_zoom",
        label: "Maximum zoom",
        selector: { number: { min: 4, max: 19, step: 1, mode: "box" } },
      },
      {
        name: "forecast_minutes",
        label: "Forecast window",
        selector: { number: { min: 15, max: 180, step: 15, mode: "box" } },
      },
      {
        name: "arrival_format",
        label: "Arrival format",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "auto", label: "Auto" },
              { value: "minutes", label: "Minutes" },
              { value: "duration", label: "Hours and minutes" },
              { value: "time", label: "Clock time" },
              { value: "duration_time", label: "Hours, minutes and time" },
            ],
          },
        },
      },
      {
        name: "radar_opacity",
        label: "Radar opacity",
        selector: { number: { min: 0.2, max: 0.95, step: 0.05, mode: "box" } },
      },
      {
        name: "coverage_opacity",
        label: "Coverage opacity",
        selector: { number: { min: 0.12, max: 0.8, step: 0.05, mode: "box" } },
      },
      {
        name: "animation_interval_ms",
        label: "Animation frame interval (ms)",
        selector: { number: { min: 250, max: 2000, step: 50, mode: "box" } },
      },
      {
        name: "default_animation_mode",
        label: "Default animation mode",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "paused", label: "Paused" },
              { value: "playing", label: "Playing" },
            ],
          },
        },
      },
      {
        name: "height",
        label: "Map height",
        selector: { number: { min: 300, max: 760, step: 20, mode: "box" } },
      },
    ];
  }

  _formData() {
    return {
      entity: this._config.entity || "",
      title: this._config.title || "",
      show_icon: this._config.show_icon !== false,
      severity_background: this._config.severity_background === true,
      show_map: this._config.show_map !== false,
      show_legend: this._config.show_legend === true,
      show_info_panel: this._config.show_info_panel === true,
      show_location_marker: this._config.show_location_marker !== false,
      map_zoom_controls: this._config.map_zoom_controls !== false,
      map_scroll_wheel: this._config.map_scroll_wheel === true,
      default_zoom: this._config.default_zoom,
      min_zoom: this._config.min_zoom,
      max_zoom: this._config.max_zoom,
      forecast_minutes: this._config.forecast_minutes,
      arrival_format: this._config.arrival_format || DEFAULT_ARRIVAL_FORMAT,
      radar_opacity: this._config.radar_opacity,
      coverage_opacity: this._config.coverage_opacity,
      animation_interval_ms: this._config.animation_interval_ms,
      default_animation_mode: this._config.default_animation_mode || "paused",
      height: this._config.height,
    };
  }

  _formValueChanged(event) {
    const value = event.detail?.value;
    if (!value) return;
    const config = { ...this._config, ...value };
    this._config = config;
    dispatchConfigChanged(this, config);
    this._render();
  }

  _computeLabel = (schema) => schema.label || schema.name;

  _metaRowsHtml() {
    const order = normalizeMetaOrder(this._config.meta_order);
    return order
      .map((key, index) => {
        if (key === "divider") {
          return `
            <div class="meta-row meta-divider-row">
              <span>— Details —</span>
              <div class="order-actions">
                <button type="button" class="order-btn" data-move-key="${key}" data-delta="-1" ${index === 0 ? "disabled" : ""}>↑</button>
                <button type="button" class="order-btn" data-move-key="${key}" data-delta="1" ${index === order.length - 1 ? "disabled" : ""}>↓</button>
              </div>
            </div>
          `;
        }
        return `
          <div class="meta-row">
            <span>${escapeHtml(this._labelForMeta(key))}</span>
            <div class="order-actions">
              <button type="button" class="order-btn" data-move-key="${key}" data-delta="-1" ${index === 0 ? "disabled" : ""}>↑</button>
              <button type="button" class="order-btn" data-move-key="${key}" data-delta="1" ${index === order.length - 1 ? "disabled" : ""}>↓</button>
            </div>
            <input type="checkbox" data-meta-key="${key}" ${this._isMetaShown(key) ? "checked" : ""}>
          </div>
        `;
      })
      .join("");
  }

  _isMetaShown(key) {
    const configKey = META_CONFIG_KEYS[key];
    return !configKey || this._config[configKey] !== false;
  }

  _toggleMeta(key, shown) {
    const configKey = META_CONFIG_KEYS[key];
    if (!configKey) return;
    this._valueChanged(configKey, shown);
  }

  _moveMeta(key, delta) {
    const order = normalizeMetaOrder(this._config.meta_order);
    const currentIndex = order.indexOf(key);
    if (currentIndex < 0) return;
    const nextIndex = clamp(currentIndex + delta, 0, order.length - 1);
    if (nextIndex === currentIndex) return;
    const nextOrder = [...order];
    nextOrder.splice(currentIndex, 1);
    nextOrder.splice(nextIndex, 0, key);
    this._valueChanged("meta_order", nextOrder);
  }

  _labelForMeta(key) {
    const labels = {
      status: "Status",
      arrival: "Arrival",
      precipitation: "Precipitation",
      risk: "12h risk",
      latest_radar: "Latest radar",
      coverage: "Coverage",
      provider: "Provider",
      forecast: "Forecast",
      timeline: "Timeline",
      map: "Map",
    };
    return labels[key] || key;
  }

}

if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, RainRadarCard);
}

if (!customElements.get(EDITOR_TYPE)) {
  customElements.define(EDITOR_TYPE, RainRadarCardEditor);
}

window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === CARD_TYPE)) {
  window.customCards.push({
    type: CARD_TYPE,
    name: "Rain Radar Card",
    description: "Shows Rain Radar status, provider attribution, radar imagery, and rain timing.",
    preview: true,
    documentationURL: "https://github.com/Nicxe/home-assistant-rain-radar",
  });
}
