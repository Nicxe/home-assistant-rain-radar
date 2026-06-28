const CARD_TYPE = "rain-radar-card";
const EDITOR_TYPE = "rain-radar-card-editor";
const DEFAULT_CONFIG = {
  show_timeline: true,
  show_status_strip: true,
  default_zoom: 6,
  default_animation_mode: "paused",
  height: 360,
};

function localTime(value) {
  if (!value) return "Unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function relativeMinutes(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "Unknown";
  if (number <= 0) return "Now";
  if (number === 1) return "1 min";
  return `${Math.round(number)} min`;
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

class RainRadarCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = null;
    this._hass = null;
    this._frames = [];
    this._animationUrl = null;
    this._activeFrame = 0;
    this._playing = false;
    this._timer = null;
    this._frameEntryId = null;
  }

  static getStubConfig() {
    return {
      type: `custom:${CARD_TYPE}`,
      entity: "binary_sensor.home_rain_soon",
      show_timeline: true,
      show_status_strip: true,
    };
  }

  static getConfigElement() {
    return document.createElement(EDITOR_TYPE);
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Please define a Rain Radar entity.");
    }
    this._config = { ...DEFAULT_CONFIG, ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const stateObj = hass.states[this._config?.entity];
    const entryId = stateObj?.attributes?.rain_radar_entry_id;
    if (entryId && entryId !== this._frameEntryId) {
      this._frameEntryId = entryId;
      this._loadFrames(entryId);
    }
    this._render();
  }

  disconnectedCallback() {
    this._stop();
  }

  getCardSize() {
    return 5;
  }

  getGridOptions() {
    const rows = Math.max(4, Math.ceil(Number(this._config?.height || 360) / 56));
    return {
      columns: 12,
      min_columns: 6,
      rows,
      min_rows: 4,
    };
  }

  async _loadFrames(entryId) {
    if (!this._hass?.callApi) return;
    try {
      const payload = await this._hass.callApi("GET", `rain_radar/${entryId}/frames`);
      this._frames = Array.isArray(payload.frames) ? payload.frames : [];
      this._animationUrl = payload.animation_url || null;
      this._activeFrame = Math.max(0, this._frames.length - 1);
      this._render();
    } catch (error) {
      this._frames = [];
      this._animationUrl = null;
      this._render();
    }
  }

  _togglePlay() {
    if (this._playing) {
      this._stop();
      this._render();
      return;
    }
    this._playing = true;
    this._timer = window.setInterval(() => {
      const count = this._frames.length || 1;
      this._activeFrame = (this._activeFrame + 1) % count;
      this._render();
    }, 900);
    this._render();
  }

  _stop() {
    this._playing = false;
    if (this._timer) {
      window.clearInterval(this._timer);
      this._timer = null;
    }
  }

  _step(delta) {
    const count = this._frames.length;
    if (!count) return;
    this._activeFrame = (this._activeFrame + count + delta) % count;
    this._render();
  }

  _render() {
    if (!this.shadowRoot || !this._config) return;
    const hass = this._hass;
    const main = hass?.states?.[this._config.entity];
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

    const isUnavailable = !main || main.state === "unavailable";
    const activeFrame = this._frames[this._activeFrame] || null;
    const imageUrl = activeFrame?.url || this._animationUrl;
    const hasTimeline = this._config.show_timeline && this._frames.length > 1;
    const height = Math.max(260, Number(this._config.height || DEFAULT_CONFIG.height));
    const rainingSoon = main?.state === "on";
    const coverageOk = coverage?.state === "on";

    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          color: var(--primary-text-color);
        }

        ha-card {
          overflow: hidden;
          border-radius: var(--ha-card-border-radius, 8px);
        }

        .shell {
          display: grid;
          gap: 0;
          min-height: ${height}px;
          background:
            linear-gradient(145deg, rgba(42, 125, 151, 0.14), transparent 34%),
            linear-gradient(0deg, rgba(44, 84, 64, 0.10), transparent 54%),
            var(--ha-card-background, var(--card-background-color, #fff));
        }

        .header {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 12px;
          padding: 16px 16px 10px;
        }

        .title {
          min-width: 0;
        }

        .name {
          font-size: 1.05rem;
          font-weight: 650;
          line-height: 1.2;
          overflow-wrap: anywhere;
        }

        .subtitle {
          color: var(--secondary-text-color);
          font-size: 0.82rem;
          line-height: 1.35;
          margin-top: 3px;
        }

        .badge {
          align-items: center;
          background: ${rainingSoon ? "rgba(35, 124, 93, 0.18)" : "rgba(91, 99, 113, 0.14)"};
          border: 1px solid var(--divider-color);
          border-radius: 999px;
          color: var(--primary-text-color);
          display: inline-flex;
          flex: 0 0 auto;
          font-size: 0.78rem;
          font-weight: 650;
          gap: 6px;
          min-height: 30px;
          padding: 0 10px;
          white-space: nowrap;
        }

        .status {
          display: ${this._config.show_status_strip ? "grid" : "none"};
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 1px;
          border-top: 1px solid var(--divider-color);
          border-bottom: 1px solid var(--divider-color);
          background: var(--divider-color);
        }

        .metric {
          background: color-mix(in srgb, var(--ha-card-background, #fff) 86%, transparent);
          min-width: 0;
          padding: 10px 12px;
        }

        .metric-label {
          color: var(--secondary-text-color);
          font-size: 0.72rem;
          line-height: 1.1;
          margin-bottom: 4px;
        }

        .metric-value {
          font-size: 0.95rem;
          font-weight: 700;
          line-height: 1.15;
          overflow-wrap: anywhere;
        }

        .radar {
          position: relative;
          min-height: ${Math.max(180, height - 185)}px;
          overflow: hidden;
          background:
            linear-gradient(90deg, rgba(66, 109, 77, 0.25) 1px, transparent 1px),
            linear-gradient(0deg, rgba(66, 109, 77, 0.16) 1px, transparent 1px),
            radial-gradient(circle at 28% 42%, rgba(30, 112, 84, 0.30), transparent 22%),
            radial-gradient(circle at 68% 48%, rgba(38, 112, 151, 0.22), transparent 24%),
            color-mix(in srgb, var(--primary-background-color, #111) 18%, #3f6f58);
          background-size: 48px 48px, 48px 48px, auto, auto, auto;
        }

        .radar img {
          display: block;
          height: 100%;
          inset: 0;
          object-fit: cover;
          position: absolute;
          width: 100%;
        }

        .radar::before {
          border: 1px solid rgba(255,255,255,0.22);
          border-radius: 50%;
          content: "";
          height: 72%;
          left: 14%;
          position: absolute;
          top: 13%;
          width: 72%;
        }

        .radar::after {
          background:
            linear-gradient(90deg, transparent 0 48%, rgba(255,255,255,0.25) 49% 51%, transparent 52%),
            linear-gradient(0deg, transparent 0 48%, rgba(255,255,255,0.18) 49% 51%, transparent 52%);
          content: "";
          inset: 0;
          position: absolute;
          pointer-events: none;
        }

        .overlay {
          align-items: flex-end;
          display: flex;
          inset: 0;
          justify-content: space-between;
          padding: 12px;
          position: absolute;
          pointer-events: none;
        }

        .panel {
          backdrop-filter: blur(10px);
          background: rgba(12, 23, 26, 0.58);
          border: 1px solid rgba(255,255,255,0.18);
          border-radius: 8px;
          color: white;
          max-width: min(72%, 360px);
          padding: 10px 12px;
        }

        .panel strong {
          display: block;
          font-size: 0.92rem;
          line-height: 1.2;
          margin-bottom: 3px;
        }

        .panel span {
          color: rgba(255,255,255,0.82);
          display: block;
          font-size: 0.78rem;
          line-height: 1.35;
        }

        .controls {
          align-items: center;
          display: ${this._config.show_timeline ? "grid" : "none"};
          gap: 10px;
          grid-template-columns: auto auto minmax(0, 1fr) auto;
          padding: 12px 16px 16px;
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

        .message {
          align-items: center;
          display: ${isUnavailable ? "flex" : "none"};
          inset: 0;
          justify-content: center;
          padding: 18px;
          position: absolute;
          text-align: center;
        }

        @media (max-width: 520px) {
          .header {
            align-items: stretch;
            flex-direction: column;
          }

          .badge {
            align-self: flex-start;
          }

          .status {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }

          .controls {
            grid-template-columns: auto auto minmax(0, 1fr);
          }

          .frame-time {
            grid-column: 1 / -1;
            text-align: left;
          }
        }
      </style>
      <ha-card>
        <div class="shell">
          <div class="header">
            <div class="title">
              <div class="name">${main?.attributes?.friendly_name || "Rain Radar"}</div>
              <div class="subtitle">${stateText(provider, "MET Norway")} · ${coverageOk ? "Radar coverage active" : "Coverage unknown"}</div>
            </div>
            <div class="badge">
              <ha-icon icon="${rainingSoon ? "mdi:weather-pouring" : "mdi:weather-partly-cloudy"}"></ha-icon>
              ${rainingSoon ? "Rain soon" : "No rain soon"}
            </div>
          </div>

          <div class="status">
            <div class="metric">
              <div class="metric-label">Precipitation</div>
              <div class="metric-value">${numberText(stateText(precipitation, null), " mm/h")}</div>
            </div>
            <div class="metric">
              <div class="metric-label">Arrival</div>
              <div class="metric-value">${relativeMinutes(stateText(arrival, null))}</div>
            </div>
            <div class="metric">
              <div class="metric-label">12h risk</div>
              <div class="metric-value">${numberText(stateText(risk, null), "%")}</div>
            </div>
            <div class="metric">
              <div class="metric-label">Latest radar</div>
              <div class="metric-value">${localTime(stateText(radarTime, null))}</div>
            </div>
          </div>

          <div class="radar">
            ${imageUrl ? `<img src="${imageUrl}" alt="MET Norway radar frame" loading="lazy">` : ""}
            <div class="message">
              <div>Rain Radar data is unavailable for this location right now.</div>
            </div>
            <div class="overlay">
              <div class="panel">
                <strong>${imageUrl ? "MET Norway radar" : "Point forecast view"}</strong>
                <span>${rainingSoon ? "Rain is expected inside the configured window." : "No rain is expected inside the configured window."}</span>
                <span>${provider?.attributes?.attribution || main?.attributes?.attribution || "Data from MET Norway"}</span>
              </div>
            </div>
          </div>

          <div class="controls">
            <button type="button" class="play" title="${this._playing ? "Pause" : "Play"}">
              <ha-icon icon="${this._playing ? "mdi:pause" : "mdi:play"}"></ha-icon>
            </button>
            <button type="button" class="step-back" title="Previous frame" ${hasTimeline ? "" : "disabled"}>
              <ha-icon icon="mdi:step-backward"></ha-icon>
            </button>
            <input type="range" min="0" max="${Math.max(0, this._frames.length - 1)}"
              value="${this._activeFrame}" ${hasTimeline ? "" : "disabled"}
              aria-label="Radar frame timeline">
            <div class="frame-time">${localTime(activeFrame?.time || radarTime?.state)}</div>
          </div>
        </div>
      </ha-card>
    `;

    this.shadowRoot.querySelector(".play")?.addEventListener("click", () => this._togglePlay());
    this.shadowRoot.querySelector(".step-back")?.addEventListener("click", () => this._step(-1));
    this.shadowRoot.querySelector("input[type='range']")?.addEventListener("input", (event) => {
      this._activeFrame = Number(event.target.value);
      this._render();
    });
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
    this._config = { ...DEFAULT_CONFIG, ...config };
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

  _render() {
    if (!this.shadowRoot) return;
    const entities = this._hass
      ? Object.keys(this._hass.states)
          .filter((entityId) => entityId.startsWith("binary_sensor.") || entityId.startsWith("sensor."))
          .sort()
      : [];
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
      </style>
      <div class="editor">
        <label>
          Entity
          <input list="rain-radar-entities" name="entity" value="${this._config.entity || ""}">
          <datalist id="rain-radar-entities">
            ${entities.map((entityId) => `<option value="${entityId}"></option>`).join("")}
          </datalist>
        </label>
        <div class="toggles">
          <label><input type="checkbox" name="show_timeline" ${this._config.show_timeline ? "checked" : ""}> Show timeline</label>
          <label><input type="checkbox" name="show_status_strip" ${this._config.show_status_strip ? "checked" : ""}> Show status strip</label>
        </div>
        <label>
          Default zoom
          <input type="number" name="default_zoom" min="3" max="10" step="1" value="${this._config.default_zoom}">
        </label>
        <label>
          Default animation mode
          <select name="default_animation_mode">
            <option value="paused" ${this._config.default_animation_mode === "paused" ? "selected" : ""}>Paused</option>
            <option value="playing" ${this._config.default_animation_mode === "playing" ? "selected" : ""}>Playing</option>
          </select>
        </label>
        <label>
          Height
          <input type="number" name="height" min="260" max="700" step="20" value="${this._config.height}">
        </label>
      </div>
    `;
    this.shadowRoot.querySelectorAll("input, select").forEach((input) => {
      input.addEventListener("change", (event) => {
        const target = event.target;
        if (target.type === "checkbox") {
          this._valueChanged(target.name, target.checked);
        } else if (target.type === "number") {
          this._valueChanged(target.name, Number(target.value));
        } else {
          this._valueChanged(target.name, target.value);
        }
      });
    });
  }
}

customElements.define(CARD_TYPE, RainRadarCard);
customElements.define(EDITOR_TYPE, RainRadarCardEditor);

window.customCards = window.customCards || [];
window.customCards.push({
  type: CARD_TYPE,
  name: "Rain Radar Card",
  preview: false,
  description: "Shows Rain Radar status, provider attribution, radar imagery, and rain timing.",
  documentationURL: "https://github.com/Nicxe/home-assistant-rain-radar",
});

