/*
 * work-integra-meter-card – Live-Pegel aller Ein- und Ausgänge (ohne Entitäten)
 * Teil der Integration "Work Pro Integra" (wird automatisch geladen).
 *
 *   type: custom:work-integra-meter-card
 *   title: Pegel
 *   entry_id: 01J…            # optional, nur bei mehreren Integra nötig
 *   inputs: [Headset 1, Headset 2, …]    # optional: eigene Namen (sonst Gerätenamen)
 *   outputs: [Saal L, Saal R, …]
 *   input_channels: [1, 2, 3, 4, 7, 8]   # optional: nur diese Eingänge anzeigen (1-basiert)
 *   output_channels: [1, 2, 3, 4, 5]     # optional: nur diese Ausgänge anzeigen
 *                                        # (Namen in inputs/outputs gelten immer je Kanal 1…8)
 *   min_db: -60               # Skala unten
 *   max_db: 0                 # Skala oben
 *   height: 220               # px
 *   interval: 0.1             # s zwischen zwei Abfragen
 *
 * Die Pegel kommen per Websocket (work_integra/subscribe_meters) direkt aus der
 * Integration – nur solange die Karte sichtbar ist. Keine Entitäten, kein Recorder.
 */

const METER_VERSION = "1.8.0";
const PEAK_HOLD_MS = 1500;
const FALL_DB_PER_S = 30;      // Abfallgeschwindigkeit der Anzeige

class WorkIntegraMeterCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Pegel" };
  }

  setConfig(config) {
    this._config = {
      title: config.title ?? "Pegel",
      entry_id: config.entry_id,
      min: Number(config.min_db ?? -60),
      max: Number(config.max_db ?? 0),
      height: Number(config.height) || 220,
      interval: Number(config.interval) || 0.1,
      inNames: config.inputs,
      outNames: config.outputs,
      inCh: WorkIntegraMeterCard._channels(config.input_channels),
      outCh: WorkIntegraMeterCard._channels(config.output_channels),
    };
    this._names = { in: [], out: [] };
    this._target = { in: [], out: [] };
    this._shown = { in: [], out: [] };
    this._peak = { in: [], out: [] };
    this._peakAt = { in: [], out: [] };
    this._lastMsg = 0;
    if (!this.shadowRoot) this._build();
  }

  static _channels(list) {
    if (!Array.isArray(list)) return null;
    const out = list.map((c) => Number(c) - 1).filter((c) => Number.isInteger(c) && c >= 0);
    return out.length ? out : null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && this.isConnected) this._subscribe();
  }

  connectedCallback() {
    if (this._hass) this._subscribe();
    this._startDraw();
  }

  disconnectedCallback() {
    this._unsubscribe();
    cancelAnimationFrame(this._raf);
    this._raf = null;
  }

  getCardSize() {
    return Math.ceil((this._config?.height || 220) / 50) + 2;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  _build() {
    const root = this.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { background: #0b0d10; color: #d7dde4; border: 1px solid #1d232b; overflow: hidden;
          font-family: var(--ha-font-family-body, Roboto, sans-serif); }
        .head { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px 6px; }
        .title { font-size: 15px; font-weight: 600; letter-spacing: .3px; }
        .state { font-size: 12px; color: #6f7a86; }
        .state.err { color: #ff9f1a; }
        canvas { display: block; width: 100%; }
        .wrap { padding: 0 10px 12px; }
      </style>
      <ha-card>
        <div class="head"><div class="title"></div><div class="state">verbinde …</div></div>
        <div class="wrap"><canvas></canvas></div>
      </ha-card>`;
    this._el = { title: root.querySelector(".title"), state: root.querySelector(".state"),
      canvas: root.querySelector("canvas"), wrap: root.querySelector(".wrap") };
    new ResizeObserver(() => this._resize()).observe(this._el.wrap);
  }

  async _subscribe() {
    if (this._unsub || this._subscribing || !this._hass) return;
    this._subscribing = true;
    const msg = { type: "work_integra/subscribe_meters", interval: this._config.interval };
    if (this._config.entry_id) msg.entry_id = this._config.entry_id;
    try {
      this._unsub = await this._hass.connection.subscribeMessage((d) => this._onData(d), msg);
      this._el.state.textContent = "live";
      this._el.state.classList.remove("err");
    } catch (err) {
      this._el.state.textContent = `Fehler: ${err.message || err.code || err}`;
      this._el.state.classList.add("err");
    } finally {
      this._subscribing = false;
    }
  }

  _unsubscribe() {
    if (this._unsub) {
      try { this._unsub(); } catch (_e) { /* Verbindung bereits weg */ }
      this._unsub = null;
    }
  }

  _onData(d) {
    if (d.names) this._names = d.names;
    this._lastMsg = Date.now();
    const now = performance.now();
    for (const k of ["in", "out"]) {
      (d[k] || []).forEach((v, i) => {
        this._target[k][i] = v;
        if (v !== null && v !== undefined && (this._peak[k][i] === undefined || v >= this._peak[k][i] || now - this._peakAt[k][i] > PEAK_HOLD_MS)) {
          this._peak[k][i] = v;
          this._peakAt[k][i] = now;
        }
      });
    }
  }

  _resize() {
    const c = this._el.canvas, w = this._el.wrap.clientWidth - 20;
    if (w <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(w * dpr);
    c.height = Math.round(this._config.height * dpr);
    c.style.height = `${this._config.height}px`;
    this._dpr = dpr;
    this._w = w;
  }

  _startDraw() {
    if (this._raf) return;
    let last = performance.now();
    const tick = (t) => {
      const dt = (t - last) / 1000;
      last = t;
      this._step(dt);
      this._draw();
      this._raf = requestAnimationFrame(tick);
    };
    this._raf = requestAnimationFrame(tick);
  }

  _step(dt) {
    if (!this._config) return;
    const { min } = this._config;
    for (const k of ["in", "out"]) {
      const tgt = this._target[k];
      for (let i = 0; i < tgt.length; i++) {
        const target = tgt[i] ?? min;
        const cur = this._shown[k][i] ?? min;
        // Anstieg sofort, Abfall gedämpft (wie ein Pultmeter)
        this._shown[k][i] = target >= cur ? target : Math.max(target, cur - FALL_DB_PER_S * dt);
      }
    }
  }

  _draw() {
    if (!this._config || !this._el) return;
    const c = this._el.canvas;
    if (!c || !this._w) return;
    this._el.title.textContent = this._config.title;
    const stale = this._lastMsg && Date.now() - this._lastMsg > 2000;
    if (stale) { this._el.state.textContent = "keine Daten"; this._el.state.classList.add("err"); }
    else if (this._lastMsg) { this._el.state.textContent = "live"; this._el.state.classList.remove("err"); }

    const ctx = c.getContext("2d");
    const dpr = this._dpr || 1, W = this._w, H = this._config.height;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const { min, max } = this._config;
    const all = (k, names) => {
      const n = Math.max(this._target[k].length, (names || []).length, 8);
      return [...Array(n).keys()];
    };
    const chIn = this._config.inCh || all("in", this._config.inNames);
    const chOut = this._config.outCh || all("out", this._config.outNames);
    const nIn = chIn.length, nOut = chOut.length;
    const scaleW = 30, gap = 18, labelH = 30, top = 18, bottom = H - labelH;
    const barsW = W - scaleW - gap;
    const slot = barsW / (nIn + nOut);
    const barW = Math.max(6, Math.min(34, slot * 0.62));
    const y = (db) => top + (1 - (Math.min(max, Math.max(min, db)) - min) / (max - min)) * (bottom - top);

    // Skala
    ctx.font = "10px Roboto, sans-serif";
    ctx.fillStyle = "#6f7a86";
    ctx.textAlign = "right";
    for (let d = Math.ceil(min / 6) * 6; d <= max; d += 6) {
      const yy = y(d);
      ctx.fillText(d > 0 ? `+${d}` : `${d}`, scaleW - 6, yy + 3);
      ctx.strokeStyle = d === 0 ? "#3a4450" : "#161b21";
      ctx.beginPath(); ctx.moveTo(scaleW, yy + 0.5); ctx.lineTo(W, yy + 0.5); ctx.stroke();
    }

    const group = (k, chans, x0, names, label) => {
      ctx.fillStyle = "#8b95a1";
      ctx.textAlign = "left";
      ctx.font = "600 10px Roboto, sans-serif";
      ctx.fillText(label, x0 + (slot - barW) / 2, 11);
      chans.forEach((i, pos) => {
        const x = x0 + pos * slot + (slot - barW) / 2;
        // Hintergrund
        ctx.fillStyle = "#12161b";
        ctx.fillRect(x, top, barW, bottom - top);
        const v = this._shown[k][i];
        const raw = this._target[k][i];
        if (raw !== null && raw !== undefined && v !== undefined) {
          const yv = y(v);
          const grad = ctx.createLinearGradient(0, bottom, 0, top);
          const p = (db) => (Math.min(max, Math.max(min, db)) - min) / (max - min);
          grad.addColorStop(0, "#1f8f4e");
          grad.addColorStop(p(-18), "#4cd964");
          grad.addColorStop(p(-9), "#ffd60a");
          grad.addColorStop(p(-3), "#ff9f1a");
          grad.addColorStop(1, "#ff4d4d");
          ctx.fillStyle = grad;
          ctx.fillRect(x, yv, barW, bottom - yv);
          // Peak-Hold
          const pk = this._peak[k][i];
          if (pk !== undefined && pk > min) {
            ctx.fillStyle = pk >= max - 1 ? "#ff4d4d" : "#e9eef3";
            ctx.fillRect(x, y(pk) - 1, barW, 2);
          }
        } else if (raw === null) {
          ctx.fillStyle = "#3a2a1a";
          ctx.fillRect(x, bottom - 3, barW, 3);
        }
        // Name
        const name = (names && names[i]) || (k === "in" ? `IN${i + 1}` : `OUT${i + 1}`);
        ctx.save();
        ctx.fillStyle = "#c7ced6";
        ctx.font = "10px Roboto, sans-serif";
        ctx.textAlign = "center";
        const cx = x + barW / 2;
        let txt = name;
        while (ctx.measureText(txt).width > slot - 2 && txt.length > 2) txt = txt.slice(0, -1);
        if (txt !== name) txt = txt.slice(0, -1) + "…";
        ctx.fillText(txt, cx, bottom + 13);
        const val = raw === null || raw === undefined ? "–" : raw <= min ? "-∞" : raw.toFixed(0);
        ctx.fillStyle = "#6f7a86";
        ctx.fillText(val, cx, bottom + 25);
        ctx.restore();
      });
    };
    const xIn = scaleW + 4;
    group("in", chIn, xIn, this._config.inNames || this._names.in, "EINGÄNGE");
    const xOut = xIn + nIn * slot + gap;
    ctx.strokeStyle = "#232a33";
    ctx.beginPath(); ctx.moveTo(xOut - gap / 2, top); ctx.lineTo(xOut - gap / 2, bottom + 26); ctx.stroke();
    group("out", chOut, xOut, this._config.outNames || this._names.out, "AUSGÄNGE");
  }
}

if (!customElements.get("work-integra-meter-card")) {
  customElements.define("work-integra-meter-card", WorkIntegraMeterCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "work-integra-meter-card",
    name: "Work Pro Integra – Pegel",
    description: "Live-Pegel aller Ein- und Ausgänge (Stream, ohne Entitäten)",
    preview: false,
  });
}
