# MOP 113 Load Calculator — Wind, V-conversion & Seismic

A local web app with three tabs, all following **ASCE MOP 113 (2007)**, *Substation Structure
Design Guide*:

- **Wind (Eq. 3-1)** — wind load on a **stacked substation assembly** (main equipment on a steel
  lattice support on a foundation, + optional plinth), computed **SI-native**.
- **V: NSCP→MOP** — converts an NSCP 2015 strength-level basic wind speed to the nominal speed
  MOP 113 Eq. 3-1 expects, by equating the two codes' velocity pressures:
  `V_MOP = V_NSCP·√(Kzt·Kd/(LF·IFW))` (defaults Kzt=Kd=1 → `V_NSCP/√(1.6·IFW)`). One click feeds
  the result into the Wind tab. Validated against a worked NSCP→TIA-222 report (75.83→64.27 m/s).
- **Seismic (NSCP 2015)** — NSCP 2015 Section 208 static force procedure (calculation ported from
  [`apecseismicpy`](https://github.com/albertp16/seismicpy)): site coefficients (Tables 208-4…208-8)
  → structural period → design response spectrum → ADRS with ATC-40 reduction → base shear
  (Eq. 208-8…208-11) → redundancy factor ρ.

All tabs share the same stack (pure-Python engine = source of truth, Flask API, Vue 3 reactive
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

Assembly aggregates: total `FX` and `FY` (reported and applied **separately** — directional
wind, no vector resultant), plus base shear and overturning moment `M = Σ Fᵢ · z̄ᵢ` about the base.

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

| Preset | Model | Kz | qz (kPa) | FX (kN) | FY (kN) |
|---|---|---|---|---|---|
| PI | circular + plinth | 0.981 | 5.13 | **13.65** | **13.65** |
| CT | circular + plinth | 1.002 | 5.24 | **19.36** | **19.36** |
| CB | rectangular + plinth | 0.984 | 5.14 | **234.3** | **193.4** (X larger) |
| PI + 2 m lattice support | stacked (2 elements) | — | — | **12.0** | **12.0** (M ≈ 57 kN·m) |

Presets 1–4 are **illustrative sample inputs for demonstration only** (not validated project
data). They exist to lock the engine arithmetic — the tabulated targets are the values the engine
itself should reproduce, used as regression anchors so behaviour does not drift between changes.

## Seismic tab — NSCP 2015 Section 208 (ported from apecseismicpy)

```
site coeffs (Na,Nv,Ca,Cv)  ->  T = Ct·hn^0.75  ->  Sa,max = 2.5·Ca, Ts = Cv/Sa,max
   ->  ADRS: Sd = Sa·g·T²/(4π²), ATC-40 reduction (SRA, SRV)
   ->  base shear  V = Cv·I/(R·T)·W           (208-8, design)
                   V ≤ 2.5·Ca·I/R·W           (208-9, upper)
                   V ≥ 0.11·Ca·I·W            (208-10, lower)
                   V ≥ 0.8·Z·Nv·I/R·W (Z=0.4) (208-11, Zone-4 lower)
   ->  redundancy  ρ = 2 − 6.1/(r_max·√AB),  clamped to [1.0, ρ_max]
```

- **Site coefficients** (Tables 208-4…208-8): Ca = Ca,table·Na, Cv = Cv,table·Nv, with near-source
  Na/Nv interpolated by distance (Zone 4 only; Zone 2 takes the table value directly).
- **Governing base shear** is the engineering-correct NSCP clamp
  `V = max( min(208-8, 208-9), 208-10 [, 208-11 if Zone 4] )`. *(The ported `governingShear`
  only covered Zone 4 and treated 208-11 as an upper cap; this is corrected here and noted in the
  engine docstring.)*
- **ADRS** shows the elastic spectrum with radial constant-period lines; supplying a capacity
  curve (dy, ay, dpi, api + structure type A/B/C) adds the ATC-40 reduced spectrum and the
  performance point.

| Preset | Zone | Ca | Cv | T (s) | V governing (kN) |
|---|---|---|---|---|---|
| Z4_SMRF | 4 | 0.528 | 1.024 | 0.557 | **3465** |
| Z2_STEEL | 2 | — | — | — | (no near-source / no 208-11) |
| Z4_ADRS | 4 | 0.528 | 1.024 | 0.481 | with ATC-40 performance point |

## Project layout

```
.
├── app.py                     # Flask: serves index, exposes /api/calculate, /api/seismic, /api/vconvert
├── wind_mop113.py             # Pure-Python SI-native wind engine (tables, lookups, stacked calc)
├── nscp2015_seismic.py        # Pure-Python NSCP 2015 seismic engine (ported from apecseismicpy)
├── vconv_mop113.py            # Pure-Python NSCP 2015 → MOP 113 basic-wind-speed conversion
├── test_wind_mop113.py        # pytest: wind presets + table logic
├── test_nscp2015_seismic.py   # pytest: NSCP seismic (site coeffs, base shear, redundancy, ADRS)
├── test_vconv_mop113.py       # pytest: NSCP→MOP conversion (reproduces the report's 64.27 m/s)
├── requirements.txt
├── templates/index.html       # nav-tabs: Wind | V:NSCP→MOP | Seismic (Bootstrap/Vue/MathJax/Plotly)
└── static/app.js              # Vue app: all tabs' state, APIs, MathJax, Plotly
```

---

*Wind per ASCE MOP 113 (2007) Eq. 3-1 (SI form); seismic per NSCP 2015 Section 208.
Verify all table-driven selections against the governing project specification.*
