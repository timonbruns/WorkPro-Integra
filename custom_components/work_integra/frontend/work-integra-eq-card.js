/*
 * work-integra-eq-card – parametrischer EQ mit Kurvengrafik für Home Assistant
 * Teil der Integration "Work Pro Integra" (wird automatisch geladen).
 *
 *   type: custom:work-integra-eq-card
 *   title: Kanal 1 – EQ
 *   prefix: in1                 # Helfer input_number.in1_eq_band_<n>_{gain,frequenz,q}
 *                               # und input_boolean.in1_eq_bypass
 *   # oder explizit:
 *   # bands:
 *   #   - {gain: input_number.x, frequency: input_number.y, q: input_number.z}
 *   # bypass: input_boolean.x
 *
 *   # Direktmodus (ohne Helfer): Quelle oben in der Karte wählen
 *   # source_select: true
 *   # input_channels: [1, 2, 3, 4, 7, 8]   # optional, 1-basiert
 *   # output_channels: [1, 2, 3, 4, 5]
 *   # side: input                          # Startauswahl
 *   # channel: 1
 *   # entry_id / device_id                 # nur bei mehreren Integra nötig
 *   db_range: 15                # ±dB der Grafik
 *   height: 300                 # px
 *
 * Bedienung: Bandpunkt ziehen = Frequenz (links/rechts) und Gain (hoch/runter),
 * Doppelklick/Doppeltipp = Band auf 0 dB. Güte (Q) und Feinwerte über die
 * Regler unter der Grafik.
 */

const VERSION = "1.8.0";
const COLORS = ["#ff4d4d", "#ff9f1a", "#ffd60a", "#4cd964", "#2ee6d6", "#3d9bff", "#9b6bff", "#ff5fd2"];
const DEFAULT_FREQ = [60, 125, 500, 1000, 2000, 4000, 8000, 16000];
const F_MIN = 20, F_MAX = 20000, Q_MIN = 0.1, Q_MAX = 20;
const FS = 48000;
const SEND_INTERVAL = 150;   // ms zwischen zwei Sendungen beim Ziehen
const HOLD_AFTER_SEND = 1500; // ms, in denen eigene Werte Vorrang vor HA-Zuständen haben
const GRID_MINOR = [30, 40, 60, 70, 80, 90, 300, 400, 600, 700, 800, 900, 3000, 4000, 6000, 7000, 8000, 9000];
const GRID_MAJOR = [[20, "20"], [50, "50"], [100, "100"], [200, "200"], [500, "500"], [1000, "1k"],
  [2000, "2k"], [5000, "5k"], [10000, "10k"], [20000, "20k"]];

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const fmtFreq = (f) => (f < 1000 ? `${Math.round(f)} Hz` : `${(f / 1000).toFixed(f < 10000 ? 2 : 1)} kHz`);
const fmtDb = (g) => `${g > 0 ? "+" : ""}${g.toFixed(1)} dB`;

/** Betragsgang eines Peaking-Biquads (RBJ Audio EQ Cookbook) in dB. */
function peakingDb(f, f0, gainDb, q) {
  if (Math.abs(gainDb) < 0.01) return 0;
  const A = Math.pow(10, gainDb / 40);
  const w0 = (2 * Math.PI * f0) / FS;
  const alpha = Math.sin(w0) / (2 * q);
  const cw = Math.cos(w0);
  const b0 = 1 + alpha * A, b1 = -2 * cw, b2 = 1 - alpha * A;
  const a0 = 1 + alpha / A, a1 = -2 * cw, a2 = 1 - alpha / A;
  const w = (2 * Math.PI * f) / FS;
  const c1 = Math.cos(w), s1 = Math.sin(w), c2 = Math.cos(2 * w), s2 = Math.sin(2 * w);
  const nr = b0 + b1 * c1 + b2 * c2, ni = -(b1 * s1 + b2 * s2);
  const dr = a0 + a1 * c1 + a2 * c2, di = -(a1 * s1 + a2 * s2);
  return 10 * Math.log10((nr * nr + ni * ni) / (dr * dr + di * di));
}

class WorkIntegraEqCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Equalizer", source_select: true };
  }

  setConfig(config) {
    const direct = !!config.source_select || (!config.prefix && !Array.isArray(config.bands));
    if (direct) return this._setDirectConfig(config);
    this._direct = false;
    const bands = Array.isArray(config.bands)
      ? config.bands
      : Array.from({ length: config.band_count || 8 }, (_, i) => ({
          gain: `input_number.${config.prefix}_eq_band_${i + 1}_gain`,
          frequency: `input_number.${config.prefix}_eq_band_${i + 1}_frequenz`,
          q: `input_number.${config.prefix}_eq_band_${i + 1}_q`,
        }));
    this._config = {
      title: config.title ?? "Equalizer",
      range: Number(config.db_range) || 15,
      height: Number(config.height) || 300,
      bypass: config.bypass ?? (config.prefix ? `input_boolean.${config.prefix}_eq_bypass` : null),
      bands,
    };
    this._vals = bands.map((_, i) => ({ f: DEFAULT_FREQ[i] || 1000, g: 0, q: 0.71 }));
    this._hold = bands.map(() => 0);
    this._lastSent = bands.map(() => ({}));
    this._sel = 0;
    this._drag = null;
    this._width = 600;
    if (!this.shadowRoot) this._build();
    this._renderAll();
  }

  _setDirectConfig(config) {
    this._direct = true;
    const chans = (l) => (Array.isArray(l) ? l.map(Number).filter((c) => Number.isInteger(c) && c > 0) : null);
    this._config = {
      title: config.title ?? "Equalizer",
      range: Number(config.db_range) || 15,
      height: Number(config.height) || 300,
      bypass: null,
      bands: Array.from({ length: 8 }, () => ({})),
      entry_id: config.entry_id,
      device_id: config.device_id,
      inCh: chans(config.input_channels),
      outCh: chans(config.output_channels),
    };
    this._src = { side: config.side === "output" ? "output" : "input", channel: Number(config.channel) || 0 };
    this._eq = this._eq || { input: {}, output: {} };
    this._names = this._names || { in: [], out: [] };
    this._vals = this._config.bands.map((_, i) => ({ f: DEFAULT_FREQ[i] || 1000, g: 0, q: 0.71 }));
    this._hold = this._config.bands.map(() => 0);
    this._lastSent = this._config.bands.map(() => ({}));
    this._sel = 0;
    this._drag = null;
    this._width = this._width || 600;
    if (!this.shadowRoot) this._build();
    this._fixSource();
    this._renderAll();
  }

  _channelsFor(side) {
    const list = side === "input" ? this._config.inCh : this._config.outCh;
    if (list) return list;
    const n = Math.max((side === "input" ? this._names.in : this._names.out).length, 8);
    return Array.from({ length: n }, (_, i) => i + 1);
  }

  _fixSource() {
    const list = this._channelsFor(this._src.side);
    if (!list.includes(this._src.channel)) this._src.channel = list[0];
  }

  _chanName(side, ch) {
    const n = (side === "input" ? this._names.in : this._names.out)[ch - 1];
    return n || `${side === "input" ? "IN" : "OUT"} ${ch}`;
  }

  async _subscribeEq() {
    if (this._unsub || this._subscribing || !this._hass) return;
    this._subscribing = true;
    const msg = { type: "work_integra/subscribe_eq" };
    if (this._config.entry_id) msg.entry_id = this._config.entry_id;
    try {
      this._unsub = await this._hass.connection.subscribeMessage((d) => this._onEq(d), msg);
      this._eqError = null;
    } catch (err) {
      this._eqError = err.message || err.code || String(err);
    } finally {
      this._subscribing = false;
      this._renderAll();
    }
  }

  connectedCallback() {
    if (this._direct && this._hass) this._subscribeEq();
  }

  disconnectedCallback() {
    if (this._unsub) {
      try { this._unsub(); } catch (_e) { /* Verbindung weg */ }
      this._unsub = null;
    }
  }

  _chan(side, ch) {
    const s = this._eq[side];
    s[ch] = s[ch] || { bypass: null, bands: {} };
    return s[ch];
  }

  _onEq(d) {
    if (d.snapshot) {
      this._eq = { input: {}, output: {} };
      for (const side of ["input", "output"]) {
        for (const [ch, v] of Object.entries(d.snapshot[side] || {})) {
          this._eq[side][Number(ch)] = { bypass: v.bypass, bands: v.bands || {} };
        }
      }
      if (d.names) this._names = d.names;
      this._fixSource();
      this._hold = this._hold.map(() => 0);
      this._loadSource();
      this._renderAll();
      return;
    }
    const c = this._chan(d.side, d.channel);
    if (d.param === "bypass") c.bypass = !!d.value;
    else if (d.band) (c.bands[d.band] = c.bands[d.band] || {})[d.param] = d.value;
    if (d.side === this._src.side && d.channel === this._src.channel) {
      this._loadSource();
      this._scheduleRender();
    }
  }

  /** Werte der gewählten Quelle in die Anzeige übernehmen (außer gerade bediente Bänder). */
  _loadSource() {
    const c = this._eq[this._src.side][this._src.channel];
    const now = Date.now();
    this._vals.forEach((v, i) => {
      if ((this._drag && this._drag.band === i) || now < this._hold[i]) return;
      const b = (c && c.bands[i + 1]) || {};
      v.f = clamp(Number.isFinite(b.frequency) ? b.frequency : DEFAULT_FREQ[i] || 1000, F_MIN, F_MAX);
      v.g = clamp(Number.isFinite(b.gain) ? b.gain : 0, -this._config.range, this._config.range);
      v.q = clamp(Number.isFinite(b.q) ? b.q : 0.71, Q_MIN, Q_MAX);
    });
    this._missing = !c;
  }

  _selectSource(side, channel) {
    this._flush();
    this._src = { side, channel };
    this._fixSource();
    this._hold = this._hold.map(() => 0);
    this._lastSent = this._lastSent.map(() => ({}));
    this._drag = null;
    this._loadSource();
    this._renderAll();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    if (this._direct) {
      if (this.isConnected) this._subscribeEq();
      return;
    }
    const now = Date.now();
    this._config.bands.forEach((b, i) => {
      if ((this._drag && this._drag.band === i) || now < this._hold[i]) return;
      const num = (id, fb) => {
        const s = id && hass.states[id];
        const v = s ? parseFloat(s.state) : NaN;
        return Number.isFinite(v) ? v : fb;
      };
      const v = this._vals[i];
      v.f = clamp(num(b.frequency, v.f), F_MIN, F_MAX);
      v.g = clamp(num(b.gain, v.g), -this._config.range, this._config.range);
      v.q = clamp(num(b.q, v.q), Q_MIN, Q_MAX);
    });
    this._missing = this._config.bands.some((b) => !hass.states[b.gain] || !hass.states[b.frequency]);
    this._renderAll();
  }

  getCardSize() {
    return Math.ceil(this._config ? this._config.height / 50 + 3 : 9);
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  // ---------------------------------------------------------------- DOM
  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; height: 100%; }
        ha-card { height: 100%; box-sizing: border-box; display: flex; flex-direction: column; background: #0b0d10; color: #d7dde4; border: 1px solid #1d232b; overflow: hidden;
          font-family: var(--ha-font-family-body, Roboto, sans-serif); }
        .head { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px 6px; }
        .title { font-size: 15px; font-weight: 600; letter-spacing: .3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-right: 8px; }
        .bypass { border: 1px solid #39424d; background: #151a20; color: #c7ced6; border-radius: 6px;
          padding: 5px 12px; font-size: 12px; font-weight: 600; cursor: pointer; letter-spacing: .5px; }
        .bypass.on { background: #ffd60a; border-color: #ffd60a; color: #111; }
        .graph { position: relative; margin: 0 10px; }
        svg { display: block; width: 100%; touch-action: none; user-select: none; cursor: crosshair; }
        .chips { display: flex; gap: 6px; padding: 10px 12px 4px; flex-wrap: wrap; }
        .chip { flex: 1 1 0; min-width: 34px; border-radius: 6px; padding: 6px 0; text-align: center; cursor: pointer;
          font-size: 12px; font-weight: 700; background: #151a20; border: 1px solid #262d36; color: #9aa4af; }
        .chip.sel { color: #0b0d10; }
        .detail { margin-top: auto; padding: 6px 14px 14px; display: grid; grid-template-columns: 70px 1fr 78px; gap: 8px 10px; align-items: center; }
        .detail .lbl { font-size: 12px; color: #8b95a1; text-transform: uppercase; letter-spacing: .6px; }
        .detail .val { font-size: 13px; font-variant-numeric: tabular-nums; text-align: right; color: #e9eef3; }
        input[type=range] { -webkit-appearance: none; appearance: none; width: 100%; height: 28px; background: transparent; }
        input[type=range]::-webkit-slider-runnable-track { height: 4px; background: #2a313a; border-radius: 2px; }
        input[type=range]::-moz-range-track { height: 4px; background: #2a313a; border-radius: 2px; }
        input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; width: 20px; height: 20px; margin-top: -8px;
          border-radius: 50%; background: var(--band); border: 2px solid #0b0d10; box-shadow: 0 0 0 1px var(--band); }
        input[type=range]::-moz-range-thumb { width: 18px; height: 18px; border-radius: 50%; background: var(--band);
          border: 2px solid #0b0d10; }
        .row-head { grid-column: 1 / -1; display: flex; align-items: center; justify-content: space-between; }
        .band-name { font-weight: 700; font-size: 14px; display: flex; align-items: center; gap: 8px; }
        .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--band); box-shadow: 0 0 8px var(--band); }
        .reset { border: 1px solid #39424d; background: #151a20; color: #c7ced6; border-radius: 6px; padding: 4px 10px;
          font-size: 12px; cursor: pointer; }
        .warn { padding: 0 14px 10px; color: #ff9f1a; font-size: 12px; }
        .src { padding: 12px 12px 2px; display: flex; flex-direction: column; gap: 10px; }
        .segs { display: flex; gap: 0; border: 1px solid #262d36; border-radius: 8px; overflow: hidden; align-self: flex-start; }
        .seg { border: 0; background: #151a20; color: #9aa4af; padding: 8px 18px; font-size: 13px; font-weight: 700; cursor: pointer; }
        .seg.on { background: #3d9bff; color: #06080a; }
        .src-chips { display: flex; gap: 6px; flex-wrap: wrap; }
        .src-chip { flex: 1 1 90px; display: flex; flex-direction: column; align-items: flex-start; gap: 2px; border: 1px solid #262d36;
          background: #151a20; color: #c7ced6; border-radius: 8px; padding: 7px 10px; cursor: pointer; text-align: left; min-width: 0; }
        .src-chip .src-no { font-size: 10px; font-weight: 700; color: #6f7a86; letter-spacing: .6px; }
        .src-chip .src-name { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
        .src-chip.on { background: #1b2a3c; border-color: #3d9bff; box-shadow: inset 0 0 0 1px #3d9bff; }
        .src-chip.on .src-no { color: #3d9bff; }
      </style>
      <ha-card>
        <div class="src" hidden></div>
        <div class="head"><div class="title"></div><button class="bypass">BYPASS</button></div>
        <div class="graph"><svg></svg></div>
        <div class="chips"></div>
        <div class="detail">
          <div class="row-head"><div class="band-name"><span class="dot"></span><span class="bn"></span></div>
            <button class="reset">0 dB</button></div>
          <div class="lbl">Frequenz</div><input class="sf" type="range" min="0" max="1000" step="1"><div class="val vf"></div>
          <div class="lbl">Gain</div><input class="sg" type="range" step="0.5"><div class="val vg"></div>
          <div class="lbl">Güte Q</div><input class="sq" type="range" min="0" max="1000" step="1"><div class="val vq"></div>
        </div>
        <div class="warn" hidden></div>
      </ha-card>`;
    const $ = (s) => root.querySelector(s);
    this._el = {
      title: $(".title"), bypass: $(".bypass"), svg: $("svg"), chips: $(".chips"), detail: $(".detail"),
      bn: $(".bn"), sf: $(".sf"), sg: $(".sg"), sq: $(".sq"), vf: $(".vf"), vg: $(".vg"), vq: $(".vq"),
      reset: $(".reset"), warn: $(".warn"), graph: $(".graph"), src: $(".src"),
    };
    new ResizeObserver(() => {
      const w = Math.round(this._el.graph.clientWidth);
      if (w && w !== this._width) { this._width = w; this._renderGraph(); }
    }).observe(this._el.graph);

    const svg = this._el.svg;
    svg.addEventListener("pointerdown", (e) => this._onDown(e));
    svg.addEventListener("pointermove", (e) => this._onMove(e));
    svg.addEventListener("pointerup", (e) => this._onUp(e));
    svg.addEventListener("pointercancel", (e) => this._onUp(e));
    svg.addEventListener("dblclick", (e) => {
      const i = this._hit(e);
      if (i !== null) { this._vals[i].g = 0; this._commit(i, ["g"], true); }
    });
    this._el.bypass.addEventListener("click", () => this._toggleBypass());
    this._el.reset.addEventListener("click", () => { this._vals[this._sel].g = 0; this._commit(this._sel, ["g"], true); });

    const fFromSlider = (s) => F_MIN * Math.pow(F_MAX / F_MIN, s / 1000);
    const qFromSlider = (s) => Q_MIN * Math.pow(Q_MAX / Q_MIN, s / 1000);
    const bind = (el, key, conv) => {
      el.addEventListener("input", () => {
        const i = this._sel;
        this._vals[i][key] = conv(parseFloat(el.value));
        this._commit(i, [key], false);
      });
      el.addEventListener("change", () => this._commit(this._sel, [key], true));
    };
    bind(this._el.sf, "f", (s) => Math.round(fFromSlider(s)));
    bind(this._el.sg, "g", (s) => s);
    bind(this._el.sq, "q", (s) => Math.round(qFromSlider(s) * 100) / 100);
  }

  // ---------------------------------------------------------------- Geometrie
  _geo() {
    const W = this._width, H = this._config.height;
    const m = { l: 34, r: 10, t: 10, b: 22 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b, R = this._config.range;
    const lmin = Math.log10(F_MIN), lspan = Math.log10(F_MAX) - lmin;
    return {
      W, H, m, pw, ph, R,
      x: (f) => m.l + ((Math.log10(f) - lmin) / lspan) * pw,
      y: (db) => m.t + ((R - db) / (2 * R)) * ph,
      f: (x) => Math.pow(10, lmin + clamp((x - m.l) / pw, 0, 1) * lspan),
      db: (y) => R - clamp((y - m.t) / ph, 0, 1) * 2 * R,
    };
  }

  _svgPoint(e) {
    const r = this._el.svg.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * this._width, y: ((e.clientY - r.top) / r.height) * this._config.height };
  }

  _hit(e) {
    const g = this._geo(), p = this._svgPoint(e);
    let best = null, bd = 30 * 30;
    this._vals.forEach((v, i) => {
      const d = (g.x(v.f) - p.x) ** 2 + (g.y(v.g) - p.y) ** 2;
      if (d < bd) { bd = d; best = i; }
    });
    return best;
  }

  // ---------------------------------------------------------------- Bedienung
  _onDown(e) {
    const i = this._hit(e);
    if (i === null) return;
    e.preventDefault();
    this._el.svg.setPointerCapture(e.pointerId);
    this._sel = i;
    this._drag = { band: i, id: e.pointerId };
    this._renderAll();
  }

  _onMove(e) {
    if (!this._drag || e.pointerId !== this._drag.id) return;
    const g = this._geo(), p = this._svgPoint(e), v = this._vals[this._drag.band];
    v.f = Math.round(clamp(g.f(p.x), F_MIN, F_MAX));
    v.g = Math.round(clamp(g.db(p.y), -g.R, g.R) * 2) / 2;
    this._commit(this._drag.band, ["f", "g"], false);
  }

  _onUp(e) {
    if (!this._drag || e.pointerId !== this._drag.id) return;
    const i = this._drag.band;
    this._drag = null;
    this._commit(i, ["f", "g"], true);
  }

  _toggleBypass() {
    if (this._direct) {
      if (!this._hass) return;
      const { side, channel } = this._src;
      const c = this._chan(side, channel);
      const next = !c.bypass;
      c.bypass = next;   // sofort anzeigen; Rückmeldung kommt über den Stream
      this._renderAll();
      this._hass.callService("work_integra", "set_peq", this._target({ side, channel, band: 1, bypass: next }));
      return;
    }
    const id = this._config.bypass;
    if (!id || !this._hass) return;
    const on = this._hass.states[id]?.state === "on";
    this._hass.callService("input_boolean", on ? "turn_off" : "turn_on", { entity_id: id });
  }

  /** Lokal anzeigen und (gedrosselt bzw. final) an die Helfer senden. */
  _commit(i, keys, final) {
    this._hold[i] = Date.now() + HOLD_AFTER_SEND;
    this._scheduleRender();
    this._pending = this._pending || {};
    keys.forEach((k) => { this._pending[`${i}:${k}`] = true; });
    const now = Date.now();
    if (final || !this._lastFlush || now - this._lastFlush >= SEND_INTERVAL) {
      this._flush();
    } else if (!this._flushTimer) {
      this._flushTimer = setTimeout(() => this._flush(), SEND_INTERVAL - (now - this._lastFlush));
    }
  }

  _flush() {
    clearTimeout(this._flushTimer);
    this._flushTimer = null;
    this._lastFlush = Date.now();
    if (!this._hass || !this._pending) return;
    const map = { f: "frequency", g: "gain", q: "q" };
    if (this._direct) {
      const perBand = {};
      for (const key of Object.keys(this._pending)) {
        const [i, k] = key.split(":");
        const val = this._vals[i][k];
        if (this._lastSent[i][k] === val) continue;
        this._lastSent[i][k] = val;
        (perBand[i] = perBand[i] || {})[map[k]] = val;
      }
      const { side, channel } = this._src;
      for (const [i, data] of Object.entries(perBand)) {
        this._hass.callService("work_integra", "set_peq",
          this._target({ side, channel, band: Number(i) + 1, ...data }));
      }
      this._pending = {};
      return;
    }
    for (const key of Object.keys(this._pending)) {
      const [i, k] = key.split(":");
      const ent = this._config.bands[i][map[k]];
      const val = this._vals[i][k];
      if (!ent || this._lastSent[i][k] === val) continue;
      this._lastSent[i][k] = val;
      this._hass.callService("input_number", "set_value", { entity_id: ent, value: val });
    }
    this._pending = {};
  }

  // ---------------------------------------------------------------- Zeichnen
  _scheduleRender() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = null; this._renderAll(); });
  }

  _target(data) {
    return this._config.device_id ? { device_id: this._config.device_id, ...data } : data;
  }

  _renderSource() {
    const el = this._el.src;
    el.hidden = !this._direct;
    if (!this._direct) return;
    const { side, channel } = this._src;
    const key = JSON.stringify([side, channel, this._channelsFor("input"), this._channelsFor("output"), this._names]);
    if (key === this._srcKey) return;
    this._srcKey = key;
    const seg = (s, label) => `<button class="seg ${s === side ? "on" : ""}" data-side="${s}">${label}</button>`;
    const chips = this._channelsFor(side).map((ch) =>
      `<button class="src-chip ${ch === channel ? "on" : ""}" data-ch="${ch}">
         <span class="src-no">${side === "input" ? "IN" : "OUT"} ${ch}</span><span class="src-name">${this._esc(this._chanName(side, ch))}</span></button>`).join("");
    el.innerHTML = `<div class="segs">${seg("input", "Eingänge")}${seg("output", "Ausgänge")}</div><div class="src-chips">${chips}</div>`;
    el.querySelectorAll(".seg").forEach((b) => b.addEventListener("click", () => {
      const s = b.dataset.side;
      if (s !== this._src.side) this._selectSource(s, this._channelsFor(s)[0]);
    }));
    el.querySelectorAll(".src-chip").forEach((b) => b.addEventListener("click", () =>
      this._selectSource(this._src.side, Number(b.dataset.ch))));
  }

  _esc(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  _renderAll() {
    if (!this._el || !this._config) return;
    this._renderSource();
    this._renderHead();
    this._renderGraph();
    this._renderChips();
    this._renderDetail();
  }

  _renderHead() {
    if (this._direct) {
      const { side, channel } = this._src;
      this._el.title.textContent = `${this._config.title} – ${this._chanName(side, channel)}`;
      this._el.bypass.hidden = false;
      this._el.bypass.classList.toggle("on", this._bypassed());
      const msg = this._eqError ? `Keine Verbindung zur Integration: ${this._eqError}`
        : this._missing && this._unsub ? "Für diesen Kanal liegen noch keine EQ-Werte vor – DSP-Verbindung prüfen." : "";
      this._el.warn.hidden = !msg;
      this._el.warn.textContent = msg;
      return;
    }
    this._el.title.textContent = this._config.title;
    const id = this._config.bypass;
    const st = id && this._hass ? this._hass.states[id] : null;
    this._el.bypass.hidden = !id;
    this._el.bypass.classList.toggle("on", st?.state === "on");
    this._el.warn.hidden = !this._missing;
    this._el.warn.textContent = this._missing ? "Einige Helfer-Entitäten fehlen – Konfiguration prüfen." : "";
  }

  _bypassed() {
    if (this._direct) {
      const c = this._eq[this._src.side][this._src.channel];
      return !!(c && c.bypass);
    }
    const id = this._config.bypass;
    return !!(id && this._hass && this._hass.states[id]?.state === "on");
  }

  _renderGraph() {
    const g = this._geo(), { W, H, m, R } = g;
    const parts = [];
    parts.push(`<defs><filter id="glow" x="-5%" y="-20%" width="110%" height="140%">
      <feGaussianBlur stdDeviation="2.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>`);
    parts.push(`<rect x="${m.l}" y="${m.t}" width="${g.pw}" height="${g.ph}" fill="#06080a" stroke="#1d232b"/>`);
    GRID_MINOR.forEach((f) => parts.push(`<line x1="${g.x(f)}" x2="${g.x(f)}" y1="${m.t}" y2="${H - m.b}" stroke="#151a20"/>`));
    const narrow = g.pw < 380;
    GRID_MAJOR.forEach(([f, t]) => {
      const x = g.x(f);
      parts.push(`<line x1="${x}" x2="${x}" y1="${m.t}" y2="${H - m.b}" stroke="#232a33"/>`);
      if (narrow && ![20, 100, 1000, 10000].includes(f)) return;  // schmale Karte: weniger Beschriftung
      const anchor = f === F_MIN ? "start" : f === F_MAX ? "end" : "middle";
      parts.push(`<text x="${x}" y="${H - 6}" fill="#6f7a86" font-size="10" text-anchor="${anchor}">${t}</text>`);
    });
    for (let d = -R; d <= R + 0.001; d += 3) {
      const y = g.y(d);
      parts.push(`<line x1="${m.l}" x2="${W - m.r}" y1="${y}" y2="${y}" stroke="${d === 0 ? "#3a4450" : "#171c22"}"/>`);
      if (Math.round(d) % 6 === 0) {
        parts.push(`<text x="${m.l - 5}" y="${y + 3.5}" fill="#6f7a86" font-size="10" text-anchor="end">${d > 0 ? "+" : ""}${Math.round(d)}</text>`);
      }
    }

    // Frequenzraster für die Kurven
    const N = Math.max(120, Math.min(400, Math.round(g.pw / 2)));
    const freqs = Array.from({ length: N }, (_, k) => F_MIN * Math.pow(F_MAX / F_MIN, k / (N - 1)));
    const by = this._bypassed();
    const sum = new Array(N).fill(0);
    const y0 = g.y(0);
    this._vals.forEach((v, i) => {
      const col = COLORS[i % COLORS.length];
      const pts = freqs.map((f, k) => {
        const d = peakingDb(f, v.f, v.g, v.q);
        sum[k] += d;
        return `${g.x(f).toFixed(1)},${g.y(clamp(d, -R, R)).toFixed(1)}`;
      });
      if (Math.abs(v.g) < 0.01) return;
      const sel = i === this._sel;
      parts.push(`<path d="M${g.x(F_MIN)},${y0} L${pts.join(" L")} L${g.x(F_MAX)},${y0} Z"
        fill="${col}" fill-opacity="${by ? 0.04 : sel ? 0.22 : 0.12}" stroke="${col}" stroke-opacity="${by ? 0.15 : 0.55}" stroke-width="1"/>`);
    });
    const sumPts = freqs.map((f, k) => `${g.x(f).toFixed(1)},${g.y(clamp(sum[k], -R, R)).toFixed(1)}`);
    parts.push(`<path d="M${sumPts.join(" L")}" fill="none" stroke="${by ? "#4a525c" : "#f2f6fa"}" stroke-width="2.2"
      ${by ? 'stroke-dasharray="6 5"' : 'filter="url(#glow)"'} stroke-linejoin="round"/>`);
    if (by) {
      parts.push(`<text x="${m.l + g.pw / 2}" y="${m.t + 22}" fill="#ffd60a" font-size="13" font-weight="700" text-anchor="middle" letter-spacing="2">BYPASS</text>`);
    }

    // Bandpunkte (ausgewählter zuletzt, damit er oben liegt)
    const order = this._vals.map((_, i) => i).filter((i) => i !== this._sel).concat([this._sel]);
    order.forEach((i) => {
      const v = this._vals[i], col = COLORS[i % COLORS.length];
      const x = g.x(v.f), y = g.y(v.g), sel = i === this._sel;
      if (sel) {
        parts.push(`<line x1="${x}" x2="${x}" y1="${m.t}" y2="${H - m.b}" stroke="${col}" stroke-opacity=".35" stroke-dasharray="3 3"/>`);
        parts.push(`<circle cx="${x}" cy="${y}" r="16" fill="none" stroke="${col}" stroke-width="2" opacity=".9"/>`);
      }
      parts.push(`<circle cx="${x}" cy="${y}" r="11" fill="${col}" stroke="#06080a" stroke-width="2" ${by ? 'opacity=".55"' : ""}/>`);
      parts.push(`<text x="${x}" y="${y + 4}" fill="#06080a" font-size="11" font-weight="800" text-anchor="middle" pointer-events="none">${i + 1}</text>`);
    });

    const svg = this._el.svg;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("height", H);
    svg.innerHTML = parts.join("");
  }

  _renderChips() {
    const el = this._el.chips;
    if (el.childElementCount !== this._vals.length) {
      el.innerHTML = this._vals.map((_, i) => `<div class="chip" data-i="${i}">${i + 1}</div>`).join("");
      el.querySelectorAll(".chip").forEach((c) =>
        c.addEventListener("click", () => { this._sel = Number(c.dataset.i); this._renderAll(); }));
    }
    el.querySelectorAll(".chip").forEach((c, i) => {
      const col = COLORS[i % COLORS.length], sel = i === this._sel;
      c.classList.toggle("sel", sel);
      c.style.background = sel ? col : "";
      c.style.borderColor = col;
      c.style.color = sel ? "" : col;
    });
  }

  _renderDetail() {
    const i = this._sel, v = this._vals[i], col = COLORS[i % COLORS.length], R = this._config.range;
    this._el.detail.style.setProperty("--band", col);
    this._el.bn.textContent = `Band ${i + 1}`;
    const active = this.shadowRoot.activeElement;
    const set = (el, val) => { if (active !== el) el.value = val; };
    set(this._el.sf, Math.round((1000 * Math.log(v.f / F_MIN)) / Math.log(F_MAX / F_MIN)));
    this._el.sg.min = -R; this._el.sg.max = R;
    set(this._el.sg, v.g);
    set(this._el.sq, Math.round((1000 * Math.log(v.q / Q_MIN)) / Math.log(Q_MAX / Q_MIN)));
    this._el.vf.textContent = fmtFreq(v.f);
    this._el.vg.textContent = fmtDb(v.g);
    this._el.vq.textContent = v.q.toFixed(2);
  }
}

if (!customElements.get("work-integra-eq-card")) {
  customElements.define("work-integra-eq-card", WorkIntegraEqCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "work-integra-eq-card",
    name: "Work Pro Integra – Equalizer",
    description: "Parametrischer EQ mit Kurvengrafik und ziehbaren Bändern",
    preview: false,
  });
  console.info(`%c WORK-INTEGRA-EQ-CARD %c ${VERSION} `, "background:#3d9bff;color:#000;font-weight:700", "background:#0b0d10;color:#fff");
}
