/* ------------------------------------------------------------------ *
 * app.js -- Vue 3 front end for the stacked MOP 113 (SI) calculator.
 *
 *   - holds the global params + the reactive element stack;
 *   - debounce-POSTs to /api/calculate on any change;
 *   - re-runs MathJax.typesetPromise() for the LaTeX report;
 *   - draws the 3 figures (stack elevation, force breakdown, Kz) with Plotly.
 *
 * The Flask engine (wind_mop113.py, SI-native) is the source of truth.
 * ------------------------------------------------------------------ */
const { createApp } = Vue;

// Suggested Cf when an element kind changes (Table 3-9).
const CF_SUGGEST = { equipment_circular: 0.9, equipment_rectangular: 2.0,
                     pedestal_plinth: 2.0 };

const KIND_LABELS = {
  equipment_circular: 'equipment – circular',
  equipment_rectangular: 'equipment – rectangular',
  lattice_truss: 'lattice truss support',
  pedestal_plinth: 'pedestal / plinth',
};

// Deep clone helper (presets must not be mutated by the form).
const clone = (o) => JSON.parse(JSON.stringify(o));

// ---- APEC report identity (matches the letterhead PDF format) ----------
const COMPANY = {
  nameUpper: 'ALBERT PAMONAG ENGINEERING CONSULTANCY',
  reportTitle: 'APEC Structural Calculation Report',
  addressLine: 'Unit 510, 5th Floor, EEC Building, Bayani Road, Western Bicutan, Taguig City',
  phone: '+63.917.899.6340',
  email: 'albert@apeconsultancy.net',
  website: 'www.apeconsultancy.net',
};
const LG = '#1f5130', LG_DARK = '#143a22';
// Only the emblem (leaf mark) is rasterised; the wordmark is drawn with jsPDF
// text so it can never clip regardless of the browser's SVG font rendering.
const EMBLEM_VB = { w: 160, h: 108 };
function apecEmblemSVGMarkup(widthPx) {
  const h = widthPx * (EMBLEM_VB.h / EMBLEM_VB.w);
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${widthPx}" height="${h}" viewBox="0 0 ${EMBLEM_VB.w} ${EMBLEM_VB.h}">` +
    `<path d="M14 100 C 64 90, 116 88, 150 94 C 116 102, 64 104, 14 100 Z" fill="${LG_DARK}"/>` +
    `<path d="M14 102 C 12 56, 62 22, 150 6 C 118 40, 94 70, 90 102 C 64 102, 38 102, 14 102 Z" fill="${LG}"/>` +
    `<path d="M62 82 C 72 52, 94 32, 126 18 C 108 42, 94 60, 88 86 C 78 84, 70 84, 62 82 Z" fill="#ffffff"/></svg>`
  );
}

// Plotly green palette (shades of green for all figures).
const GP = { primary: '#1f5130', mid: '#2e7d4f', light: '#6aa84f',
             accent: '#8fbf73', dark: '#143a22', faint: '#cfe3d4',
             marker: '#143a22', wind: '#3f7d3f' };

