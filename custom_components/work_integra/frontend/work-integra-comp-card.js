/*
 * work-integra-comp-card – Kompressor mit Kennliniengrafik für Home Assistant
 * Teil der Integration "Work Pro Integra" (wird automatisch geladen).
 *
 *   type: custom:work-integra-comp-card
 *   title: Kanal 1 – Kompressor
 *   prefix: in1          # Helfer input_number.in1_comp_{threshold,ratio,attack,release}
 *   # oder explizit: threshold/ratio/attack/release: input_number.x
 *   height: 300          # px, gleiche Höhe wie die EQ-Karte -> nebeneinander nutzbar
 *
 * Bedienung: Punkt T ziehen = Threshold, Punkt R ziehen = Ratio; Feinwerte und
 * Attack/Release über die Regler unter der Grafik.
 */

const COMP_VERSION = "1.5.0";
const C_T = "#ff9f1a";   // Threshold
const C_R = "#2ee6d6";   // Ratio
const C_A = "#3d9bff";   // Attack
const C_REL = "#9b6bff"; // Release
const DB_MIN = -60, DB_MAX = 0;
const LIM = {
  threshold: [-60, 0], ratio: [1, 20], attack: [1, 500], release: [10, 5000],
};
const C_SEND_INTERVAL = 150, C_HOLD = 1500;

const cclamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const logTo = (s, lo, hi) => lo * Math.pow(hi / lo, s / 1000);
const toLog = (v, lo, hi) => Math.round((1000 * Math.log(v / lo)) / Math.log(hi / lo));
const fmtMs = (v) => (v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);

class WorkIntegraCompCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Kompressor", prefix: "in1" };
  }

  setConfig(config) {
    const p = config.prefix;
    const ent = (k) => config[k] ?? (p ? `input_number.${p}_comp_${k}` : null);
    if (!ent("threshold") || !ent("ratio")) throw new Error("Bitte 'prefix' (z. B. in1) oder threshold/ratio angeben.");
    this._config = {
      title: config.title ?? "Kompressor",
      height: Number(config.height) || 300,
      ent: { threshold: ent("threshold"), ratio: ent("ratio"), attack: ent("attack"), release: ent("release") },
    };
    this._v = { threshold: -20, ratio: 2, attack: 15, release: 1000 };
    this._hold = {};
    this._sent = {};
    this._drag = null;
    this._width = 300;
    if (!this.shadowRoot) this._build();
    this._renderAll();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const now = Date.now();
    for (const [k, id] of Object.entries(this._config.ent)) {
      if (!id || (this._drag && this._drag.key === k) || now < (this._hold[k] || 0)) continue;
      const v = parseFloat(hass.states[id]?.state);
      if (Number.isFinite(v)) this._v[k] = cclamp(v, ...LIM[k]);
    }
    this._missing = Object.values(this._config.ent).some((id) => id && !hass.states[id]);
    this._renderAll();
  }

  getCardSize() {
    return Math.ceil(this._config ? this._config.height / 50 + 3 : 9);
  }

  getGridOptions() {
    return { columns: 6, min_columns: 6, rows: "auto" };
  }

  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; height: 100%; }
        ha-card { height: 100%; box-sizing: border-box; display: flex; flex-direction: column;
          background: #0b0d10; color: #d7dde4; border: 1px solid #1d232b; overflow: hidden;
          font-family: var(--ha-font-family-body, Roboto, sans-serif); }
        .head { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px 6px; min-height: 29px; }
        .title { font-size: 15px; font-weight: 600; letter-spacing: .3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-right: 8px; }
        .gr { font-size: 12px; color: #8b95a1; font-variant-numeric: tabular-nums; white-space: nowrap; }
        .graph { margin: 0 10px; }
        svg { display: block; width: 100%; touch-action: none; user-select: none; cursor: crosshair; }
        .chips { display: flex; gap: 6px; padding: 10px 12px 4px; }
        .chip { flex: 1 1 0; min-width: 0; border-radius: 6px; padding: 6px 2px; text-align: center; font-size: 12px; font-weight: 700;
          background: #151a20; border: 1px solid var(--c); color: var(--c); font-variant-numeric: tabular-nums;
          white-space: nowrap; overflow: hidden; }
        .detail { padding: 6px 14px 14px; display: grid; grid-template-columns: 80px 1fr 78px; gap: 8px 10px; align-items: center; margin-top: auto; }
        .lbl { font-size: 12px; color: #8b95a1; text-transform: uppercase; letter-spacing: .6px; }
        .val { font-size: 13px; font-variant-numeric: tabular-nums; text-align: right; color: #e9eef3; }
        input[type=range] { -webkit-appearance: none; appearance: none; width: 100%; height: 28px; background: transparent; }
        input[type=range]::-webkit-slider-runnable-track { height: 4px; background: #2a313a; border-radius: 2px; }
        input[type=range]::-moz-range-track { height: 4px; background: #2a313a; border-radius: 2px; }
        input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; width: 20px; height: 20px; margin-top: -8px;
          border-radius: 50%; background: var(--c); border: 2px solid #0b0d10; box-shadow: 0 0 0 1px var(--c); }
        input[type=range]::-moz-range-thumb { width: 18px; height: 18px; border-radius: 50%; background: var(--c); border: 2px solid #0b0d10; }
        .warn { padding: 0 14px 10px; color: #ff9f1a; font-size: 12px; }
      </style>
      <ha-card>
        <div class="head"><div class="title"></div><div class="gr"></div></div>
        <div class="graph"><svg></svg></div>
        <div class="chips">
          <div class="chip ct" style="--c:${C_T}"></div><div class="chip cr" style="--c:${C_R}"></div>
          <div class="chip ca" style="--c:${C_A}"></div><div class="chip crel" style="--c:${C_REL}"></div>
        </div>
        <div class="detail">
          <div class="lbl">Threshold</div><input class="st" style="--c:${C_T}" type="range" min="-60" max="0" step="0.5"><div class="val vt"></div>
          <div class="lbl">Ratio</div><input class="sr" style="--c:${C_R}" type="range" min="0" max="1000" step="1"><div class="val vr"></div>
          <div class="lbl">Attack</div><input class="sa" style="--c:${C_A}" type="range" min="0" max="1000" step="1"><div class="val va"></div>
          <div class="lbl">Release</div><input class="srel" style="--c:${C_REL}" type="range" min="0" max="1000" step="1"><div class="val vrel"></div>
        </div>
        <div class="warn" hidden></div>
      </ha-card>`;
    const $ = (s) => root.querySelector(s);
    this._el = {
      title: $(".title"), gr: $(".gr"), graph: $(".graph"), svg: $("svg"), warn: $(".warn"),
      ct: $(".ct"), cr: $(".cr"), ca: $(".ca"), crel: $(".crel"),
      st: $(".st"), sr: $(".sr"), sa: $(".sa"), srel: $(".srel"),
      vt: $(".vt"), vr: $(".vr"), va: $(".va"), vrel: $(".vrel"),
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

    const bind = (el, key, conv) => {
      el.addEventListener("input", () => { this._v[key] = conv(parseFloat(el.value)); this._commit([key], false); });
      el.addEventListener("change", () => this._commit([key], true));
    };
    bind(this._el.st, "threshold", (s) => s);
    bind(this._el.sr, "ratio", (s) => Math.round(logTo(s, ...LIM.ratio) * 10) / 10);
    bind(this._el.sa, "attack", (s) => Math.round(logTo(s, ...LIM.attack)));
    bind(this._el.srel, "release", (s) => Math.round(logTo(s, ...LIM.release) / 10) * 10);
  }

  // ---------------------------------------------------------------- Geometrie
  _geo() {
    const W = this._width, H = this._config.height;
    const m = { l: 34, r: 12, t: 10, b: 22 };
    const pw = W - m.l - m.r, ph = H - m.t - m.b, span = DB_MAX - DB_MIN;
    return {
      W, H, m, pw, ph,
      x: (d) => m.l + ((d - DB_MIN) / span) * pw,
      y: (d) => m.t + ((DB_MAX - d) / span) * ph,
      dx: (x) => DB_MIN + cclamp((x - m.l) / pw, 0, 1) * span,
      dy: (y) => DB_MAX - cclamp((y - m.t) / ph, 0, 1) * span,
    };
  }

  _out(inDb) {
    const { threshold: T, ratio: R } = this._v;
    return inDb <= T ? inDb : T + (inDb - T) / R;
  }

  _handles(g) {
    const T = this._v.threshold;
    return {
      threshold: { x: g.x(T), y: g.y(T) },
      ratio: { x: g.x(DB_MAX), y: g.y(this._out(DB_MAX)) },
    };
  }

  _pt(e) {
    const r = this._el.svg.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * this._width, y: ((e.clientY - r.top) / r.height) * this._config.height };
  }

  _onDown(e) {
    const g = this._geo(), p = this._pt(e), h = this._handles(g);
    let key = null, best = 32 * 32;
    for (const [k, q] of Object.entries(h)) {
      const d = (q.x - p.x) ** 2 + (q.y - p.y) ** 2;
      if (d < best) { best = d; key = k; }
    }
    if (!key) return;
    e.preventDefault();
    this._el.svg.setPointerCapture(e.pointerId);
    this._drag = { key, id: e.pointerId };
    this._onMove(e);
  }

  _onMove(e) {
    if (!this._drag || e.pointerId !== this._drag.id) return;
    const g = this._geo(), p = this._pt(e);
    if (this._drag.key === "threshold") {
      // entlang der 1:1-Diagonale: Mittel aus x- und y-Position
      const d = (g.dx(p.x) + g.dy(p.y)) / 2;
      this._v.threshold = Math.round(cclamp(d, ...LIM.threshold) * 2) / 2;
      this._commit(["threshold"], false);
    } else {
      const T = this._v.threshold;
      if (T >= DB_MAX - 0.5) return;
      const out0 = cclamp(g.dy(p.y), T + 0.01, DB_MAX);
      this._v.ratio = Math.round(cclamp((DB_MAX - T) / (out0 - T), ...LIM.ratio) * 10) / 10;
      this._commit(["ratio"], false);
    }
  }

  _onUp(e) {
    if (!this._drag || e.pointerId !== this._drag.id) return;
    const key = this._drag.key;
    this._drag = null;
    this._commit([key], true);
  }

  _commit(keys, final) {
    const now = Date.now();
    keys.forEach((k) => { this._hold[k] = now + C_HOLD; });
    this._pending = Object.assign(this._pending || {}, Object.fromEntries(keys.map((k) => [k, true])));
    this._scheduleRender();
    if (final || !this._lastFlush || now - this._lastFlush >= C_SEND_INTERVAL) this._flush();
    else if (!this._timer) this._timer = setTimeout(() => this._flush(), C_SEND_INTERVAL - (now - this._lastFlush));
  }

  _flush() {
    clearTimeout(this._timer);
    this._timer = null;
    this._lastFlush = Date.now();
    if (!this._hass || !this._pending) return;
    for (const k of Object.keys(this._pending)) {
      const id = this._config.ent[k], v = this._v[k];
      if (!id || this._sent[k] === v) continue;
      this._sent[k] = v;
      this._hass.callService("input_number", "set_value", { entity_id: id, value: v });
    }
    this._pending = {};
  }

  // ---------------------------------------------------------------- Zeichnen
  _scheduleRender() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = null; this._renderAll(); });
  }

  _renderAll() {
    if (!this._el || !this._config) return;
    this._el.title.textContent = this._config.title;
    const { threshold: T, ratio: R, attack: A, release: Rel } = this._v;
    const gr = -(this._out(DB_MAX) - DB_MAX);
    this._el.gr.textContent = `GR max ${gr.toFixed(1)} dB`;
    this._el.gr.title = "maximale Pegelreduktion bei 0 dB Eingang";
    this._el.ct.textContent = `${T.toFixed(1)} dB`;
    this._el.cr.textContent = `${R.toFixed(1)}:1`;
    this._el.ca.textContent = fmtMs(A);
    this._el.crel.textContent = fmtMs(Rel);
    const active = this.shadowRoot.activeElement;
    const set = (el, v) => { if (active !== el) el.value = v; };
    set(this._el.st, T);
    set(this._el.sr, toLog(R, ...LIM.ratio));
    set(this._el.sa, toLog(A, ...LIM.attack));
    set(this._el.srel, toLog(Rel, ...LIM.release));
    this._el.vt.textContent = `${T.toFixed(1)} dB`;
    this._el.vr.textContent = `${R.toFixed(1)}:1`;
    this._el.va.textContent = fmtMs(A);
    this._el.vrel.textContent = fmtMs(Rel);
    this._el.warn.hidden = !this._missing;
    this._el.warn.textContent = this._missing ? "Einige Helfer-Entitäten fehlen – Konfiguration prüfen." : "";
    this._renderGraph();
  }

  _renderGraph() {
    if (!this._el) return;
    const g = this._geo(), { W, H, m } = g;
    const T = this._v.threshold, R = this._v.ratio;
    const parts = [];
    parts.push(`<defs><filter id="glow" x="-10%" y="-10%" width="120%" height="120%">
      <feGaussianBlur stdDeviation="2.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>`);
    parts.push(`<rect x="${m.l}" y="${m.t}" width="${g.pw}" height="${g.ph}" fill="#06080a" stroke="#1d232b"/>`);
    for (let d = DB_MIN; d <= DB_MAX; d += 6) {
      const major = d % 12 === 0;
      parts.push(`<line x1="${g.x(d)}" x2="${g.x(d)}" y1="${m.t}" y2="${H - m.b}" stroke="${major ? "#232a33" : "#151a20"}"/>`);
      parts.push(`<line x1="${m.l}" x2="${W - m.r}" y1="${g.y(d)}" y2="${g.y(d)}" stroke="${major ? "#232a33" : "#151a20"}"/>`);
      if (major && !(g.pw < 260 && d % 24 !== 0 && d !== DB_MAX)) {
        parts.push(`<text x="${g.x(d)}" y="${H - 6}" fill="#6f7a86" font-size="10" text-anchor="${d === DB_MIN ? "start" : d === DB_MAX ? "end" : "middle"}">${d}</text>`);
        parts.push(`<text x="${m.l - 5}" y="${g.y(d) + 3.5}" fill="#6f7a86" font-size="10" text-anchor="end">${d}</text>`);
      }
    }
    parts.push(`<text x="${W - m.r - 4}" y="${H - m.b - 6}" fill="#4f5a66" font-size="10" text-anchor="end">IN dB</text>`);
    parts.push(`<text x="${m.l + 4}" y="${m.t + 12}" fill="#4f5a66" font-size="10">OUT dB</text>`);
    // 1:1-Referenz
    parts.push(`<line x1="${g.x(DB_MIN)}" y1="${g.y(DB_MIN)}" x2="${g.x(DB_MAX)}" y2="${g.y(DB_MAX)}" stroke="#3a4450" stroke-dasharray="5 5"/>`);
    // Reduktionsfläche oberhalb des Thresholds
    const out0 = this._out(DB_MAX);
    parts.push(`<path d="M${g.x(T)},${g.y(T)} L${g.x(DB_MAX)},${g.y(DB_MAX)} L${g.x(DB_MAX)},${g.y(out0)} Z" fill="${C_T}" fill-opacity=".14"/>`);
    // Threshold-Hilfslinien
    parts.push(`<line x1="${g.x(T)}" x2="${g.x(T)}" y1="${m.t}" y2="${H - m.b}" stroke="${C_T}" stroke-opacity=".35" stroke-dasharray="3 3"/>`);
    parts.push(`<line x1="${m.l}" x2="${W - m.r}" y1="${g.y(T)}" y2="${g.y(T)}" stroke="${C_T}" stroke-opacity=".2" stroke-dasharray="3 3"/>`);
    // Kennlinie
    parts.push(`<path d="M${g.x(DB_MIN)},${g.y(DB_MIN)} L${g.x(T)},${g.y(T)} L${g.x(DB_MAX)},${g.y(out0)}" fill="none"
      stroke="#f2f6fa" stroke-width="2.4" stroke-linejoin="round" filter="url(#glow)"/>`);
    // Griffe
    const h = this._handles(g);
    const handle = (q, col, label, active) => {
      if (active) parts.push(`<circle cx="${q.x}" cy="${q.y}" r="16" fill="none" stroke="${col}" stroke-width="2"/>`);
      parts.push(`<circle cx="${q.x}" cy="${q.y}" r="11" fill="${col}" stroke="#06080a" stroke-width="2"/>`);
      parts.push(`<text x="${q.x}" y="${q.y + 4}" fill="#06080a" font-size="11" font-weight="800" text-anchor="middle" pointer-events="none">${label}</text>`);
    };
    handle(h.threshold, C_T, "T", this._drag?.key === "threshold");
    handle({ x: h.ratio.x - 2, y: h.ratio.y }, C_R, "R", this._drag?.key === "ratio");
    // Beschriftungen
    const tx = cclamp(h.threshold.x + 14, m.l + 4, W - m.r - 60);
    parts.push(`<text x="${tx}" y="${h.threshold.y + 18}" fill="${C_T}" font-size="11" font-weight="700">${T.toFixed(1)} dB</text>`);
    parts.push(`<text x="${h.ratio.x - 18}" y="${cclamp(h.ratio.y - 16, m.t + 12, H - m.b - 4)}" fill="${C_R}" font-size="11" font-weight="700" text-anchor="end">${R.toFixed(1)}:1</text>`);

    const svg = this._el.svg;
    svg.setAttribute("viewBox", `0 0 ${W} ${this._config.height}`);
    svg.setAttribute("height", this._config.height);
    svg.innerHTML = parts.join("");
  }
}

if (!customElements.get("work-integra-comp-card")) {
  customElements.define("work-integra-comp-card", WorkIntegraCompCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "work-integra-comp-card",
    name: "Work Pro Integra – Kompressor",
    description: "Kompressor mit Kennliniengrafik, ziehbarem Threshold und Ratio",
    preview: false,
  });
}
