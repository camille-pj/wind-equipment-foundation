/* ------------------------------------------------------------------ *
 * app.js -- Vue 3 front end for the MOP 113 wind-load calculator.
 *
 * Responsibilities:
 *   - hold the reactive form state (left column);
 *   - debounce-POST to /api/calculate on any change;
 *   - re-run MathJax.typesetPromise() so the LaTeX report renders;
 *   - draw the three figures with Plotly.react().
 *
 * The Flask engine (wind_mop113.py) is the source of truth for all numbers;
 * this file renders only what the engine returns.
 * ------------------------------------------------------------------ */
const { createApp } = Vue;

// Suggested body Cf when the shape selector changes (Table 3-9).
const CF_SUGGEST = { circular: 0.9, rectangular: 2.0 };

createApp({
  // Use [[ ]] so Vue does not collide with Jinja's {{ }}.
  delimiters: ['[[', ']]'],

  data() {
    return {
      tables: window.MOP113_TABLES,
      presets: window.MOP113_PRESETS,
      qConst: window.MOP113_TABLES.q_const,
      f: { ...window.MOP113_PRESETS.PI },   // start on the validated PI preset
      result: null,
      error: null,
      loading: false,
      _timer: null,
    };
  },

  watch: {
    // Deep-watch the whole form: any field change schedules a recompute.
    f: { handler() { this.scheduleCompute(); }, deep: true },
  },

  mounted() {
    this.compute();   // initial render with the PI preset
  },

  methods: {
    /* ---------- preset / shape helpers ---------- */
    loadPreset(key) {
      // Replace form state wholesale; the deep watcher triggers a recompute.
      this.f = { ...this.presets[key] };
    },

    onShapeChange() {
      // Auto-suggest the body Cf from the new shape (user may still override).
      const s = this.f.shape;
      if (CF_SUGGEST[s] !== undefined) this.f.cf_body = CF_SUGGEST[s];
      // Seed sensible defaults for the now-visible fields if blank.
      if (s === 'circular' && !this.f.D_mm) this.f.D_mm = 345;
      if (s === 'rectangular') {
        if (!this.f.WX_mm) this.f.WX_mm = 1000;
        if (!this.f.WY_mm) this.f.WY_mm = 1000;
      }
    },

    /* ---------- debounced API call ---------- */
    scheduleCompute() {
      this.loading = true;
      clearTimeout(this._timer);
      this._timer = setTimeout(() => this.compute(), 250);   // ~250 ms debounce
    },

    async compute() {
      this.loading = true;
      try {
        const resp = await fetch('/api/calculate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.f),
        });
        const data = await resp.json();
        if (!resp.ok) {
          this.error = data.error || 'Calculation error.';
        } else {
          this.error = null;
          this.result = data;
          // Wait for Vue to patch the DOM, then typeset maths + draw figures.
          await this.$nextTick();
          this.typeset();
          this.drawFigures();
        }
      } catch (e) {
        this.error = 'Could not reach the calculation API: ' + e.message;
      } finally {
        this.loading = false;
      }
    },

    /* ---------- MathJax ---------- */
    renderTitle(t) {
      // Step titles may contain inline $...$; render them in-place after typeset.
      return t;
    },

    typeset() {
      if (window.MathJax && window.MathJax.typesetPromise) {
        const el = document.getElementById('app');  // covers header + report
        MathJax.typesetClear && MathJax.typesetClear([el]);
        MathJax.typesetPromise(el ? [el] : undefined).catch(() => {});
      }
    },

    /* ---------- Plotly figures ---------- */
    drawFigures() {
      const fig = this.result.figures;
      this.drawForce(fig.force_breakdown);
      this.drawKz(fig.kz_curve);
      this.drawSchematic(fig.schematic);
    },

    drawForce(fb) {
      const gov = fb.governing;
      const traces = [
        { x: fb.directions, y: fb.body, name: 'Body', type: 'bar',
          marker: { color: '#0d6efd' },
          text: fb.body.map(v => v.toFixed(2)), textposition: 'auto' },
        { x: fb.directions, y: fb.plinth, name: 'Plinth', type: 'bar',
          marker: { color: '#6c757d' },
          text: fb.plinth.map(v => v.toFixed(2)), textposition: 'auto' },
      ];
      const layout = {
        barmode: 'group',
        title: { text: `Force breakdown — total F<sub>X</sub>=${fb.totals[0].toFixed(2)} kN, ` +
                       `F<sub>Y</sub>=${fb.totals[1].toFixed(2)} kN (${gov} governs)`,
                 font: { size: 13 } },
        yaxis: { title: 'Force (kN)' },
        xaxis: { title: 'Direction' },
        margin: { t: 40, r: 10, b: 40, l: 55 },
        legend: { orientation: 'h', y: -0.2 },
        height: 360,
      };
      Plotly.react(this.$refs.figForce, traces, layout, { responsive: true, displaylogo: false });
    },

    drawKz(kz) {
      const tableTrace = {
        x: kz.heights, y: kz.kz, mode: 'lines+markers', name: `Table 3-1 (Exp. ${kz.exposure})`,
        line: { color: '#0d6efd' }, marker: { size: 7 },
      };
      const tipTrace = {
        x: [kz.tip_h_ft], y: [kz.tip_kz], mode: 'markers', name: 'Tip height (interpolated)',
        marker: { color: '#dc3545', size: 14, symbol: 'star' },
      };
      const layout = {
        title: { text: `K<sub>z</sub> interpolation — h=${kz.tip_h_ft.toFixed(2)} ft → K<sub>z</sub>=${kz.tip_kz.toFixed(4)}`,
                 font: { size: 13 } },
        xaxis: { title: 'Height z (ft)' },
        yaxis: { title: 'K<sub>z</sub>' },
        margin: { t: 40, r: 10, b: 45, l: 55 },
        legend: { orientation: 'h', y: -0.25 },
        height: 360,
        annotations: [{
          x: kz.tip_h_ft, y: kz.tip_kz, ax: 40, ay: -40,
          text: `(${kz.tip_h_ft.toFixed(1)}, ${kz.tip_kz.toFixed(3)})`,
          showarrow: true, arrowhead: 3, font: { color: '#dc3545' },
        }],
      };
      Plotly.react(this.$refs.figKz, [tableTrace, tipTrace], layout, { responsive: true, displaylogo: false });
    },

    drawSchematic(s) {
      const shapes = [];
      const annotations = [];

      // Body rectangle.
      shapes.push({
        type: 'rect', x0: s.body.x0, x1: s.body.x1, y0: s.body.y0, y1: s.body.y1,
        line: { color: '#0d6efd', width: 2 }, fillcolor: 'rgba(13,110,253,0.18)',
      });
      // Plinth rectangle.
      if (s.plinth) {
        shapes.push({
          type: 'rect', x0: s.plinth.x0, x1: s.plinth.x1, y0: s.plinth.y0, y1: s.plinth.y1,
          line: { color: '#6c757d', width: 2 }, fillcolor: 'rgba(108,117,125,0.30)',
        });
      }
      // Ground line.
      const halfMax = Math.max(s.body_w_m, s.plinth_w_m, 0.5) * 1.6;
      shapes.push({
        type: 'line', x0: -halfMax, x1: halfMax, y0: 0, y1: 0,
        line: { color: '#198754', width: 2, dash: 'dot' },
      });

      // Tip-height dimension (vertical, right side).
      const dimX = s.body.x1 + halfMax * 0.25;
      shapes.push({ type: 'line', x0: dimX, x1: dimX, y0: 0, y1: s.H_m,
        line: { color: '#212529', width: 1 } });
      annotations.push({ x: dimX, y: s.H_m / 2, text: `H = ${s.H_m.toFixed(2)} m`,
        showarrow: false, textangle: -90, xanchor: 'left', font: { size: 11 } });

      // Body-width dimension (top).
      annotations.push({ x: 0, y: s.body.y1, ax: 0, ay: -22, showarrow: false,
        text: `width = ${s.body_w_m.toFixed(2)} m`, yanchor: 'bottom', font: { size: 11 } });
      annotations.push({ x: 0, y: (s.body.y0 + s.body.y1) / 2, showarrow: false,
        text: `${s.tag}<br>(${s.shape})`, font: { size: 12, color: '#0d6efd' } });
      if (s.plinth) {
        annotations.push({ x: 0, y: s.plinth.y1 / 2, showarrow: false,
          text: 'plinth', font: { size: 10, color: '#495057' } });
      }

      // Wind arrow (left -> body), with label.
      const windY = s.H_m * 0.7;
      annotations.push({
        x: s.body.x0, y: windY, ax: -halfMax * 0.9, ay: windY, axref: 'x', ayref: 'y',
        xref: 'x', yref: 'y', showarrow: true, arrowhead: 3, arrowsize: 1.6,
        arrowwidth: 2, arrowcolor: '#fd7e14',
      });
      annotations.push({ x: -halfMax * 0.9, y: windY + s.H_m * 0.06, showarrow: false,
        text: '🌬 WIND', font: { size: 12, color: '#fd7e14' }, xanchor: 'left' });

      const layout = {
        title: { text: 'Elevation (to scale, metres)', font: { size: 13 } },
        xaxis: { range: [-halfMax * 1.05, halfMax * 1.1], zeroline: false,
                 scaleanchor: 'y', scaleratio: 1, title: 'm' },
        yaxis: { range: [-s.H_m * 0.08, s.H_m * 1.12], title: 'm' },
        shapes, annotations,
        margin: { t: 40, r: 10, b: 40, l: 45 },
        height: 420, showlegend: false,
      };
      Plotly.react(this.$refs.figSchematic, [], layout, { responsive: true, displaylogo: false });
    },

    /* ---------- print ---------- */
    printReport() {
      // Resize Plotly figures to fit the page, then invoke the browser dialog.
      [this.$refs.figForce, this.$refs.figKz, this.$refs.figSchematic]
        .forEach(el => el && Plotly.Plots.resize(el));
      window.print();
    },
  },
}).mount('#app');