createApp({
  delimiters: ['[[', ']]'],

  data() {
    return {
      activeTab: 'wind',
      // --- wind ---
      tables: window.MOP113_TABLES,
      presets: window.MOP113_PRESETS,
      f: clone(window.MOP113_PRESETS.PI_STACK),  // start on the stacked preset
      result: null,
      error: null,
      loading: false,
      exporting: false,
      calcPulse: false,
      _timer: null,
      // --- seismic ---
      seismicPresets: window.MOP113_SEISMIC_PRESETS,
      s: clone(window.MOP113_SEISMIC_PRESETS.Z4_SMRF),
      sresult: null,
      serror: null,
      sloading: false,
      _stimer: null,
      // --- V conversion (NSCP -> MOP) ---
      vconvMeta: window.MOP113_VCONV_META,
      vconvPresets: window.MOP113_VCONV_PRESETS,
      vc: clone(window.MOP113_VCONV_PRESETS.MOP_50YR),
      vresult: null,
      verror: null,
      vloading: false,
      _vtimer: null,
    };
  },

  watch: {
    f: { handler() { this.scheduleCompute(); }, deep: true },
    s: { handler() { this.scheduleSeismic(); }, deep: true },
    vc: { handler() { this.scheduleVconv(); }, deep: true },
  },

  mounted() { this.compute(); this.computeSeismic(); this.computeVconv(); },

  methods: {
    /* ---------- explicit recalculate (visible loading) ---------- */
    recalc() {
      this.calcPulse = true;
      const fn = this.activeTab === 'wind' ? this.compute
        : this.activeTab === 'vconv' ? this.computeVconv : this.computeSeismic;
      // keep the spinner visible briefly so the recompute is clearly seen
      Promise.all([fn.call(this), new Promise(r => setTimeout(r, 450))])
        .finally(() => { this.calcPulse = false; });
    },

    /* ---------- tab switching ---------- */
    switchTab(tab) {
      this.activeTab = tab;
      // Re-typeset + resize the now-visible tab's figures (they may have been
      // laid out at zero width while the pane was display:none).
      this.$nextTick(() => {
        this.typeset();
        const byTab = {
          wind: [this.$refs.figStack, this.$refs.figForce, this.$refs.figKz],
          vconv: [this.$refs.figVconv],
          seismic: [this.$refs.figSpectrum, this.$refs.figADRS],
        };
        (byTab[tab] || []).forEach(el => el && window.Plotly && Plotly.Plots.resize(el));
      });
    },

    /* ---------- labels ---------- */
    kindLabel(k) { return KIND_LABELS[k] || k; },
    presetTag(key) { return key.replace('_STACK', '+stack'); },

    /* ---------- GRF band labels (Tables 3-4a/3-4b) ---------- */
    band34ft(arr, i) {
      const u = arr[i].upper;
      return i === 0 ? `≤${u}` : `>${arr[i - 1].upper} to ${u}`;
    },
    band34m(arr, i) {
      const m = (x) => (x * 0.3048).toFixed(2);
      const u = arr[i].upper;
      return i === 0 ? `≤${m(u)}` : `>${m(arr[i - 1].upper)} to ${m(u)}`;
    },

    /* ---------- per-element result lookup (for live captions) ---------- */
    elResult(i) { return this.result ? this.result.elements[i] : null; },

    /* ---------- presets / stack management ---------- */
    loadPreset(key) { this.f = clone(this.presets[key]); },

    // Physical height (mm) of an element, by kind.
    _elemHeightMm(el) {
      if (el.kind === 'equipment_circular' || el.kind === 'equipment_rectangular')
        return (Number(el.z_tip_mm) || 0) - (Number(el.z_base_mm) || 0);
      if (el.kind === 'pedestal_plinth') return Number(el.height_mm) || 0;
      if (el.kind === 'lattice_truss')
        return Number(el.route === 'A' ? el.face_height_mm : el.L_mm) || 0;
      return 0;
    },
    // Shift an element vertically by dz (mm), keeping its own height.
    _shiftElem(el, dz) {
      el.z_base_mm = (Number(el.z_base_mm) || 0) + dz;
      if (el.z_tip_mm != null) el.z_tip_mm = (Number(el.z_tip_mm) || 0) + dz;
    },
    // Current top-of-stack elevation (mm).
    _stackTop() {
      return this.f.elements.reduce(
        (mx, el) => Math.max(mx, (Number(el.z_base_mm) || 0) + this._elemHeightMm(el)), 0);
    },

    addElement() {
      // New equipment sits ON TOP of the current stack (heights follow).
      const top = this._stackTop();
      this.f.elements.unshift({
        label: 'New equipment', kind: 'equipment_circular',
        z_base_mm: top, z_tip_mm: top + 3000, D_mm: 300, cf: 0.9,
        kz_basis: 'tip', grf_type: 'rigid',
      });
    },

    // Re-seat every element so each sits on the one below it (no gaps/overlap).
    // List is top→bottom, so we stack from the last (bottom) element up.
    autoStack() {
      let z = 0;
      for (let i = this.f.elements.length - 1; i >= 0; i--) {
        const el = this.f.elements[i];
        const h = this._elemHeightMm(el);
        this._shiftElem(el, z - (Number(el.z_base_mm) || 0));  // move base to z
        z += h;
      }
    },

    removeElement(i) { this.f.elements.splice(i, 1); },
    move(i, d) {
      const j = i + d;
      if (j < 0 || j >= this.f.elements.length) return;
      const arr = this.f.elements;
      [arr[i], arr[j]] = [arr[j], arr[i]];
    },

    onKindChange(el) {
      // Suggest a Cf and seed sensible defaults for the now-relevant fields.
      if (CF_SUGGEST[el.kind] !== undefined) el.cf = CF_SUGGEST[el.kind];
      if (el.kind === 'equipment_circular' && !el.D_mm) el.D_mm = 345;
      if (el.kind === 'equipment_rectangular') {
        if (!el.WX_mm) el.WX_mm = 1000;
        if (!el.WY_mm) el.WY_mm = 1000;
        if (!el.z_tip_mm) el.z_tip_mm = 3000;
      }
      if (el.kind === 'pedestal_plinth') {
        if (!el.width_mm) el.width_mm = 700;
        if (!el.height_mm) el.height_mm = 200;
      }
      if (el.kind === 'lattice_truss') {
        if (!el.route) el.route = 'A';
        if (!el.face_width_mm) el.face_width_mm = 500;
        if (!el.face_height_mm) el.face_height_mm = 2000;
        if (!el.L_mm) el.L_mm = 2000;
        if (el.phi === undefined) el.phi = 0.2;
        if (!el.phi_mode) el.phi_mode = 'direct';
        if (!el.phi_members) el.phi_members = [{ b_mm: 90, L_mm: 2000, n: 4 }];
        if (!el.cross_section) el.cross_section = 'square';
        if (!el.member_type) el.member_type = 'flat';
        if (!el.members) el.members = [{ b_mm: 90, L_mm: 2000, n: 4, shape: 'flat' }];
        if (!el.panel_count) el.panel_count = 5;
      }
    },

    // Replicate one lattice panel into N stacked lattice elements, each sitting
    // a panel-height above the last so each is evaluated at its own Kz/height.
    stackPanels(ei) {
      const el = this.f.elements[ei];
      const n = Math.max(2, Math.min(20, parseInt(el.panel_count, 10) || 5));
      const h = Number(el.route === 'A' ? el.face_height_mm : el.L_mm) || 0;
      if (!h) { this.error = 'Set the panel height (face height / support height L) first.'; return; }
      const base = Number(el.z_base_mm) || 0;
      const origTop = base + h;          // top of the single panel before expanding
      const added = (n - 1) * h;          // extra height the stack now occupies
      // Anything that was sitting on top of this panel moves up so it still sits
      // on top of the expanded truss (heights follow).
      this.f.elements.forEach((other, j) => {
        if (j !== ei && (Number(other.z_base_mm) || 0) >= origTop - 1e-6)
          this._shiftElem(other, added);
      });
      const baseLabel = String(el.label || 'Truss').replace(/\s*P\d+$/, '');
      const copies = [];
      for (let i = 0; i < n; i++) {
        const c = clone(el);
        delete c.panel_count;
        c.z_base_mm = base + i * h;
        c.label = `${baseLabel} P${i + 1}`;
        copies.push(c);
      }
      this.f.elements.splice(ei, 1, ...copies);
    },

    /* ---------- debounced API ---------- */
    scheduleCompute() {
      this.loading = true;
      clearTimeout(this._timer);
      this._timer = setTimeout(() => this.compute(), 250);
    },

    async compute() {
      this.loading = true;
      try {
        const resp = await fetch('/api/calculate', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.f),
        });
        const data = await resp.json();
        if (!resp.ok) { this.error = data.error || 'Calculation error.'; }
        else {
          this.error = null;
          this.result = data;
          await this.$nextTick();
          this.typeset();
          this.drawFigures();
        }
      } catch (e) {
        this.error = 'Could not reach the calculation API: ' + e.message;
      } finally { this.loading = false; }
    },

    /* ---------- MathJax ---------- */
    typeset() {
      if (window.MathJax && window.MathJax.typesetPromise) {
        const el = document.getElementById('app');
        if (MathJax.typesetClear) MathJax.typesetClear([el]);
        MathJax.typesetPromise([el]).catch(() => {});
      }
    },

    /* ---------- figures ---------- */
    drawFigures() {
      const fig = this.result.figures;
      this.drawStack(fig.stack);
      this.drawForce(fig.force_breakdown);
      this.drawKz(fig.kz_curve);
    },

    drawStack(s) {
      const shapes = [];
      const annotations = [];
      const fillByKind = { lattice_truss: 'rgba(46,125,79,0.20)',
                           pedestal_plinth: 'rgba(20,58,34,0.22)' };
      const lineByKind = { lattice_truss: GP.mid, pedestal_plinth: GP.dark };
      const halfMax = Math.max(s.w_max, 0.4) * 1.7;

      // Ground line (NGL).
      shapes.push({ type: 'line', x0: -halfMax, x1: halfMax, y0: 0, y1: 0,
        line: { color: GP.primary, width: 2, dash: 'dot' } });
      annotations.push({ x: 0, y: 0, ax: 0, ay: 14, showarrow: false,
        text: 'NGL', xanchor: 'center', font: { size: 12, color: GP.primary } });

      s.elements.forEach((e) => {
        const fill = fillByKind[e.kind] || 'rgba(106,168,79,0.22)';
        const line = lineByKind[e.kind] || GP.primary;
        shapes.push({ type: 'rect', x0: e.x0, x1: e.x1, y0: e.y0, y1: e.y1,
          line: { color: line, width: 2 }, fillcolor: fill });
        const midY = (e.y0 + e.y1) / 2;
        // Info label OUTSIDE to the right, anchored by a fixed pixel offset so it
        // never overlaps the (often very narrow) element body or its neighbours.
        annotations.push({ x: e.x1, y: midY, ax: 92, ay: 0, showarrow: true,
          arrowhead: 2, arrowsize: 1, arrowwidth: 1.2, arrowcolor: '#9fb3a5',
          xanchor: 'left', align: 'left',
          text: `<b>${e.label}</b><br><span style="font-size:11px">K<sub>z</sub>=${e.Kz} · F=${e.F} kN</span>`,
          font: { size: 14, color: line }, bgcolor: 'rgba(255,255,255,0.85)',
          borderpad: 2 });
        // Elevation tick on the left (bigger, pixel offset, no overlap).
        annotations.push({ x: e.x0, y: e.y1, ax: -8, ay: 0, showarrow: false,
          xanchor: 'right', text: `${e.z_top} m`, font: { size: 11, color: '#52635a' } });
      });
      // base elevation of the lowest element
      const last = s.elements[s.elements.length - 1];
      annotations.push({ x: last.x0, y: last.y0, ax: -8, ay: 0, showarrow: false,
        xanchor: 'right', text: `${last.z_base} m`, font: { size: 11, color: '#52635a' } });

      // Wind arrow on the windward (left) side — pixel offset, plain text label.
      const wy = s.z_max * 0.62;
      annotations.push({ x: -halfMax * 0.5, y: wy, ax: -52, ay: 0, showarrow: true,
        arrowhead: 3, arrowsize: 1.8, arrowwidth: 2.4, arrowcolor: GP.light, text: '' });
      annotations.push({ x: -halfMax * 0.5, y: wy, ax: -52, ay: -16, showarrow: false,
        xanchor: 'left', text: 'WIND', font: { size: 13, color: GP.mid } });

      const layout = {
        // Axis titles omitted (units are in the caption); a rotated single-letter
        // y-title is mangled by html2canvas in the PDF capture.
        xaxis: { range: [-halfMax * 1.35, halfMax * 1.35], zeroline: false,
                 scaleanchor: 'y', scaleratio: 1, ticksuffix: ' m' },
        yaxis: { range: [-s.z_max * 0.08, s.z_max * 1.12], ticksuffix: ' m' },
        shapes, annotations, showlegend: false,
        margin: { t: 20, r: 120, b: 40, l: 64 }, height: 480,
      };
      Plotly.react(this.$refs.figStack, [], layout, { responsive: true, displaylogo: false });
    },

    drawForce(fb) {
      const traces = [
        { x: fb.labels, y: fb.Fx, name: 'F_X', type: 'bar', marker: { color: GP.primary },
          text: fb.Fx.map(v => v.toFixed(1)), textposition: 'auto' },
        { x: fb.labels, y: fb.Fy, name: 'F_Y', type: 'bar', marker: { color: GP.light },
          text: fb.Fy.map(v => v.toFixed(1)), textposition: 'auto' },
      ];
      const layout = {
        barmode: 'group',
        title: { text: `Total F<sub>X</sub>=${fb.FX_total.toFixed(2)} kN, ` +
                       `F<sub>Y</sub>=${fb.FY_total.toFixed(2)} kN (${fb.governing} governs)`,
                 font: { size: 13 } },
        yaxis: { title: 'Force (kN)' }, xaxis: { title: 'Element' },
        margin: { t: 40, r: 10, b: 50, l: 55 }, height: 340,
        legend: { orientation: 'h', y: -0.25 },
      };
      Plotly.react(this.$refs.figForce, traces, layout, { responsive: true, displaylogo: false });
    },

    drawKz(kz) {
      const traces = [
        { x: kz.heights, y: kz.kz, mode: 'lines+markers', name: `Table 3-1 (Exp. ${kz.exposure})`,
          line: { color: GP.mid }, marker: { size: 6 } },
        { x: kz.points.map(p => p.h_ft), y: kz.points.map(p => p.kz), mode: 'markers+text',
          name: 'elements', text: kz.points.map(p => p.label),
          textposition: kz.points.map((p, i) => i % 2 ? 'bottom center' : 'top center'),
          textfont: { size: 10, color: GP.dark },
          marker: { color: GP.dark, size: 12, symbol: 'star' } },
      ];
      const layout = {
        title: { text: 'K<sub>z</sub> vs height — each element highlighted', font: { size: 13 } },
        xaxis: { title: 'Height z (ft)' }, yaxis: { title: 'K<sub>z</sub>' },
        margin: { t: 40, r: 10, b: 50, l: 55 }, height: 340,
        legend: { orientation: 'h', y: -0.25 },
      };
      Plotly.react(this.$refs.figKz, traces, layout, { responsive: true, displaylogo: false });
    },

    /* ================= SEISMIC ================= */
    loadSeismicPreset(key) { this.s = clone(this.seismicPresets[key]); },

    scheduleSeismic() {
      this.sloading = true;
      clearTimeout(this._stimer);
      this._stimer = setTimeout(() => this.computeSeismic(), 250);
    },

    async computeSeismic() {
      this.sloading = true;
      try {
        const resp = await fetch('/api/seismic', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.s),
        });
        const data = await resp.json();
        if (!resp.ok) { this.serror = data.error || 'Calculation error.'; }
        else {
          this.serror = null;
          this.sresult = data;
          await this.$nextTick();
          this.typeset();
          this.drawSeismicFigures();
        }
      } catch (e) {
        this.serror = 'Could not reach the seismic API: ' + e.message;
      } finally { this.sloading = false; }
    },

    drawSeismicFigures() {
      const fig = this.sresult.figures;
      this.drawSpectrum(fig.spectrum);
      this.drawADRS(fig.adrs);
    },

    drawSpectrum(sp) {
      // NSCP design response spectrum: Sa vs actual period T, + 1.4x envelope.
      const traces = [
        { x: sp.T, y: sp.Sa, mode: 'lines', name: 'Design spectrum',
          line: { color: GP.primary, width: 2 } },
        { x: sp.T, y: sp.Sa_14, mode: 'lines', name: '1.4× (TH reference)',
          line: { color: GP.accent, width: 1, dash: 'dash' } },
        { x: [sp.struct_T], y: [sp.struct_Sa], mode: 'markers',
          name: 'Structure (T, Sₐ)',
          marker: { color: GP.dark, size: 14, symbol: 'star' } },
      ];
      // place the two callouts on opposite sides so their text never overlaps
      const tsRight = sp.Ts < (Math.max(...sp.T) * 0.6);
      const layout = {
        title: { text: `NSCP design spectrum — S<sub>a,max</sub>=${sp.sa_max.toFixed(3)} g, ` +
                       `T₀=${sp.T0.toFixed(3)} s, Tₛ=${sp.Ts.toFixed(3)} s`, font: { size: 13 } },
        xaxis: { title: 'Period T (s)' }, yaxis: { title: 'Sₐ (g)', rangemode: 'tozero' },
        margin: { t: 40, r: 10, b: 50, l: 55 }, height: 340,
        legend: { orientation: 'h', y: -0.28 },
        shapes: [
          { type: 'line', x0: sp.T0, x1: sp.T0, y0: 0, y1: sp.sa_max,
            line: { color: '#9bb0a2', width: 1, dash: 'dot' } },
          { type: 'line', x0: sp.Ts, x1: sp.Ts, y0: 0, y1: sp.sa_max,
            line: { color: '#9bb0a2', width: 1, dash: 'dot' } },
        ],
        annotations: [
          { x: sp.Ts, y: sp.sa_max, text: `Tₛ=${sp.Ts.toFixed(3)}`, showarrow: true,
            arrowhead: 3, ax: tsRight ? 34 : -34, ay: -20,
            font: { size: 10, color: GP.mid } },
          { x: sp.struct_T, y: sp.struct_Sa, ax: tsRight ? -36 : 38, ay: 34, arrowhead: 3,
            showarrow: true, font: { color: GP.dark, size: 10 },
            text: `(${sp.struct_T.toFixed(2)}, ${sp.struct_Sa.toFixed(3)})` },
        ],
      };
      Plotly.react(this.$refs.figSpectrum, traces, layout, { responsive: true, displaylogo: false });
    },

    drawADRS(ad) {
      const traces = [
        { x: ad.Sd, y: ad.Sa, mode: 'lines', name: 'Elastic ADRS',
          line: { color: GP.primary, width: 2 } },
      ];
      // radial constant-period lines
      ad.radial.forEach(rl => {
        traces.push({ x: rl.x, y: rl.y, mode: 'lines', name: `T=${rl.T}s`,
          line: { color: GP.faint, width: 1 }, hoverinfo: 'name', showlegend: false });
      });
      if (ad.reduced) {
        traces.push({ x: ad.reduced.Sd, y: ad.reduced.Sa, mode: 'lines',
          name: 'Reduced ADRS (ATC-40)', line: { color: GP.light, width: 2, dash: 'dot' } });
        if (ad.reduced.apn != null) {
          traces.push({ x: [ad.reduced.dpi], y: [ad.reduced.apn], mode: 'markers',
            name: 'Performance point',
            marker: { color: GP.dark, size: 13, symbol: 'star' } });
        }
      }
      const layout = {
        title: { text: 'ADRS — Sₐ vs S_d', font: { size: 13 } },
        xaxis: { title: 'Spectral displacement S_d (m)', rangemode: 'tozero' },
        yaxis: { title: 'Sₐ (g)', rangemode: 'tozero' },
        margin: { t: 40, r: 10, b: 50, l: 55 }, height: 360,
        legend: { orientation: 'h', y: -0.25 },
      };
      Plotly.react(this.$refs.figADRS, traces, layout, { responsive: true, displaylogo: false });
    },

    /* ================= V CONVERSION (NSCP -> MOP) ================= */
    vconvTag(key) { return key.replace('_', ' '); },
    ifwInTable(v) { return this.vconvMeta.ifw.some(r => Math.abs(r.ifw - v) < 1e-9); },
    loadVconvPreset(key) { this.vc = clone(this.vconvPresets[key]); },

    scheduleVconv() {
      this.vloading = true;
      clearTimeout(this._vtimer);
      this._vtimer = setTimeout(() => this.computeVconv(), 250);
    },

    async computeVconv() {
      this.vloading = true;
      try {
        const resp = await fetch('/api/vconvert', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.vc),
        });
        const data = await resp.json();
        if (!resp.ok) { this.verror = data.error || 'Conversion error.'; }
        else {
          this.verror = null;
          this.vresult = data;
          await this.$nextTick();
          this.typeset();
          this.drawVconv(data.figures.speed_bar);
        }
      } catch (e) {
        this.verror = 'Could not reach the conversion API: ' + e.message;
      } finally { this.vloading = false; }
    },

    drawVconv(sb) {
      const traces = [{
        x: sb.labels, y: sb.kph, type: 'bar',
        marker: { color: [GP.light, GP.primary] },
        text: sb.kph.map((v, i) => `${v.toFixed(1)} kph<br>${sb.ms[i].toFixed(2)} m/s`),
        textposition: 'auto',
      }];
      const layout = {
        title: { text: 'NSCP 2015 vs MOP 113 basic wind speed', font: { size: 13 } },
        yaxis: { title: 'V (kph)' },
        margin: { t: 40, r: 10, b: 40, l: 55 }, height: 340, showlegend: false,
      };
      Plotly.react(this.$refs.figVconv, traces, layout, { responsive: true, displaylogo: false });
    },

    useVinWind() {
      // Copy the converted MOP speed into the Wind tab and switch to it.
      this.f.V_kph = Math.round(this.vresult.summary.V_mop_kph * 100) / 100;
      this.switchTab('wind');
    },

    /* ================= PDF EXPORT (APEC letterhead) ================= */
    // Rasterise just the APEC emblem (leaf mark) to a PNG data URL.
    async _rasteriseLogo() {
      try {
        const widthPx = 480;
        const heightPx = Math.round(widthPx * (EMBLEM_VB.h / EMBLEM_VB.w));
        const url = 'data:image/svg+xml;charset=utf-8,' +
          encodeURIComponent(apecEmblemSVGMarkup(widthPx));
        const img = new Image();
        img.width = widthPx; img.height = heightPx;   // explicit size = reliable raster
        await new Promise((res, rej) => { img.onload = res; img.onerror = rej; img.src = url; });
        const c = document.createElement('canvas');
        c.width = widthPx; c.height = heightPx;
        c.getContext('2d').drawImage(img, 0, 0, widthPx, heightPx);
        return c.toDataURL('image/png');
      } catch { return null; }
    },

    _drawHeader(pdf, pageW, margin, logoPng, projectName) {
      const GREEN = [34, 139, 34], LOGO_GREEN = [31, 81, 48], GRAY = [150, 150, 150];
      let x = margin;
      if (logoPng) {
        const emH = 7, emW = emH * (EMBLEM_VB.w / EMBLEM_VB.h);  // ~10.4 mm wide
        pdf.addImage(logoPng, 'PNG', margin, margin, emW, emH);
        x = margin + emW + 2.5;
      }
      // Wordmark drawn as real PDF text — measured & placed, so it never clips.
      pdf.setFont('times', 'bold'); pdf.setFontSize(11);
      pdf.setTextColor(...LOGO_GREEN);
      pdf.text(COMPANY.nameUpper, x, margin + 5.4);

      pdf.setFont('helvetica', 'normal'); pdf.setFontSize(8); pdf.setTextColor(...GRAY);
      pdf.text(projectName || '', pageW - margin, margin + 5.5, { align: 'right' });
      pdf.setDrawColor(...GREEN); pdf.setLineWidth(0.4);
      pdf.line(margin, margin + 8, pageW - margin, margin + 8);
    },

    _drawFooter(pdf, pageW, pageH, margin, page, total) {
      const GREEN = [34, 139, 34], GRAY = [150, 150, 150];
      const cx = pageW / 2; let y = pageH - 20;
      pdf.setDrawColor(...GREEN); pdf.setLineWidth(0.3);
      pdf.line(margin, y - 4, pageW - margin, y - 4);
      pdf.setTextColor(...GREEN); pdf.setFont('helvetica', 'bold'); pdf.setFontSize(9);
      pdf.text(COMPANY.nameUpper, cx, y, { align: 'center' }); y += 4;
      pdf.setFont('helvetica', 'normal'); pdf.setFontSize(7.5);
      pdf.text(COMPANY.addressLine, cx, y, { align: 'center' }); y += 3.6;
      pdf.text(`Tel  ${COMPANY.phone}      Email  ${COMPANY.email}      Web  ${COMPANY.website}`,
               cx, y, { align: 'center' }); y += 4;
      pdf.setTextColor(...GRAY); pdf.setFontSize(7);
      pdf.text(`Page ${page} of ${total}`, cx, y, { align: 'center' });
    },

    _projectName() {
      if (this.activeTab === 'wind') return 'Wind Load — ASCE MOP 113 (Eq. 3-1)';
      if (this.activeTab === 'vconv') return `Wind Speed NSCP→MOP — ${this.vc.tag || ''}`;
      return 'Seismic — NSCP 2015 Section 208';
    },

    // Main entry — replaces window.print(): capture the active report and
    // slice it across A4 pages with the green APEC header/footer on each page.
    async printReport() {
      if (!window.jspdf || !window.html2canvas) { window.print(); return; }
      // resize the active tab's Plotly figures so they capture sharply
      const byTab = {
        wind: [this.$refs.figStack, this.$refs.figForce, this.$refs.figKz],
        vconv: [this.$refs.figVconv],
        seismic: [this.$refs.figSpectrum, this.$refs.figADRS],
      };
      (byTab[this.activeTab] || []).forEach(el => el && Plotly.Plots.resize(el));

      // the visible report column
      const node = [...document.querySelectorAll('.sticky-col')]
        .find(el => el.offsetParent !== null);
      if (!node) return;

      this.exporting = true;
      // unclamp the sticky/scroll box so html2canvas sees the full content
      const prev = { position: node.style.position, max: node.style.maxHeight,
                     overflow: node.style.overflow };
      node.style.position = 'static'; node.style.maxHeight = 'none';
      node.style.overflow = 'visible';
      try {
        await this.$nextTick();
        const [canvas, logoPng] = await Promise.all([
          html2canvas(node, { scale: 2, useCORS: true, backgroundColor: '#ffffff',
            logging: false,
            ignoreElements: (el) => el.classList && el.classList.contains('no-print') }),
          this._rasteriseLogo(),
        ]);

        const { jsPDF } = window.jspdf;
        const pdf = new jsPDF({ unit: 'mm', format: 'a4', orientation: 'portrait' });
        const pageW = pdf.internal.pageSize.getWidth();
        const pageH = pdf.internal.pageSize.getHeight();
        const margin = 10, headerH = 12, footerH = 26;
        const usableW = pageW - margin * 2;
        const bodyTop = margin + headerH;
        const bodyAvailH = (pageH - footerH) - bodyTop;
        const pxPerMm = canvas.width / usableW;
        const pageBodyPx = bodyAvailH * pxPerMm;
        const totalPages = Math.max(1, Math.ceil(canvas.height / pageBodyPx));
        const project = this._projectName();

        for (let p = 0; p < totalPages; p++) {
          if (p > 0) pdf.addPage();
          const sliceY = p * pageBodyPx;
          const sliceH = Math.min(pageBodyPx, canvas.height - sliceY);
          const slice = document.createElement('canvas');
          slice.width = canvas.width; slice.height = sliceH;
          slice.getContext('2d').drawImage(canvas, 0, sliceY, canvas.width, sliceH,
                                            0, 0, canvas.width, sliceH);
          // JPEG (q=0.92) keeps the figure/text legible while keeping the
          // file small — full-page PNG slices at scale 2 balloon to tens of MB.
          pdf.addImage(slice.toDataURL('image/jpeg', 0.92), 'JPEG', margin, bodyTop,
                       usableW, sliceH / pxPerMm);
          this._drawHeader(pdf, pageW, margin, logoPng, project);
          this._drawFooter(pdf, pageW, pageH, margin, p + 1, totalPages);
        }
        pdf.save(`APEC-${this.activeTab}-report.pdf`);
      } catch (e) {
        this.serror = this.error = 'PDF export failed: ' + e.message;
      } finally {
        node.style.position = prev.position; node.style.maxHeight = prev.max;
        node.style.overflow = prev.overflow;
        this.exporting = false;
      }
    },
  },
}).mount('#app');
