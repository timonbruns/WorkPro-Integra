/*
 * work-integra-matrix-card – Routing-Matrix (Eingänge × Ausgänge) für Home Assistant
 * Teil der Integration "Work Pro Integra" (wird automatisch geladen).
 *
 *   type: custom:work-integra-matrix-card
 *   title: Matrix
 *   entity_prefix: switch.technikraum_integra   # optional: nur Schalter dieses Geräts
 *
 * Nutzt die Matrix-Schalter der Integration (Attribute matrix_input/matrix_output).
 * Kreuzpunkt antippen = ein/aus.
 */

const MATRIX_VERSION = "1.4.0";
const ROW_COLORS = ["#ff4d4d", "#ff9f1a", "#ffd60a", "#4cd964", "#2ee6d6", "#3d9bff", "#9b6bff", "#ff5fd2"];
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

class WorkIntegraMatrixCard extends HTMLElement {
  static getStubConfig() {
    return { title: "Matrix" };
  }

  setConfig(config) {
    this._config = { title: config.title ?? "Matrix", prefix: config.entity_prefix || "" };
    this._pending = {};
    if (!this.shadowRoot) this._build();
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 7;
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
        .head { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px 8px; }
        .title { font-size: 15px; font-weight: 600; letter-spacing: .3px; }
        .legend { font-size: 11px; color: #6f7a86; }
        .wrap { padding: 0 12px 14px; overflow-x: auto; }
        table { border-collapse: separate; border-spacing: 4px; margin: 0 auto; }
        th { font-size: 11px; font-weight: 600; color: #8b95a1; white-space: nowrap; }
        th.out { height: 74px; vertical-align: bottom; padding: 0; }
        th.out div { writing-mode: vertical-rl; transform: rotate(180deg); max-height: 70px; overflow: hidden;
          text-overflow: ellipsis; margin: 0 auto; }
        th.in { text-align: right; padding-right: 6px; max-width: 110px; overflow: hidden; text-overflow: ellipsis; }
        th.in span { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px;
          vertical-align: middle; }
        th.corner { font-size: 10px; color: #4f5a66; text-align: right; vertical-align: bottom; padding: 0 6px 4px 0; }
        td { padding: 0; }
        button.x { width: 38px; height: 38px; border-radius: 7px; border: 1px solid #262d36; background: #12161b;
          cursor: pointer; padding: 0; display: block; transition: background .12s, box-shadow .12s; }
        button.x:hover { border-color: #3a4450; }
        button.x.on { border-color: var(--c); background: var(--c); box-shadow: 0 0 10px -2px var(--c); }
        button.x.unknown { background: repeating-linear-gradient(45deg, #12161b 0 4px, #1a2027 4px 8px); }
        button.x.pending { opacity: .6; }
        button.x:disabled { cursor: not-allowed; opacity: .35; }
        .empty, .note { padding: 6px 14px 14px; font-size: 12px; color: #8b95a1; }
      </style>
      <ha-card>
        <div class="head"><div class="title"></div><div class="legend"></div></div>
        <div class="wrap"></div>
        <div class="note"></div>
      </ha-card>`;
    this._el = { title: root.querySelector(".title"), legend: root.querySelector(".legend"),
      wrap: root.querySelector(".wrap"), note: root.querySelector(".note") };
    this._el.wrap.addEventListener("click", (e) => {
      const b = e.target.closest("button.x");
      if (!b || b.disabled || !this._hass) return;
      const id = b.dataset.e;
      const on = this._hass.states[id]?.state === "on";
      this._pending[id] = Date.now();
      b.classList.add("pending");
      this._hass.callService("switch", on ? "turn_off" : "turn_on", { entity_id: id });
    });
  }

  _points() {
    const pts = [];
    for (const [id, st] of Object.entries(this._hass.states)) {
      if (!id.startsWith("switch.") || st.attributes.matrix_input === undefined) continue;
      if (this._config.prefix && !id.startsWith(this._config.prefix)) continue;
      pts.push({ id, st, i: st.attributes.matrix_input, o: st.attributes.matrix_output });
    }
    return pts;
  }

  _render() {
    if (!this._el || !this._config || !this._hass) return;
    this._el.title.textContent = this._config.title;
    const pts = this._points();
    if (!pts.length) {
      this._el.wrap.innerHTML = `<div class="empty">Keine Matrix-Schalter gefunden (Integration ab Version ${MATRIX_VERSION} nötig).</div>`;
      this._el.note.textContent = "";
      return;
    }
    const ins = [...new Set(pts.map((p) => p.i))].sort((a, b) => a - b);
    const outs = [...new Set(pts.map((p) => p.o))].sort((a, b) => a - b);
    const at = {}, inName = {}, outName = {};
    pts.forEach((p) => {
      at[`${p.i}:${p.o}`] = p;
      inName[p.i] = p.st.attributes.input_name || `IN ${p.i}`;
      outName[p.o] = p.st.attributes.output_name || `OUT ${p.o}`;
    });
    const now = Date.now();
    let unknown = 0, active = 0;
    let html = `<table><tr><th class="corner">IN ↓ / OUT →</th>`;
    outs.forEach((o) => { html += `<th class="out" title="${esc(outName[o])}"><div>${esc(outName[o])}</div></th>`; });
    html += `</tr>`;
    ins.forEach((i, r) => {
      const col = ROW_COLORS[(i - 1) % ROW_COLORS.length];
      html += `<tr><th class="in" title="${esc(inName[i])}"><span style="background:${col}"></span>${esc(inName[i])}</th>`;
      outs.forEach((o) => {
        const p = at[`${i}:${o}`];
        if (!p) { html += `<td></td>`; return; }
        const s = p.st.state;
        if (s === "on") active++;
        if (s !== "on" && s !== "off") unknown++;
        const pend = this._pending[p.id] && now - this._pending[p.id] < 1500 ? " pending" : "";
        const cls = s === "on" ? "on" : s === "off" ? "" : "unknown";
        const dis = s === "unavailable" ? "disabled" : "";
        html += `<td><button class="x ${cls}${pend}" style="--c:${col}" data-e="${p.id}" ${dis}
          title="${esc(inName[i])} → ${esc(outName[o])}: ${s === "on" ? "an" : s === "off" ? "aus" : "unbekannt"}"></button></td>`;
      });
      html += `</tr>`;
    });
    html += `</table>`;
    this._el.wrap.innerHTML = html;
    this._el.legend.textContent = `${active} aktiv`;
    this._el.note.textContent = unknown
      ? "Schraffierte Punkte: Zustand noch unbekannt – einmal schalten oder in der Work-Software ändern."
      : "";
  }
}

if (!customElements.get("work-integra-matrix-card")) {
  customElements.define("work-integra-matrix-card", WorkIntegraMatrixCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "work-integra-matrix-card",
    name: "Work Pro Integra – Matrix",
    description: "Routing-Matrix Eingänge × Ausgänge",
    preview: false,
  });
}
