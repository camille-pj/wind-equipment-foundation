# MOP 113 Load Calculator — Wind & Seismic

A local web app with two tabs, both following **ASCE MOP 113 (2007)**, *Substation Structure
Design Guide*:

- **Wind (Eq. 3-1)** — wind load on a **stacked substation assembly** (main equipment on a steel
  lattice support on a foundation, + optional plinth), computed **SI-native**.
- **Seismic (Eq. 3-10)** — simplified NEHRP/FEMA 450 equivalent-lateral-force seismic design per
  **Section 3.1.7** (ASCE 7 spectral framework).

Both tabs share the same stack (pure-Python engine = source of truth, Flask API, Vue 3 reactive
form, MathJax report, Plotly figures) and switch via Bootstrap `nav-tabs`, each preserving its own state.

## Wind tab — Eq. 3-1 (SI form)

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

## Seismic tab — Section 3.1.7 (NEHRP/FEMA 450 ELF)

```
SDS = (2/3)·Fa·Ss   (3-6)      Sa = SDS            if T ≤ T0   (3-8)
SD1 = (2/3)·Fv·S1   (3-7)      Sa = SD1/T          if T > T0   (3-9)
FE  = (Sa/R)·W_eff·IFE·IMV     (3-10)              T0 = SD1/SDS
```

- `Ss`, `S1` are the 0.2-s / 1.0-s spectral accelerations (g) from a **site-specific PSHA**
  (NSCP 2015 §208 is UBC-97-based and does *not* produce Ss/S1 — a separate method).
- `Fa`, `Fv` are interpolated from site class + Ss/S1 (Tables 3-12, 3-13); Site Class F requires
  manual values (site-specific study). `R` follows the USD/ASD basis (Sec 3.1.7.3).
- `W_eff = W + 0.5·(attached wire weight)`. `Sa` is used as a seismic coefficient (fraction of g),
  so `FE` comes out in kN directly — no conversion to m/s².
- Vertical component: `a_vert = 0.8·Sa` (g); informational force `FE_vert = 0.8·Sa·W_eff·IFE`
  **without** the R reduction (R is a lateral-ductility factor — deliberately *not* `0.8·FE`).

| Preset | Branch | Fa | Fv | SDS (g) | Sa (g) | FE (kN) |
|---|---|---|---|---|---|---|
| S1 | plateau (T ≤ T0) | 1.0 | 1.5 | 1.000 | 1.000 | **8.333** |
| S2 | descending (T > T0) | 1.0 | 1.5 | 1.000 | 0.600 | **5.000** |
| S3 | interpolation check | 1.30 | 1.90 | 0.542 | 0.542 | **2.708** |

## Project layout

```
.
├── app.py                  # Flask: serves index, exposes /api/calculate + /api/seismic
├── wind_mop113.py          # Pure-Python SI-native wind engine (tables, lookups, stacked calc)
├── seismic_mop113.py       # Pure-Python seismic engine (§3.1.7 ELF, Tables 3-12/3-13, R/IFE/IMV)
├── test_wind_mop113.py     # pytest: 4 wind presets + table logic
├── test_seismic_mop113.py  # pytest: 3 seismic presets + table logic
├── requirements.txt
├── templates/index.html    # nav-tabs shell + Wind & Seismic panes (Bootstrap/Vue/MathJax/Plotly)
└── static/app.js           # Vue app: both tabs' state, APIs, MathJax, Plotly
```

---

*Calculation per ASCE MOP 113 (2007): Wind Eq. 3-1 (SI form) and Seismic §3.1.7 (Eq. 3-10).
Verify all table-driven selections against the governing project specification.*
