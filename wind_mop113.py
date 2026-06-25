"""
wind_mop113.py
==============

Pure-Python wind-load calculation engine implementing ASCE MOP 113 (2007),
*Substation Structure Design Guide*, governing equation Eq. 3-1:

        F = Q * Kz * V**2 * IFW * GRF * Cf * A

It is implemented as a two-stage calculation exactly as the worked examples do:

    Stage 1 -- velocity pressure (force per unit area):
        qz = Q * Kz * V**2 * IFW            [lb/ft^2]   -> converted to kPa
    Stage 2 -- wind force on each element:
        F  = qz * GRF * Cf * A              [lb]        -> converted to kN

Q, Kz and V are US-customary based (Q = 0.00256 lb/ft^2/mph^2, V in mph,
area in ft^2). All arithmetic is therefore done in US units and only the
*results* are converted to SI (kPa, kN) for display.

This module is intentionally free of any Flask / Plotly imports so that it
stays trivially unit-testable.  ``calculate(inputs: dict) -> dict`` returns
every intermediate value, ready-to-render LaTeX strings for each report step,
and the raw data arrays the front end needs to build the Plotly figures.
"""

from __future__ import annotations

import math
from typing import Dict, List, Any


# ---------------------------------------------------------------------------
# UNIT CONVERSION FACTORS  (exact factors required by the spec)
# ---------------------------------------------------------------------------
KPH_TO_MPH = 0.621371          # mph = kph * 0.621371
MM_TO_M = 1.0 / 1000.0         # m  = mm / 1000
M_TO_FT = 3.28084              # ft = m * 3.28084
M2_TO_FT2 = 10.76391           # ft^2 = m^2 * 10.76391
PSF_TO_KPA = 0.04788026        # kPa = (lb/ft^2) * 0.04788026
LB_TO_KN = 0.00444822          # kN  = lb * 0.00444822
KG_TO_KN = 9.80665 / 1000.0    # weight: kN = kg * g / 1000  (display only)


# ---------------------------------------------------------------------------
# EMBEDDED MOP 113 TABLES  (hard-coded exactly as in the design guide)
# ---------------------------------------------------------------------------

# Table 3-1 -- Terrain Exposure Coefficient, Kz (height z in feet).
# Linear-interpolate between rows; the "0-15" row is keyed at 15 ft.
TABLE_3_1 = {
    # z_ft : (Exp B, Exp C, Exp D)
    15:  {"B": 0.57, "C": 0.85, "D": 1.03},
    30:  {"B": 0.70, "C": 0.98, "D": 1.16},
    40:  {"B": 0.76, "C": 1.04, "D": 1.22},
    50:  {"B": 0.81, "C": 1.09, "D": 1.27},
    60:  {"B": 0.85, "C": 1.13, "D": 1.31},
    70:  {"B": 0.89, "C": 1.17, "D": 1.34},
    80:  {"B": 0.93, "C": 1.21, "D": 1.38},
    90:  {"B": 0.96, "C": 1.24, "D": 1.40},
    100: {"B": 0.99, "C": 1.26, "D": 1.43},
}
TABLE_3_1_HEIGHTS = sorted(TABLE_3_1.keys())

# Table 3-2 -- Power Law Constants (for Eq. 3-2 fallback, h > 100 ft).
TABLE_3_2 = {
    # exposure : (alpha, zg_ft)
    "B": {"alpha": 7.0,  "zg": 1200.0},
    "C": {"alpha": 9.5,  "zg": 900.0},
    "D": {"alpha": 11.5, "zg": 700.0},
}

# Table 3-3 -- Importance Factor, IFW.
TABLE_3_3 = {
    "50":  {"label": "50-year MRI", "ifw": 1.00},
    "100": {"label": "100-year MRI (critical facility)", "ifw": 1.15},
}

# Table 3-4a -- Structure GRF, wire-supporting, epsilon = 0.75 (tip height ft).
# Each row is (upper_bound_ft, {B, C, D}); the band is "<= upper_bound".
TABLE_3_4A = [
    (33,  {"B": 1.17, "C": 0.96, "D": 0.85}),   # <= 33
    (40,  {"B": 1.15, "C": 0.95, "D": 0.84}),   # > 33 to 40
    (50,  {"B": 1.12, "C": 0.94, "D": 0.84}),   # > 40 to 50
    (60,  {"B": 1.08, "C": 0.92, "D": 0.83}),   # > 50 to 60
    (70,  {"B": 1.06, "C": 0.91, "D": 0.82}),   # > 60 to 70
    (80,  {"B": 1.03, "C": 0.89, "D": 0.81}),   # > 70 to 80
    (90,  {"B": 1.01, "C": 0.88, "D": 0.81}),   # > 80 to 90
    (100, {"B": 1.00, "C": 0.88, "D": 0.80}),   # > 90 to 100
]

