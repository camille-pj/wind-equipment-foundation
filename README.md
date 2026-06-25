# Wind Equipment Foundation — MOP 113 Stacked Wind Load Calculator

A local web app that computes the wind load on a **stacked substation assembly** — main
equipment seated on a steel lattice support seated on a foundation (plus an optional
pedestal/plinth) — following **ASCE MOP 113 (2007)**, *Substation Structure Design Guide*,
governing equation **Eq. 3-1** in its **SI-native** form.

```
F = Q · Kz · V² · IFW · GRF · Cf · A        (Q = 0.613, V in m/s, A in m²)
```

## Unit policy (the key consistency rule)

Everything in the force math is computed **natively in SI** — no round-trip through US units.

- `Q = 0.613` (SI value printed in Eq. 3-1); `V`: kph → m/s via `V_ms = V_kph / 3.6`.
- Lengths mm → m; areas m²; pressures **kPa**; forces **kN**; line loads **kN/m**; moments **kN·m**.
- Feet are used **only** to read the height-tabulated tables (3-1, 3-4a, 3-4b): `ft = m × 3.28084`.

> MOP 113 prints `Q = 0.00256` (US) and `Q = 0.613` (SI). These are not exact twins, so SI-native
> results differ from US-unit hand calcs by ~0.1% (negligible). This tool uses the SI form throughout.

The calculation runs as a four-stage chain so the user can read off each quantity:

1. velocity pressure `qz = Q · Kz · V_ms² · IFW` → kPa
2. design pressure `p = qz · GRF · Cf` → kPa (= F/A)
3. line load `w = p · b` → kN/m (`b` = projected width normal to the wind)
4. element force `F = p · A = w · L` → kN

Assembly aggregates: total `FX`/`FY`, resultant `FR`, base shear, and overturning moment
`M = Σ Fᵢ · z̄ᵢ` about the base.

## The stacked model

The assembly is a vertical stack of elements above natural ground level (NGL); the foundation
carries no wind and is excluded. Each element has a **base elevation** `z_base`, an **own physical
height** `L` (equipment derives `L = z_tip − z_base`), and a per-element **Kz effective-height
basis** (tip / centroid / custom). Element kinds:

- **Equipment – circular**: `A = D·L`, suggested `Cf = 0.9`.
- **Equipment – rectangular**: `A_X = WX·L`, `A_Y = WY·L`, suggested `Cf = 2.0`; governing direction flagged.
- **Pedestal / plinth**: `A = width·height`, `Cf = 2.0`.
- **Lattice truss support** — two routes:
  - **Route A (solidity, MOP-preferred):** `Cf` from **Table 3-8** vs solidity Φ (× `Cc` from
    Table 3-10 for round members); applied area = one solid face `A = Φ·Ag`. Optional yawed-wind +15%.
  - **Route B (member-by-member, conservative):** per member `Cf = c·1.6` with `c` from **Table 3-7**;
    ignores the §3.1.5.7 shielding credit.

## Tech stack

Python + Flask (engine `wind_mop113.py` is the single source of truth, free of Flask/Plotly
imports), Bootstrap 5, Vue 3, MathJax 3, Plotly.js — all front-end libs via CDN.

## Run it

```bash
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5000>. Each input and derived value has a hideable **"Show reference"**
panel rendering the exact MOP 113 table/equation it comes from.

## Test it

```bash
pytest -v
```

| Preset | Model | Kz | qz (kPa) | FX (kN) | FY (kN) | FR (kN) |
|---|---|---|---|---|---|---|
| PI | circular + plinth | 0.981 | 5.13 | 13.65 | 13.65 | **19.30** |
| CT | circular + plinth | 1.002 | 5.24 | 19.36 | 19.36 | **27.38** |
| CB | rectangular + plinth | 0.984 | 5.14 | 234.3 | 193.4 | **303.8** (X governs) |
| PI + 2 m lattice support | stacked (2 elements) | — | — | 12.0 | 12.0 | **17.0** (M ≈ 57 kN·m) |

Presets 1–3 are single-equipment + plinth stacks from the project sheets; their US-derived targets
are matched within ~0.1% by the SI-native engine. Preset 4 is a two-element stacking sanity check.

## Project layout

```
.
├── app.py                 # Flask: serves index, exposes /api/calculate
├── wind_mop113.py         # Pure-Python SI-native MOP 113 engine (tables, lookups, stacked calc)
├── test_wind_mop113.py    # pytest: validates the 4 worked presets + table logic
├── requirements.txt
├── templates/index.html   # Bootstrap + Vue 3 + MathJax + Plotly (CDN)
└── static/app.js          # Vue app (global params + element stack, API, MathJax, Plotly)
```

---

*Calculation per ASCE MOP 113 (2007), Eq. 3-1 (SI form). The basic wind speed is the 3-s gust
wind speed (§3.1.5.3) from the governing project specification. Verify all table-driven selections
against the governing project specification.*
