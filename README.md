# Wind Equipment Foundation — MOP 113 Wind Load Calculator

A local web app that computes the wind load on a single substation equipment item and its
plinth following **ASCE MOP 113 (2007)**, *Substation Structure Design Guide*, governing
equation **Eq. 3-1**:

```
F = Q · Kz · V² · IFW · GRF · Cf · A
```

The calculation is done in two stages, exactly as the worked examples do:

1. **Velocity pressure** `qz = Q · Kz · V² · IFW`  → lb/ft², converted to kPa
2. **Wind force** `F = qz · GRF · Cf · A`  → lb, converted to kN

All arithmetic runs in US-customary units (`Q = 0.00256 lb/ft²/mph²`, `V` in mph, area in ft²);
only the *results* are converted to SI (kPa, kN) for display.

## Tech stack

- **Python + Flask** backend — the MOP 113 engine (`wind_mop113.py`) is the single source of truth.
- **Bootstrap 5** layout, **Vue 3** reactive front end, **MathJax 3** LaTeX report, **Plotly.js** figures (all via CDN).

## Run it

```bash
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>.

## Test it

```bash
pytest -v
```

The test suite reproduces three worked examples within ~0.5 %:

| Preset | Shape | Kz | qz (kPa) | FX (kN) | FY (kN) | FR (kN) |
|---|---|---|---|---|---|---|
| Post Insulator (PI) | circular | 0.981 | 5.131 | 13.648 | 13.648 | **19.300** |
| Current Transformer (CT) | circular | 1.002 | 5.241 | 19.363 | 19.363 | **27.381** |
| Circuit Breaker (CB) | rectangular | 0.984 | 5.145 | 234.281 | 193.391 | **303.789** |

## Project layout

```
.
├── app.py                 # Flask: serves index, exposes /api/calculate
├── wind_mop113.py         # Pure-Python MOP 113 engine (tables, lookups, calc)
├── test_wind_mop113.py    # pytest: validates the 3 worked presets
├── requirements.txt
├── templates/
│   └── index.html         # Bootstrap + Vue 3 + MathJax + Plotly (CDN)
└── static/
    └── app.js             # Vue app (form state, API calls, MathJax, Plotly)
```

---

*Calculation per ASCE MOP 113 (2007), Eq. 3-1. Verify all table-driven selections against the
governing project specification.*