# Table 3-4b -- Structure GRF, flexible non-wire-supporting (<1 Hz), eps = 1.0.
TABLE_3_4B = [
    (15,  {"B": 1.59, "C": 1.20, "D": 1.02}),   # <= 15
    (33,  {"B": 1.48, "C": 1.15, "D": 0.99}),   # > 15 to 33
    (40,  {"B": 1.37, "C": 1.11, "D": 0.96}),   # > 33 to 40
    (50,  {"B": 1.33, "C": 1.08, "D": 0.95}),   # > 40 to 50
    (60,  {"B": 1.28, "C": 1.06, "D": 0.94}),   # > 50 to 60
    (70,  {"B": 1.25, "C": 1.05, "D": 0.93}),   # > 60 to 70
    (80,  {"B": 1.22, "C": 1.03, "D": 0.92}),   # > 70 to 80
    (90,  {"B": 1.19, "C": 1.02, "D": 0.91}),   # > 80 to 90
    (100, {"B": 1.17, "C": 1.00, "D": 0.90}),   # > 90 to 100
]

# Table 3-9 -- Force Coefficient, Cf (structural shapes / bus / tubular).
TABLE_3_9 = [
    {"shape": "Structural shapes (average value)",      "cf": 1.6},
    {"shape": "Bus: rigid and flexible",                "cf": 1.0},
    {"shape": "Circular",                               "cf": 0.9},
    {"shape": "Hexadecagonal (16-sided polygonal)",     "cf": 0.9},
    {"shape": "Dodecagonal (12-sided polygonal)",       "cf": 1.0},
    {"shape": "Octagonal (8-sided polygonal)",          "cf": 1.4},
    {"shape": "Hexagonal (6-sided polygonal)",          "cf": 1.4},
    {"shape": "Square or rectangle",                    "cf": 2.0},
]

# The fixed air-density factor from Eq. 3-1 (US units).
Q_CONST = 0.00256  # lb/ft^2/mph^2


# ---------------------------------------------------------------------------
# Number formatting helpers for LaTeX strings
# ---------------------------------------------------------------------------
def _f(x: float, n: int = 3) -> str:
    """Format a float with n significant decimals, trimming trailing zeros only
    when it keeps the report readable.  Used inside LaTeX strings."""
    return f"{x:,.{n}f}"


# ---------------------------------------------------------------------------
# Kz -- Table 3-1 interpolation / Eq. 3-2 fallback
# ---------------------------------------------------------------------------
def compute_kz(h_ft: float, exposure: str) -> Dict[str, Any]:
    """
    Terrain exposure coefficient Kz for tip height ``h_ft`` (feet) in the
    selected exposure column of Table 3-1.

    * h <= 15 ft        -> use the 0-15 row value directly.
    * 15 < h <= 100 ft  -> linear interpolation between bracketing rows.
    * h > 100 ft        -> Eq. 3-2: Kz = 2.01 * (z / zg)^(2/alpha).

    Returns a dict carrying the method, the result, and (for interpolation)
    the two bracketing rows so the report can show the audit trail.
    """
    exposure = exposure.upper()

    # --- Case 1: at or below 15 ft -> use the 0-15 row directly. ---
    if h_ft <= 15:
        kz = TABLE_3_1[15][exposure]
        return {
            "method": "floor",
            "kz": kz,
            "h_lo": 15, "h_hi": 15,
            "kz_lo": kz, "kz_hi": kz,
        }

    # --- Case 3: above 100 ft -> Eq. 3-2 power-law fallback. ---
    if h_ft > 100:
        alpha = TABLE_3_2[exposure]["alpha"]
        zg = TABLE_3_2[exposure]["zg"]
        kz = 2.01 * (h_ft / zg) ** (2.0 / alpha)
        return {
            "method": "powerlaw",
            "kz": kz,
            "alpha": alpha,
            "zg": zg,
        }

    # --- Case 2: 15 < h <= 100 -> linear interpolation. ---
    h_lo = max(z for z in TABLE_3_1_HEIGHTS if z <= h_ft)
    h_hi = min(z for z in TABLE_3_1_HEIGHTS if z >= h_ft)

    kz_lo = TABLE_3_1[h_lo][exposure]
    kz_hi = TABLE_3_1[h_hi][exposure]

    if h_hi == h_lo:
        kz = kz_lo
    else:
        kz = kz_lo + (h_ft - h_lo) / (h_hi - h_lo) * (kz_hi - kz_lo)

    return {
        "method": "interp",
        "kz": kz,
        "h_lo": h_lo, "h_hi": h_hi,
        "kz_lo": kz_lo, "kz_hi": kz_hi,
    }


