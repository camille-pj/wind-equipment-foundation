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
      _timer: null,
      // --- seismic ---
      seismicTables: window.MOP113_SEISMIC_TABLES,
      seismicPresets: window.MOP113_SEISMIC_PRESETS,
      s: clone(window.MOP113_SEISMIC_PRESETS.S1),
      sresult: null,
      serror: null,
      sloading: false,
      _stimer: null,
    };
  },

  watch: {
    f: { handler() { this.scheduleCompute(); }, deep: true },
    s: { handler() { this.scheduleSeismic(); }, deep: true },
  },

  mounted() { this.compute(); this.computeSeismic(); },

  methods: {
    /* ---------- tab switching ---------- */
    switchTab(tab) {
      this.activeTab = tab;
      // Re-typeset + resize the now-visible tab's figures (they may have been
      // laid out at zero width while the pane was display:none).
      this.$nextTick(() => {
        this.typeset();
        const refs = tab === 'wind'
          ? [this.$refs.figStack, this.$refs.figForce, this.$refs.figKz]
          : [this.$refs.figSpectrum, this.$refs.figSeisForce];
        refs.forEach(el => el && window.Plotly && Plotly.Plots.resize(el));
      });
    },

    /* ---------- labels ---------- */
    kindLabel(k) { return KIND_LABELS[k] || k; },
    presetTag(key) { return key.replace('_STACK', '+stack'); },

    /* ---------- per-element result lookup (for live captions) ---------- */
    elResult(i) { return this.result ? this.result.elements[i] : null; },

    /* ---------- presets / stack management ---------- */
    loadPreset(key) { this.f = clone(this.presets[key]); },

    addElement() {
      this.f.elements.push({
        label: 'New equipment', kind: 'equipment_circular',
        z_base_mm: 0, z_tip_mm: 3000, D_mm: 300, cf: 0.9,
        kz_basis: 'tip', grf_type: 'rigid',
      });
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
        if (!el.cross_section) el.cross_section = 'square';
        if (!el.member_type) el.member_type = 'flat';
        if (!el.members) el.members = [{ b_mm: 90, L_mm: 2000, n: 4, shape: 'flat' }];
      }
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
      const palette = { lattice_truss: 'rgba(108,117,125,0.35)',
                        pedestal_plinth: 'rgba(73,80,87,0.30)' };
      const halfMax = Math.max(s.w_max, 0.4) * 1.7;

      // Ground line (NGL).
      shapes.push({ type: 'line', x0: -halfMax, x1: halfMax, y0: 0, y1: 0,
        line: { color: '#198754', width: 2, dash: 'dot' } });
      annotations.push({ x: -halfMax, y: 0, text: 'NGL', showarrow: false,
        xanchor: 'left', yanchor: 'bottom', font: { size: 10, color: '#198754' } });

      s.elements.forEach((e) => {
        const fill = palette[e.kind] || 'rgba(13,110,253,0.18)';
        const line = e.kind === 'lattice_truss' ? '#6c757d' : '#0d6efd';
        shapes.push({ type: 'rect', x0: e.x0, x1: e.x1, y0: e.y0, y1: e.y1,
          line: { color: line, width: 2 }, fillcolor: fill });
        // Label + Kz + F inside/next to the element.
        annotations.push({ x: 0, y: (e.y0 + e.y1) / 2, showarrow: false,
          text: `${e.label}<br><span style="font-size:10px">K<sub>z</sub>=${e.Kz}, F=${e.F} kN</span>`,
          font: { size: 11, color: line } });
        // z annotations on the left.
        annotations.push({ x: e.x0, y: e.y1, showarrow: false, xanchor: 'right',
          text: `${e.z_top} m`, font: { size: 9, color: '#555' } });
      });
      // base elevation of the lowest element.
      annotations.push({ x: s.elements[s.elements.length - 1].x0,
        y: s.elements[s.elements.length - 1].y0, showarrow: false, xanchor: 'right',
        text: `${s.elements[s.elements.length - 1].z_base} m`, font: { size: 9, color: '#555' } });

      // Wind arrow on the windward side.
      const wy = s.z_max * 0.6;
      annotations.push({ x: -halfMax * 0.55, y: wy, ax: -halfMax * 0.95, ay: wy,
        xref: 'x', yref: 'y', axref: 'x', ayref: 'y', showarrow: true,
        arrowhead: 3, arrowsize: 1.5, arrowwidth: 2, arrowcolor: '#fd7e14' });
      annotations.push({ x: -halfMax * 0.95, y: wy + s.z_max * 0.05, showarrow: false,
        text: '🌬 WIND', xanchor: 'left', font: { size: 12, color: '#fd7e14' } });

      const layout = {
        xaxis: { range: [-halfMax * 1.1, halfMax * 1.1], zeroline: false,
                 scaleanchor: 'y', scaleratio: 1, title: 'm' },
        yaxis: { range: [-s.z_max * 0.08, s.z_max * 1.1], title: 'm' },
        shapes, annotations, showlegend: false,
        margin: { t: 20, r: 10, b: 40, l: 50 }, height: 420,
      };
      Plotly.react(this.$refs.figStack, [], layout, { responsive: true, displaylogo: false });
    },

    drawForce(fb) {
      const traces = [
        { x: fb.labels, y: fb.Fx, name: 'F_X', type: 'bar', marker: { color: '#0d6efd' },
          text: fb.Fx.map(v => v.toFixed(1)), textposition: 'auto' },
        { x: fb.labels, y: fb.Fy, name: 'F_Y', type: 'bar', marker: { color: '#20c997' },
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
          line: { color: '#0d6efd' }, marker: { size: 6 } },
        { x: kz.points.map(p => p.h_ft), y: kz.points.map(p => p.kz), mode: 'markers+text',
          name: 'elements', text: kz.points.map(p => p.label), textposition: 'top center',
          marker: { color: '#dc3545', size: 13, symbol: 'star' } },
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
      this.drawSeisForce(fig.force_bar);
    },

    drawSpectrum(sp) {
      const traces = [
        { x: sp.T, y: sp.Sa, mode: 'lines', name: 'Design spectrum',
          line: { color: '#0d6efd', width: 2 } },
        { x: [sp.struct_T], y: [sp.struct_Sa], mode: 'markers',
          name: 'Structure (T, Sₐ)',
          marker: { color: '#dc3545', size: 14, symbol: 'star' } },
      ];
      const layout = {
        title: { text: `Design response spectrum — S<sub>DS</sub>=${sp.SDS.toFixed(3)} g, ` +
                       `T₀=${sp.T0.toFixed(3)} s`, font: { size: 13 } },
        xaxis: { title: 'Period T (s)' }, yaxis: { title: 'Sₐ (g)', rangemode: 'tozero' },
        margin: { t: 40, r: 10, b: 50, l: 55 }, height: 340,
        legend: { orientation: 'h', y: -0.25 },
        shapes: [{ type: 'line', x0: sp.T0, x1: sp.T0, y0: 0, y1: sp.SDS,
                   line: { color: '#6c757d', width: 1, dash: 'dot' } }],
        annotations: [{ x: sp.T0, y: sp.SDS, text: `T₀=${sp.T0.toFixed(3)} s`,
                        showarrow: true, arrowhead: 3, ax: 30, ay: -25, font: { size: 10 } },
                      { x: sp.struct_T, y: sp.struct_Sa, ax: 35, ay: -35, arrowhead: 3,
                        showarrow: true, font: { color: '#dc3545', size: 10 },
                        text: `(${sp.struct_T.toFixed(2)}, ${sp.struct_Sa.toFixed(3)})` }],
      };
      Plotly.react(this.$refs.figSpectrum, traces, layout, { responsive: true, displaylogo: false });
    },

    drawSeisForce(fb) {
      const traces = [{
        x: fb.labels, y: fb.values, type: 'bar',
        marker: { color: ['#dc3545', '#fd7e14'] },
        text: fb.values.map(v => v.toFixed(2)), textposition: 'auto',
      }];
      const layout = {
        title: { text: 'Seismic force (kN)', font: { size: 13 } },
        yaxis: { title: 'Force (kN)' },
        margin: { t: 40, r: 10, b: 40, l: 55 }, height: 340, showlegend: false,
      };
      Plotly.react(this.$refs.figSeisForce, traces, layout, { responsive: true, displaylogo: false });
    },

    /* ---------- print (active tab) ---------- */
    printReport() {
      const refs = this.activeTab === 'wind'
        ? [this.$refs.figStack, this.$refs.figForce, this.$refs.figKz]
        : [this.$refs.figSpectrum, this.$refs.figSeisForce];
      refs.forEach(el => el && Plotly.Plots.resize(el));
      window.print();
    },
  },
}).mount('#app');