# ---------------------------------------------------------------------------
# GRF -- gust response factor lookup
# ---------------------------------------------------------------------------
def _band_lookup(table: List, h_ft: float, exposure: str) -> Dict[str, Any]:
    """Return the GRF value and the band label for a banded table (3-4a/3-4b)."""
    exposure = exposure.upper()
    prev_bound = 0
    for upper, row in table:
        if h_ft <= upper:
            if prev_bound == 0:
                band = f"≤{upper} ft"
            else:
                band = f">{prev_bound} to {upper} ft"
            return {"grf": row[exposure], "band": band, "upper": upper}
        prev_bound = upper
    # Above the last band -> clamp to the top band (>90 to 100).
    upper, row = table[-1]
    return {"grf": row[exposure], "band": f">{prev_bound} ft (clamped to top band)",
            "upper": upper}


def compute_grf(grf_type: str, h_ft: float, exposure: str) -> Dict[str, Any]:
    """
    Gust response factor per MOP 113 Sec. 3.1.5.5.

    * "rigid"    -> 0.85 flat (Sec. 3.1.5.5.1, rigid non-wire-supporting >= 1 Hz).
    * "wire"     -> Table 3-4a lookup by tip height + exposure (eps = 0.75).
    * "flexible" -> Table 3-4b lookup by tip height + exposure (eps = 1.0).
    """
    if grf_type == "rigid":
        return {"grf": 0.85, "method": "rigid",
                "desc": "Rigid, non-wire-supporting (≥1 Hz), Sec. 3.1.5.5.1"}
    if grf_type == "wire":
        res = _band_lookup(TABLE_3_4A, h_ft, exposure)
        res.update({"method": "table34a",
                    "desc": "Wire-supporting, Table 3-4a (ε = 0.75)"})
        return res
    if grf_type == "flexible":
        res = _band_lookup(TABLE_3_4B, h_ft, exposure)
        res.update({"method": "table34b",
                    "desc": "Flexible non-wire-supporting, Table 3-4b (ε = 1.0)"})
        return res
    raise ValueError(f"Unknown grf_type: {grf_type!r}")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def calculate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the full MOP 113 Eq. 3-1 wind-load calculation.

    ``inputs`` keys (all numeric inputs already in the UI's display units):
        tag             : str   equipment tag / name
        V_kph           : float basic 3-s gust wind speed (kph)
        shape           : str   "circular" | "rectangular"
        H_mm            : float overall tip height above NGL (mm)
        D_mm            : float body diameter (circular)            (mm)
        WX_mm, WY_mm    : float projected widths front/side (rect)  (mm)
        weight_kg       : float equipment weight (display only)     (kg)
        include_plinth  : bool  include plinth in totals
        plinth_height_mm: float plinth height (mm)
        plinth_width_mm : float plinth square cross-section width (mm)
        IFW             : float importance factor (Table 3-3)
        exposure        : str   "B" | "C" | "D" (Table 3-1/3-2)
        grf_type        : str   "rigid" | "wire" | "flexible"
        cf_body         : float force coefficient, body  (Table 3-9)
        cf_plinth       : float force coefficient, plinth (Table 3-9)
        apply_075       : bool  apply 0.75 wind-on-two-faces factor to resultant

    Returns a dict with: echoed inputs, every intermediate, ``steps`` (LaTeX),
    ``summary``, and ``figures`` (raw data for Plotly).
    """
    # -- Parse / coerce inputs --------------------------------------------
    tag = str(inputs.get("tag", "Equipment"))
    V_kph = float(inputs["V_kph"])
    shape = str(inputs.get("shape", "circular")).lower()
    H_mm = float(inputs["H_mm"])
    weight_kg = float(inputs.get("weight_kg", 0.0) or 0.0)
    include_plinth = bool(inputs.get("include_plinth", True))
    plinth_height_mm = float(inputs.get("plinth_height_mm", 0.0) or 0.0)
    plinth_width_mm = float(inputs.get("plinth_width_mm", 0.0) or 0.0)
    IFW = float(inputs["IFW"])
    exposure = str(inputs.get("exposure", "C")).upper()
    grf_type = str(inputs.get("grf_type", "rigid")).lower()
    cf_body = float(inputs["cf_body"])
    cf_plinth = float(inputs["cf_plinth"])
    apply_075 = bool(inputs.get("apply_075", False))
    Q = Q_CONST

    # =====================================================================
    # STEP 1 -- wind parameters
    # =====================================================================
    V_mph = V_kph * KPH_TO_MPH

    # =====================================================================
    # STEP 2 -- dimensions
    # =====================================================================
    H_m = H_mm * MM_TO_M
    H_ft = H_m * M_TO_FT
    weight_kN = weight_kg * KG_TO_KN

    if shape == "circular":
        D_mm = float(inputs["D_mm"])
        D_m = D_mm * MM_TO_M
        WX_m = WY_m = D_m            # projected width identical both directions
        width_label_x = width_label_y = f"D = {_f(D_mm, 1)} mm"
    else:
        WX_mm = float(inputs["WX_mm"])
        WY_mm = float(inputs["WY_mm"])
        D_mm = None
        WX_m = WX_mm * MM_TO_M
        WY_m = WY_mm * MM_TO_M
        width_label_x = f"W_X = {_f(WX_mm, 1)} mm"
        width_label_y = f"W_Y = {_f(WY_mm, 1)} mm"

    plinth_height_m = plinth_height_mm * MM_TO_M
    plinth_width_m = plinth_width_mm * MM_TO_M

    # =====================================================================
    # STEP 3 -- Kz (terrain exposure coefficient), Table 3-1
    # =====================================================================
    kz_info = compute_kz(H_ft, exposure)
    Kz = kz_info["kz"]

    # =====================================================================
    # GRF lookup (used in Step 1 echo + Step 6)
    # =====================================================================
    grf_info = compute_grf(grf_type, H_ft, exposure)
    GRF = grf_info["grf"]

    # =====================================================================
    # STEP 4 -- velocity pressure  qz = Q * Kz * V^2 * IFW
    # =====================================================================
    qz_psf = Q * Kz * V_mph ** 2 * IFW
    qz_kpa = qz_psf * PSF_TO_KPA

    # =====================================================================
    # STEP 5 -- projected wind areas
    # =====================================================================
    # Body: X-direction wind acts on the front face (width = WX), Y on side.
    A_body_x_m2 = WX_m * H_m
    A_body_y_m2 = WY_m * H_m
    A_body_x_ft2 = A_body_x_m2 * M2_TO_FT2
    A_body_y_ft2 = A_body_y_m2 * M2_TO_FT2

    # Plinth: always a square section, identical projected area both directions.
    if include_plinth:
        A_plinth_m2 = plinth_width_m * plinth_height_m
    else:
        A_plinth_m2 = 0.0
    A_plinth_ft2 = A_plinth_m2 * M2_TO_FT2

    # =====================================================================
    # STEP 6 -- wind forces  F = qz * GRF * Cf * A
    # =====================================================================
    def force_lb(area_ft2: float, cf: float) -> float:
        return qz_psf * GRF * cf * area_ft2

    F_body_x_lb = force_lb(A_body_x_ft2, cf_body)
    F_body_y_lb = force_lb(A_body_y_ft2, cf_body)
    F_plinth_lb = force_lb(A_plinth_ft2, cf_plinth)

    F_body_x_kN = F_body_x_lb * LB_TO_KN
    F_body_y_kN = F_body_y_lb * LB_TO_KN
    F_plinth_kN = F_plinth_lb * LB_TO_KN

    FX_kN = F_body_x_kN + F_plinth_kN
    FY_kN = F_body_y_kN + F_plinth_kN

    # Equivalent applied face pressure F/A (kPa) for each element.
    def face_pressure_kpa(F_lb: float, area_ft2: float) -> float:
        if area_ft2 <= 0:
            return 0.0
        return (F_lb / area_ft2) * PSF_TO_KPA

    p_body_x_kpa = face_pressure_kpa(F_body_x_lb, A_body_x_ft2)
    p_body_y_kpa = face_pressure_kpa(F_body_y_lb, A_body_y_ft2)
    p_plinth_kpa = face_pressure_kpa(F_plinth_lb, A_plinth_ft2)

    # Governing direction.
    if shape == "circular":
        governing = "X = Y (symmetric)"
    else:
        governing = "X" if FX_kN >= FY_kN else "Y"

    # =====================================================================
    # STEP 7 -- resultant  FR = sqrt(FX^2 + FY^2)
    # =====================================================================
    FR_kN_full = math.sqrt(FX_kN ** 2 + FY_kN ** 2)
    FR_kN = FR_kN_full * (0.75 if apply_075 else 1.0)

    # =====================================================================
    # Build LaTeX report steps (numbers already substituted)
    # =====================================================================
    steps = _build_steps(
        tag=tag, Q=Q, V_kph=V_kph, V_mph=V_mph, IFW=IFW, exposure=exposure,
        grf_info=grf_info, GRF=GRF, cf_body=cf_body, cf_plinth=cf_plinth,
        shape=shape, H_mm=H_mm, H_m=H_m, H_ft=H_ft, D_mm=D_mm,
        weight_kg=weight_kg, weight_kN=weight_kN,
        WX_m=WX_m, WY_m=WY_m, width_label_x=width_label_x,
        width_label_y=width_label_y,
        include_plinth=include_plinth, plinth_width_m=plinth_width_m,
        plinth_height_m=plinth_height_m, plinth_width_mm=plinth_width_mm,
        plinth_height_mm=plinth_height_mm,
        kz_info=kz_info, Kz=Kz, qz_psf=qz_psf, qz_kpa=qz_kpa,
        A_body_x_m2=A_body_x_m2, A_body_y_m2=A_body_y_m2,
        A_body_x_ft2=A_body_x_ft2, A_body_y_ft2=A_body_y_ft2,
        A_plinth_m2=A_plinth_m2, A_plinth_ft2=A_plinth_ft2,
        F_body_x_lb=F_body_x_lb, F_body_y_lb=F_body_y_lb, F_plinth_lb=F_plinth_lb,
        F_body_x_kN=F_body_x_kN, F_body_y_kN=F_body_y_kN, F_plinth_kN=F_plinth_kN,
        FX_kN=FX_kN, FY_kN=FY_kN, FR_kN_full=FR_kN_full, FR_kN=FR_kN,
        p_body_x_kpa=p_body_x_kpa, p_body_y_kpa=p_body_y_kpa,
        p_plinth_kpa=p_plinth_kpa, governing=governing, apply_075=apply_075,
    )

    # =====================================================================
    # Figure data arrays for Plotly
    # =====================================================================
    figures = _build_figures(
        exposure=exposure, kz_info=kz_info, Kz=Kz, H_ft=H_ft,
        F_body_x_kN=F_body_x_kN, F_body_y_kN=F_body_y_kN, F_plinth_kN=F_plinth_kN,
        shape=shape, H_m=H_m, WX_m=WX_m, WY_m=WY_m,
        include_plinth=include_plinth,
        plinth_width_m=plinth_width_m, plinth_height_m=plinth_height_m,
        governing=governing, tag=tag,
    )

    # =====================================================================
    # Assemble result dict
    # =====================================================================
    return {
        "inputs_echo": {
            "tag": tag, "V_kph": V_kph, "V_mph": V_mph, "shape": shape,
            "H_mm": H_mm, "H_m": H_m, "H_ft": H_ft, "D_mm": D_mm,
            "weight_kg": weight_kg, "weight_kN": weight_kN,
            "include_plinth": include_plinth,
            "plinth_height_mm": plinth_height_mm, "plinth_width_mm": plinth_width_mm,
            "Q": Q, "IFW": IFW, "exposure": exposure,
            "grf_type": grf_type, "GRF": GRF, "grf_desc": grf_info["desc"],
            "cf_body": cf_body, "cf_plinth": cf_plinth, "apply_075": apply_075,
        },
        "kz": {
            "Kz": Kz, "H_ft": H_ft, "method": kz_info["method"], **kz_info,
        },
        "grf": grf_info,
        "qz": {"psf": qz_psf, "kpa": qz_kpa},
        "areas": {
            "A_body_x_m2": A_body_x_m2, "A_body_y_m2": A_body_y_m2,
            "A_body_x_ft2": A_body_x_ft2, "A_body_y_ft2": A_body_y_ft2,
            "A_plinth_m2": A_plinth_m2, "A_plinth_ft2": A_plinth_ft2,
        },
        "forces": {
            "F_body_x_kN": F_body_x_kN, "F_body_y_kN": F_body_y_kN,
            "F_plinth_kN": F_plinth_kN, "FX_kN": FX_kN, "FY_kN": FY_kN,
            "FR_kN": FR_kN, "FR_kN_full": FR_kN_full,
            "p_body_x_kpa": p_body_x_kpa, "p_body_y_kpa": p_body_y_kpa,
            "p_plinth_kpa": p_plinth_kpa,
        },
        "summary": {
            "qz_kpa": qz_kpa, "FX_kN": FX_kN, "FY_kN": FY_kN,
            "FR_kN": FR_kN, "governing": governing,
        },
        "steps": steps,
        "figures": figures,
    }


# ---------------------------------------------------------------------------
# LaTeX report builder
# ---------------------------------------------------------------------------
def _build_steps(**v) -> List[Dict[str, Any]]:
    """Assemble the 7-step report. Each step carries a title and a list of LaTeX
    lines (symbolic equation followed by the substituted-number version)."""
    steps: List[Dict[str, Any]] = []

    # ---- Step 1 -- wind parameters --------------------------------------
    grf_note = v["grf_info"]["desc"]
    if v["grf_info"]["method"] != "rigid":
        grf_note += f", band {v['grf_info']['band']}"
    steps.append({
        "n": 1, "title": "Wind parameters",
        "lines": [
            r"\text{Air density factor (Eq. 3-1): } Q = " + _f(v["Q"], 5)
            + r"\ \text{lb/ft}^2\text{/mph}^2",
            r"\text{Wind speed: } V = " + _f(v["V_kph"], 1)
            + r"\ \text{kph} \times 0.621371 = " + _f(v["V_mph"], 3)
            + r"\ \text{mph}",
            r"\text{Importance factor (Table 3-3): } I_{FW} = " + _f(v["IFW"], 2),
            r"\text{Gust response factor (Sec. 3.1.5.5): } GRF = "
            + _f(v["GRF"], 3) + r"\quad (\text{" + grf_note + r"})",
            r"\text{Force coefficient (Table 3-9): } C_{f,\text{body}} = "
            + _f(v["cf_body"], 2) + r",\ C_{f,\text{plinth}} = "
            + _f(v["cf_plinth"], 2),
        ],
    })

    # ---- Step 2 -- dimensions -------------------------------------------
    dim_lines = [
        r"\text{Tip height: } H = " + _f(v["H_mm"], 0) + r"\ \text{mm} = "
        + _f(v["H_m"], 3) + r"\ \text{m} = " + _f(v["H_ft"], 3) + r"\ \text{ft}",
    ]
    if v["shape"] == "circular":
        dim_lines.append(
            r"\text{Diameter: } D = " + _f(v["D_mm"], 1) + r"\ \text{mm}"
            + r"\quad(\text{circular: } W_X = W_Y = D)")
    else:
        dim_lines.append(
            r"\text{Front-face width: } W_X = " + _f(v["WX_m"] * 1000, 1)
            + r"\ \text{mm},\quad \text{side-face width: } W_Y = "
            + _f(v["WY_m"] * 1000, 1) + r"\ \text{mm}")
    dim_lines.append(
        r"\text{Weight: } " + _f(v["weight_kg"], 1) + r"\ \text{kg} = "
        + _f(v["weight_kN"], 3) + r"\ \text{kN}\quad(\text{display only, "
        + r"not used in wind force})")
    if v["include_plinth"]:
        dim_lines.append(
            r"\text{Plinth: } " + _f(v["plinth_width_mm"], 0)
            + r"\ \text{mm square} \times " + _f(v["plinth_height_mm"], 0)
            + r"\ \text{mm high}")
    steps.append({"n": 2, "title": "Dimensions", "lines": dim_lines})

    # ---- Step 3 -- Kz ---------------------------------------------------
    ki = v["kz_info"]
    if ki["method"] == "interp":
        kz_lines = [
            r"K_z = K_{z,lo} + \dfrac{h - h_{lo}}{h_{hi} - h_{lo}}\,"
            r"(K_{z,hi} - K_{z,lo})\quad(\text{Table 3-1, Exp. "
            + v["exposure"] + r"})",
            r"\text{Bracketing rows: } (h_{lo}, K_{z,lo}) = ("
            + _f(ki["h_lo"], 0) + r"\,\text{ft}, " + _f(ki["kz_lo"], 3)
            + r"),\quad (h_{hi}, K_{z,hi}) = (" + _f(ki["h_hi"], 0)
            + r"\,\text{ft}, " + _f(ki["kz_hi"], 3) + r")",
            r"K_z = " + _f(ki["kz_lo"], 3) + r" + \dfrac{"
            + _f(v["H_ft"], 3) + r" - " + _f(ki["h_lo"], 0) + r"}{"
            + _f(ki["h_hi"], 0) + r" - " + _f(ki["h_lo"], 0) + r"}\,("
            + _f(ki["kz_hi"], 3) + r" - " + _f(ki["kz_lo"], 3) + r") = "
            + _f(v["Kz"], 4),
        ]
    elif ki["method"] == "floor":
        kz_lines = [
            r"h \leq 15\ \text{ft} \Rightarrow K_z = "
            + r"\text{(0--15 row, Exp. " + v["exposure"] + r")} = "
            + _f(v["Kz"], 4),
        ]
    else:  # powerlaw
        kz_lines = [
            r"h > 100\ \text{ft} \Rightarrow K_z = 2.01\left(\dfrac{z}{z_g}"
            r"\right)^{2/\alpha}\quad(\text{Eq. 3-2, Table 3-2, Exp. "
            + v["exposure"] + r"})",
            r"K_z = 2.01\left(\dfrac{" + _f(v["H_ft"], 3) + r"}{"
            + _f(ki["zg"], 0) + r"}\right)^{2/" + _f(ki["alpha"], 1)
            + r"} = " + _f(v["Kz"], 4),
        ]
    steps.append({"n": 3, "title": "Terrain exposure coefficient $K_z$",
                  "lines": kz_lines})

    # ---- Step 4 -- velocity pressure ------------------------------------
    steps.append({
        "n": 4, "title": "Velocity pressure $q_z$",
        "lines": [
            r"q_z = Q \cdot K_z \cdot V^2 \cdot I_{FW}",
            r"q_z = " + _f(v["Q"], 5) + r" \times " + _f(v["Kz"], 4)
            + r" \times " + _f(v["V_mph"], 3) + r"^2 \times " + _f(v["IFW"], 2)
            + r" = " + _f(v["qz_psf"], 3) + r"\ \text{lb/ft}^2 = "
            + _f(v["qz_kpa"], 4) + r"\ \text{kPa}",
        ],
    })

    # ---- Step 5 -- projected areas --------------------------------------
    if v["shape"] == "circular":
        area_lines = [
            r"A_{body} = D \times H = " + _f(v["WX_m"], 4) + r" \times "
            + _f(v["H_m"], 3) + r" = " + _f(v["A_body_x_m2"], 4)
            + r"\ \text{m}^2 = " + _f(v["A_body_x_ft2"], 3) + r"\ \text{ft}^2"
            + r"\quad(\text{same both directions})",
        ]
    else:
        area_lines = [
            r"A_X = W_X \times H = " + _f(v["WX_m"], 4) + r" \times "
            + _f(v["H_m"], 3) + r" = " + _f(v["A_body_x_m2"], 4)
            + r"\ \text{m}^2 = " + _f(v["A_body_x_ft2"], 3) + r"\ \text{ft}^2",
            r"A_Y = W_Y \times H = " + _f(v["WY_m"], 4) + r" \times "
            + _f(v["H_m"], 3) + r" = " + _f(v["A_body_y_m2"], 4)
            + r"\ \text{m}^2 = " + _f(v["A_body_y_ft2"], 3) + r"\ \text{ft}^2",
        ]
    if v["include_plinth"]:
        area_lines.append(
            r"A_{plinth} = w_p \times h_p = " + _f(v["plinth_width_m"], 3)
            + r" \times " + _f(v["plinth_height_m"], 3) + r" = "
            + _f(v["A_plinth_m2"], 4) + r"\ \text{m}^2 = "
            + _f(v["A_plinth_ft2"], 3) + r"\ \text{ft}^2")
    steps.append({"n": 5, "title": "Projected wind areas", "lines": area_lines})

    # ---- Step 6 -- wind forces ------------------------------------------
    force_lines = [r"F = q_z \cdot GRF \cdot C_f \cdot A"]
    # Body X
    force_lines.append(
        r"F_{body,X} = " + _f(v["qz_psf"], 3) + r" \times " + _f(v["GRF"], 3)
        + r" \times " + _f(v["cf_body"], 2) + r" \times "
        + _f(v["A_body_x_ft2"], 3) + r" = " + _f(v["F_body_x_lb"], 1)
        + r"\ \text{lb} = " + _f(v["F_body_x_kN"], 3) + r"\ \text{kN}"
        + r"\quad(F/A = " + _f(v["p_body_x_kpa"], 4) + r"\ \text{kPa})")
    # Body Y (only show separately if asymmetric)
    if v["shape"] != "circular":
        force_lines.append(
            r"F_{body,Y} = " + _f(v["qz_psf"], 3) + r" \times " + _f(v["GRF"], 3)
            + r" \times " + _f(v["cf_body"], 2) + r" \times "
            + _f(v["A_body_y_ft2"], 3) + r" = " + _f(v["F_body_y_lb"], 1)
            + r"\ \text{lb} = " + _f(v["F_body_y_kN"], 3) + r"\ \text{kN}"
            + r"\quad(F/A = " + _f(v["p_body_y_kpa"], 4) + r"\ \text{kPa})")
    # Plinth
    if v["include_plinth"]:
        force_lines.append(
            r"F_{plinth} = " + _f(v["qz_psf"], 3) + r" \times " + _f(v["GRF"], 3)
            + r" \times " + _f(v["cf_plinth"], 2) + r" \times "
            + _f(v["A_plinth_ft2"], 3) + r" = " + _f(v["F_plinth_lb"], 1)
            + r"\ \text{lb} = " + _f(v["F_plinth_kN"], 3) + r"\ \text{kN}"
            + r"\quad(F/A = " + _f(v["p_plinth_kpa"], 4) + r"\ \text{kPa})")
    # Totals
    force_lines.append(
        r"\text{Total } F_X = " + _f(v["F_body_x_kN"], 3) + r" + "
        + _f(v["F_plinth_kN"], 3) + r" = " + _f(v["FX_kN"], 3) + r"\ \text{kN}")
    force_lines.append(
        r"\text{Total } F_Y = " + _f(v["F_body_y_kN"], 3) + r" + "
        + _f(v["F_plinth_kN"], 3) + r" = " + _f(v["FY_kN"], 3) + r"\ \text{kN}")
    if v["shape"] != "circular":
        force_lines.append(
            r"\Rightarrow \text{Direction } " + v["governing"]
            + r"\ \text{governs (larger force).}")
    steps.append({"n": 6, "title": "Wind forces", "lines": force_lines})

    # ---- Step 7 -- resultant --------------------------------------------
    res_lines = [
        r"F_R = \sqrt{F_X^2 + F_Y^2}",
        r"F_R = \sqrt{" + _f(v["FX_kN"], 3) + r"^2 + " + _f(v["FY_kN"], 3)
        + r"^2} = " + _f(v["FR_kN_full"], 3) + r"\ \text{kN}",
    ]
    if v["apply_075"]:
        res_lines.append(
            r"\text{Apply 0.75 wind-on-two-faces factor: } F_R = 0.75 \times "
            + _f(v["FR_kN_full"], 3) + r" = " + _f(v["FR_kN"], 3) + r"\ \text{kN}")
    else:
        res_lines.append(
            r"\text{(0.75 wind-on-two-faces factor not applied --- full vector "
            r"sum per worked examples.)}")
    steps.append({"n": 7, "title": "Resultant force $F_R$", "lines": res_lines})

    return steps


# ---------------------------------------------------------------------------
# Plotly figure data builder
# ---------------------------------------------------------------------------
def _build_figures(**v) -> Dict[str, Any]:
    """Return raw data arrays the front end feeds straight into Plotly."""
    # --- Figure 1: force breakdown grouped bars ---
    force_breakdown = {
        "directions": ["X", "Y"],
        "body": [round(v["F_body_x_kN"], 3), round(v["F_body_y_kN"], 3)],
        "plinth": [round(v["F_plinth_kN"], 3), round(v["F_plinth_kN"], 3)],
        "totals": [round(v["F_body_x_kN"] + v["F_plinth_kN"], 3),
                   round(v["F_body_y_kN"] + v["F_plinth_kN"], 3)],
        "governing": v["governing"],
    }

    # --- Figure 2: Kz interpolation curve for the selected exposure ---
    exp = v["exposure"]
    kz_curve = {
        "heights": list(TABLE_3_1_HEIGHTS),
        "kz": [TABLE_3_1[h][exp] for h in TABLE_3_1_HEIGHTS],
        "tip_h_ft": round(v["H_ft"], 3),
        "tip_kz": round(v["Kz"], 4),
        "exposure": exp,
    }

    # --- Figure 3: elevation schematic (rectangles, in metres) ---
    plinth_w = v["plinth_width_m"] if v["include_plinth"] else 0.0
    plinth_h = v["plinth_height_m"] if v["include_plinth"] else 0.0
    body_w = v["WX_m"]
    body_h = v["H_m"]            # H is overall tip height above NGL
    # Body sits above the plinth; widths centred on x = 0.
    schematic = {
        "body": {
            "x0": -body_w / 2.0, "x1": body_w / 2.0,
            "y0": plinth_h, "y1": body_h,
        },
        "plinth": {
            "x0": -plinth_w / 2.0, "x1": plinth_w / 2.0,
            "y0": 0.0, "y1": plinth_h,
        } if v["include_plinth"] else None,
        "H_m": round(body_h, 3),
        "body_w_m": round(body_w, 3),
        "plinth_w_m": round(plinth_w, 3),
        "plinth_h_m": round(plinth_h, 3),
        "shape": v["shape"],
        "tag": v["tag"],
    }

    return {
        "force_breakdown": force_breakdown,
        "kz_curve": kz_curve,
        "schematic": schematic,
    }


# ---------------------------------------------------------------------------
# Worked-example presets (also surfaced in the UI)
# ---------------------------------------------------------------------------
PRESETS = {
    "PI": {
        "label": "Preset 1 - Post Insulator (PI), circular",
        "tag": "PI", "V_kph": 310, "shape": "circular", "H_mm": 9189,
        "D_mm": 345, "weight_kg": 505, "include_plinth": True,
        "plinth_height_mm": 200, "plinth_width_mm": 700,
        "IFW": 1.15, "exposure": "C", "grf_type": "rigid",
        "cf_body": 0.9, "cf_plinth": 2.0, "apply_075": False,
    },
    "CT": {
        "label": "Preset 2 - Current Transformer (CT), circular",
        "tag": "CT", "V_kph": 310, "shape": "circular", "H_mm": 10265,
        "D_mm": 440, "weight_kg": 980, "include_plinth": True,
        "plinth_height_mm": 200, "plinth_width_mm": 700,
        "IFW": 1.15, "exposure": "C", "grf_type": "rigid",
        "cf_body": 0.9, "cf_plinth": 2.0, "apply_075": False,
    },
    "CB": {
        "label": "Preset 3 - Circuit Breaker (CB), rectangular",
        "tag": "CB", "V_kph": 310, "shape": "rectangular", "H_mm": 9336,
        "WX_mm": 2853.8, "WY_mm": 2353.1, "weight_kg": 3730,
        "include_plinth": True, "plinth_height_mm": 200, "plinth_width_mm": 700,
        "IFW": 1.15, "exposure": "C", "grf_type": "rigid",
        "cf_body": 2.0, "cf_plinth": 2.0, "apply_075": False,
    },
}
