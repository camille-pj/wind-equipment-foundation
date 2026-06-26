"""
wind_mop113.py
==============

Pure-Python wind-load engine implementing ASCE MOP 113 (2007), *Substation
Structure Design Guide*, governing equation Eq. 3-1 -- **SI-native form** --
for a *stacked* substation assembly (main equipment seated on a steel lattice
support seated on a foundation; an optional pedestal/plinth may also be stacked).

UNIT POLICY (the single most important consistency rule)
--------------------------------------------------------
Everything in the force math is computed natively in SI.  We never round-trip
through US units.

    Q   = 0.613                       (SI value printed in MOP 113 Eq. 3-1)
    V   : input kph -> m/s  (V_ms = V_kph / 3.6)
    L   : input mm  -> m
    A   : m^2,  pressure : kPa,  force : kN,  line load : kN/m,  moment : kN.m

Feet are used ONLY to read the height-tabulated tables (3-1, 3-4a, 3-4b):
``ft = m * 3.28084``.  The force equation itself stays entirely in SI.

    MOP 113 prints Q = 0.00256 (US) and Q = 0.613 (SI).  These are not exact
    twins, so SI-native results differ from US-unit hand calcs by ~0.1 %
    (negligible).  This tool uses the SI form throughout.

Governing equation (SI), built as a three-stage chain so the user can read off
pressure, line load and force:

    1. velocity pressure   qz = Q * Kz * V_ms^2 * IFW        [Pa -> kPa]
    2. design pressure      p = qz * GRF * Cf                [kPa = kN/m^2]  (= F/A)
    3. line load            w = p * b                        [kN/m]  (b = projected width)
    4. element force        F = p * A = w * L                [kN]

The module is free of any Flask / Plotly imports so it stays trivially
unit-testable.  ``calculate(inputs) -> dict`` returns every intermediate, the
LaTeX strings (numbers already substituted) and the raw Plotly data arrays.
"""

from __future__ import annotations

import math
from typing import Dict, List, Any, Optional


# ---------------------------------------------------------------------------
# CONSTANTS & UNIT CONVERSIONS
# ---------------------------------------------------------------------------
Q_SI = 0.613                # lb-equivalent SI air-density factor, Eq. 3-1 (SI)
Q_US = 0.00256              # US value, shown only in the dual-constant note

KPH_TO_MS = 1.0 / 3.6       # V_ms = V_kph / 3.6
KPH_TO_MPH = 0.621371       # secondary US display only
MM_TO_M = 1.0 / 1000.0
M_TO_FT = 3.28084           # table lookups only
PA_TO_KPA = 1.0 / 1000.0
N_TO_KN = 1.0 / 1000.0
PSF_PER_KPA = 1.0 / 0.04788026   # secondary US display only
KG_TO_KN = 9.80665 / 1000.0

DUAL_CONSTANT_NOTE = (
    "MOP 113 prints Q = 0.00256 (US) and Q = 0.613 (SI). These are not exact "
    "twins, so SI-native results differ from US-unit hand calcs by ~0.1% "
    "(negligible). This tool uses the SI form throughout."
)


# ---------------------------------------------------------------------------
# EMBEDDED MOP 113 TABLES  (hard-coded exactly as in the design guide)
# ---------------------------------------------------------------------------

# Table 3-1 -- Terrain Exposure Coefficient, Kz (height z in feet).
# The "0-15" row is keyed at 15 ft; linear-interpolate between rows.
TABLE_3_1 = {
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

# Table 3-2 -- Power Law Constants (Eq. 3-2 fallback, h > 100 ft).
TABLE_3_2 = {
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
TABLE_3_4A = [
    (33,  {"B": 1.17, "C": 0.96, "D": 0.85}),
    (40,  {"B": 1.15, "C": 0.95, "D": 0.84}),
    (50,  {"B": 1.12, "C": 0.94, "D": 0.84}),
    (60,  {"B": 1.08, "C": 0.92, "D": 0.83}),
    (70,  {"B": 1.06, "C": 0.91, "D": 0.82}),
    (80,  {"B": 1.03, "C": 0.89, "D": 0.81}),
    (90,  {"B": 1.01, "C": 0.88, "D": 0.81}),
    (100, {"B": 1.00, "C": 0.88, "D": 0.80}),
]

# Table 3-4b -- Structure GRF, flexible non-wire-supporting (<1 Hz), eps = 1.0.
TABLE_3_4B = [
    (15,  {"B": 1.59, "C": 1.20, "D": 1.02}),
    (33,  {"B": 1.48, "C": 1.15, "D": 0.99}),
    (40,  {"B": 1.37, "C": 1.11, "D": 0.96}),
    (50,  {"B": 1.33, "C": 1.08, "D": 0.95}),
    (60,  {"B": 1.28, "C": 1.06, "D": 0.94}),
    (70,  {"B": 1.25, "C": 1.05, "D": 0.93}),
    (80,  {"B": 1.22, "C": 1.03, "D": 0.92}),
    (90,  {"B": 1.19, "C": 1.02, "D": 0.91}),
    (100, {"B": 1.17, "C": 1.00, "D": 0.90}),
]

# Table 3-7 -- Aspect Ratio Correction Factor, c (for member-by-member trusses).
# Bands keyed by upper bound of aspect ratio L/width.
TABLE_3_7 = [
    (4,   0.6),    # 0-4
    (8,   0.7),    # 4-8
    (40,  0.8),    # 8-40
    (1e9, 1.0),    # >40
]

# Table 3-8 -- Cf, latticed structures, flat-sided members (function of solidity Phi).
# Stored as descriptive bands; values computed in truss_cf_solidity().
TABLE_3_8_ROWS = [
    {"phi": "< 0.025",   "square": "4.00",        "tri": "3.6"},
    {"phi": "0.025-0.44", "square": "4.1 - 5.2Φ", "tri": "3.7 - 4.5Φ"},
    {"phi": "0.45-0.69", "square": "1.8",         "tri": "1.7"},
    {"phi": "0.70-1.00", "square": "1.3 - 0.7Φ",  "tri": "1.0 + Φ"},
]

# Table 3-9 -- Force Coefficient, Cf (structural shapes / bus / tubular).
TABLE_3_9 = [
    {"shape": "Structural shapes (average value)",  "cf": 1.6},
    {"shape": "Bus: rigid and flexible",            "cf": 1.0},
    {"shape": "Circular",                           "cf": 0.9},
    {"shape": "Hexadecagonal (16-sided polygonal)", "cf": 0.9},
    {"shape": "Dodecagonal (12-sided polygonal)",   "cf": 1.0},
    {"shape": "Octagonal (8-sided polygonal)",      "cf": 1.4},
    {"shape": "Hexagonal (6-sided polygonal)",      "cf": 1.4},
    {"shape": "Square or rectangle",                "cf": 2.0},
]

# Table 3-10 -- Correction factor Cc, latticed round-section members (function of Phi).
TABLE_3_10_ROWS = [
    {"phi": "< 0.30",    "cc": "0.67"},
    {"phi": "0.30-0.79", "cc": "0.47 + 0.67Φ"},
    {"phi": "0.80-1.00", "cc": "1.0"},
]


# ---------------------------------------------------------------------------
# Formatting helper for LaTeX strings
# ---------------------------------------------------------------------------
def _f(x: float, n: int = 3) -> str:
    return f"{x:,.{n}f}"


# ---------------------------------------------------------------------------
# Kz -- Table 3-1 interpolation / Eq. 3-2 fallback
# ---------------------------------------------------------------------------
def compute_kz(h_ft: float, exposure: str) -> Dict[str, Any]:
    """Terrain exposure coefficient Kz at effective height ``h_ft`` (feet).

    * h <= 15 ft       -> 0-15 row value directly.
    * 15 < h <= 100 ft -> linear interpolation between bracketing rows.
    * h > 100 ft       -> Eq. 3-2 power law, Kz = 2.01 (z/zg)^(2/alpha).
    """
    exposure = exposure.upper()
    if h_ft <= 15:
        kz = TABLE_3_1[15][exposure]
        return {"method": "floor", "kz": kz, "h_lo": 15, "h_hi": 15,
                "kz_lo": kz, "kz_hi": kz}
    if h_ft > 100:
        alpha = TABLE_3_2[exposure]["alpha"]
        zg = TABLE_3_2[exposure]["zg"]
        kz = 2.01 * (h_ft / zg) ** (2.0 / alpha)
        return {"method": "powerlaw", "kz": kz, "alpha": alpha, "zg": zg}
    h_lo = max(z for z in TABLE_3_1_HEIGHTS if z <= h_ft)
    h_hi = min(z for z in TABLE_3_1_HEIGHTS if z >= h_ft)
    kz_lo = TABLE_3_1[h_lo][exposure]
    kz_hi = TABLE_3_1[h_hi][exposure]
    kz = kz_lo if h_hi == h_lo else \
        kz_lo + (h_ft - h_lo) / (h_hi - h_lo) * (kz_hi - kz_lo)
    return {"method": "interp", "kz": kz, "h_lo": h_lo, "h_hi": h_hi,
            "kz_lo": kz_lo, "kz_hi": kz_hi}


# ---------------------------------------------------------------------------
# GRF -- gust response factor lookup (Sec. 3.1.5.5)
# ---------------------------------------------------------------------------
def _band_lookup(table: List, h_ft: float, exposure: str) -> Dict[str, Any]:
    """Banded step lookup (NOT interpolation): pick the row where the height
    satisfies ``lower < h <= upper``.  ``ft = m * 3.28084`` is the only place
    feet enter; both ft and the m-equivalent are reported in the band label."""
    exposure = exposure.upper()
    prev = 0
    for upper, row in table:
        if h_ft <= upper:
            if prev == 0:
                band = f"≤{upper} ft (≤{upper * 0.3048:.2f} m)"
            else:
                band = (f">{prev} to {upper} ft "
                        f"(>{prev * 0.3048:.2f} to {upper * 0.3048:.2f} m)")
            return {"grf": row[exposure], "band": band, "band_upper": upper}
        prev = upper
    upper, row = table[-1]
    return {"grf": row[exposure],
            "band": f">{prev} ft (clamped to top band)", "band_upper": upper}


def compute_grf(grf_type: str, h_ft: float, exposure: str) -> Dict[str, Any]:
    """GRF per Sec. 3.1.5.5: rigid flat 0.85, else Table 3-4a/3-4b lookup."""
    if grf_type == "rigid":
        return {"grf": 0.85, "method": "rigid",
                "desc": "Rigid, non-wire-supporting (≥1 Hz), Sec. 3.1.5.5.1"}
    if grf_type == "wire":
        r = _band_lookup(TABLE_3_4A, h_ft, exposure)
        r.update({"method": "table34a", "desc": "Wire-supporting, Table 3-4a (ε = 0.75)"})
        return r
    if grf_type == "flexible":
        r = _band_lookup(TABLE_3_4B, h_ft, exposure)
        r.update({"method": "table34b",
                  "desc": "Flexible non-wire-supporting, Table 3-4b (ε = 1.0)"})
        return r
    raise ValueError(f"Unknown grf_type: {grf_type!r}")


# ---------------------------------------------------------------------------
# Truss helpers
# ---------------------------------------------------------------------------
def aspect_ratio_c(ar: float) -> Dict[str, Any]:
    """Table 3-7 aspect-ratio correction factor c for member-by-member trusses."""
    for upper, c in TABLE_3_7:
        if ar <= upper:
            return {"c": c, "upper": upper}
    return {"c": 1.0, "upper": 1e9}


def cc_round(phi: float) -> float:
    """Table 3-10 correction factor Cc for round latticed members."""
    if phi < 0.30:
        return 0.67
    if phi <= 0.79:
        return 0.47 + 0.67 * phi
    return 1.0


def truss_cf_solidity(phi: float, cross_section: str,
                      member_type: str) -> Dict[str, Any]:
    """Table 3-8 Cf for a complete latticed face as a function of solidity Phi.

    Round members multiply the flat-sided Cf by Cc (Table 3-10).  The tabulated
    Cf already accounts for BOTH windward and leeward faces incl. shielding
    (Sec. 3.1.5.7), so the applied area is the solid area of ONE face.
    """
    square = (cross_section == "square")
    if phi < 0.025:
        cf_flat = 4.00 if square else 3.6
        branch = "Φ < 0.025"
    elif phi <= 0.44:
        cf_flat = (4.1 - 5.2 * phi) if square else (3.7 - 4.5 * phi)
        branch = "0.025–0.44: 4.1 − 5.2Φ" if square else "0.025–0.44: 3.7 − 4.5Φ"
    elif phi <= 0.69:
        cf_flat = 1.8 if square else 1.7
        branch = "0.45–0.69"
    else:
        cf_flat = (1.3 - 0.7 * phi) if square else (1.0 + phi)
        branch = "0.70–1.00: 1.3 − 0.7Φ" if square else "0.70–1.00: 1.0 + Φ"

    cc = None
    cf = cf_flat
    if member_type == "round":
        cc = cc_round(phi)
        cf = cf_flat * cc
    return {"cf": cf, "cf_flat": cf_flat, "cc": cc, "branch": branch,
            "cross_section": cross_section, "member_type": member_type}


# ---------------------------------------------------------------------------
# Velocity pressure (SI)
# ---------------------------------------------------------------------------
def velocity_pressure_pa(Kz: float, V_ms: float, IFW: float) -> float:
    """qz = Q * Kz * V_ms^2 * IFW  (SI), returns Pascals."""
    return Q_SI * Kz * V_ms ** 2 * IFW


# ===========================================================================
# Per-element computation
# ===========================================================================
def _compute_element(el: Dict[str, Any], g: Dict[str, Any]) -> Dict[str, Any]:
    """Compute one stacked element. ``g`` holds the global parameters."""
    label = str(el.get("label", "Element"))
    kind = str(el.get("kind", "equipment_circular"))
    V_ms = g["V_ms"]
    IFW = g["IFW"]
    exposure = g["exposure"]

    # --- height model -------------------------------------------------------
    z_base_m = float(el.get("z_base_mm", 0.0) or 0.0) * MM_TO_M
    # Equipment may supply z_tip (overall tip above NGL); L = z_tip - z_base.
    if el.get("z_tip_mm") not in (None, ""):
        L_m = float(el["z_tip_mm"]) * MM_TO_M - z_base_m
    else:
        L_m = float(el.get("L_mm", 0.0) or 0.0) * MM_TO_M
    L_m = max(L_m, 1e-6)
    z_top_m = z_base_m + L_m

    # Effective height for Kz (Sec. 3.1.5.2.2):
    #   "tip"      -> top of element            (default; conservative)
    #   "centroid" -> z_base + L/2              (less conservative; defensible)
    #   "custom"   -> explicit kz_height_mm     (evaluate at a controlling
    #                 reference height, e.g. a base plinth taken at the
    #                 governing equipment height)
    kz_basis = str(el.get("kz_basis", "tip"))
    if kz_basis == "custom" and el.get("kz_height_mm") not in (None, ""):
        z_eff_m = float(el["kz_height_mm"]) * MM_TO_M
    elif kz_basis == "centroid":
        z_eff_m = z_base_m + L_m / 2.0
    else:
        z_eff_m = z_top_m
    h_ft = z_eff_m * M_TO_FT

    # --- Kz & GRF -----------------------------------------------------------
    kz_info = compute_kz(h_ft, exposure)
    Kz = kz_info["kz"]
    grf_type = str(el.get("grf_type", "rigid"))
    grf_info = compute_grf(grf_type, h_ft, exposure)
    GRF = grf_info["grf"]

    # --- velocity pressure --------------------------------------------------
    qz_pa = velocity_pressure_pa(Kz, V_ms, IFW)
    qz_kpa = qz_pa * PA_TO_KPA

    # --- kind-specific area / Cf / force ------------------------------------
    # All forces in kN, pressures in kPa, line loads in kN/m.
    # The centroid elevation zbar is the line of action used for overturning.
    zbar_m = z_base_m + L_m / 2.0
    extra: Dict[str, Any] = {}      # kind-specific detail for the report
    cf_used = None

    if kind == "equipment_circular":
        D_m = float(el["D_mm"]) * MM_TO_M
        cf_used = float(el.get("cf", 0.9))
        b_x = b_y = D_m
        A_x = A_y = D_m * L_m
        p_kpa = qz_kpa * GRF * cf_used
        Fx = p_kpa * A_x
        Fy = p_kpa * A_y
        w_x = p_kpa * b_x
        w_y = p_kpa * b_y
        draw_w = D_m
        extra = {"D_m": D_m}

    elif kind == "equipment_rectangular":
        WX_m = float(el["WX_mm"]) * MM_TO_M
        WY_m = float(el["WY_mm"]) * MM_TO_M
        cf_used = float(el.get("cf", 2.0))
        b_x, b_y = WX_m, WY_m
        A_x, A_y = WX_m * L_m, WY_m * L_m
        p_kpa = qz_kpa * GRF * cf_used
        Fx = p_kpa * A_x
        Fy = p_kpa * A_y
        w_x = p_kpa * b_x
        w_y = p_kpa * b_y
        draw_w = WX_m
        extra = {"WX_m": WX_m, "WY_m": WY_m}

    elif kind == "pedestal_plinth":
        width_m = float(el["width_mm"]) * MM_TO_M
        height_m = float(el.get("height_mm", el.get("L_mm", 0))) * MM_TO_M
        cf_used = float(el.get("cf", 2.0))
        b_x = b_y = width_m
        A_x = A_y = width_m * height_m
        p_kpa = qz_kpa * GRF * cf_used
        Fx = Fy = p_kpa * A_x
        w_x = w_y = p_kpa * b_x
        draw_w = width_m
        zbar_m = z_base_m + height_m / 2.0
        extra = {"width_m": width_m, "height_m": height_m}

    elif kind == "lattice_truss":
        route = str(el.get("route", "A"))
        if route == "A":
            # --- Route A: solidity method (one solid face). ---
            fw_m = float(el["face_width_mm"]) * MM_TO_M
            fh_m = float(el.get("face_height_mm", el.get("L_mm", 0))) * MM_TO_M
            Ag = fw_m * fh_m
            # Solidity ratio: entered directly, or computed from a member
            # take-off:  Phi = sum(b_i * L_i * n_i) / Ag.
            phi_mode = str(el.get("phi_mode", "direct"))
            solid_area: Optional[float] = None
            takeoff: Optional[List[Dict[str, Any]]] = None
            if phi_mode == "takeoff":
                takeoff = []
                solid_area = 0.0
                for m in el.get("phi_members", []):
                    bm = float(m["b_mm"]) * MM_TO_M
                    Lm = float(m["L_mm"]) * MM_TO_M
                    nm = float(m.get("n", 1))
                    am = bm * Lm * nm
                    solid_area += am
                    takeoff.append({"b_m": bm, "L_m": Lm, "n": nm, "area": am})
                phi = solid_area / Ag if Ag > 0 else 0.0
            else:
                phi = float(el["phi"])
            cross = str(el.get("cross_section", "square"))
            mtype = str(el.get("member_type", "flat"))
            cinfo = truss_cf_solidity(phi, cross, mtype)
            cf_used = cinfo["cf"]
            yawed = bool(el.get("yawed_wind", False)) and cross == "square"
            if yawed:
                cf_used *= 1.15
            A_solid = phi * Ag
            b = A_solid / max(fh_m, 1e-6)         # smeared solid width
            p_kpa = qz_kpa * GRF * cf_used
            Fx = Fy = p_kpa * A_solid
            w_x = w_y = p_kpa * b
            A_x = A_y = A_solid
            b_x = b_y = b
            draw_w = fw_m
            zbar_m = z_base_m + fh_m / 2.0
            extra = {"route": "A", "face_w_m": fw_m, "face_h_m": fh_m,
                     "Ag": Ag, "phi": phi, "A_solid": A_solid,
                     "cf_info": cinfo, "yawed": yawed,
                     "phi_mode": phi_mode,
                     "solid_area": (solid_area if solid_area is not None
                                    else phi * Ag),
                     "takeoff": takeoff}
        else:
            # --- Route B: member-by-member (no shielding credit). ---
            members = el.get("members", [])
            rows = []
            F_sum = 0.0
            A_sum = 0.0
            for m in members:
                b_m = float(m["b_mm"]) * MM_TO_M
                L_mem = float(m["L_mm"]) * MM_TO_M
                n = float(m.get("n", 1))
                shape = str(m.get("shape", "flat"))
                ar = L_mem / max(b_m, 1e-6)
                cinfo = aspect_ratio_c(ar)
                c = cinfo["c"]
                base_cf = 1.6 if shape != "round" else 0.9   # Table 3-9
                cf_m = c * base_cf
                A_m = b_m * L_mem * n
                p_m = qz_kpa * GRF * cf_m
                F_m = p_m * A_m
                F_sum += F_m
                A_sum += A_m
                rows.append({"b_m": b_m, "L_m": L_mem, "n": n, "shape": shape,
                             "ar": ar, "c": c, "cf_m": cf_m, "A_m": A_m,
                             "p_m": p_m, "F_m": F_m})
            Fx = Fy = F_sum
            A_x = A_y = A_sum
            p_kpa = (F_sum / A_sum) if A_sum > 0 else 0.0   # effective p = F/A
            b = A_sum / max(L_m, 1e-6)
            b_x = b_y = b
            w_x = w_y = p_kpa * b
            draw_w = max((r["b_m"] for r in rows), default=0.3) * 4
            zbar_m = z_base_m + L_m / 2.0
            extra = {"route": "B", "rows": rows, "A_sum": A_sum, "F_sum": F_sum}
    else:
        raise ValueError(f"Unknown element kind: {kind!r}")

    governing = "X = Y (symmetric)" if abs(Fx - Fy) < 1e-9 else \
        ("X" if Fx >= Fy else "Y")

    res = {
        "label": label, "kind": kind,
        "z_base_m": z_base_m, "L_m": L_m, "z_top_m": z_top_m,
        "z_eff_m": z_eff_m, "h_ft": h_ft, "kz_basis": kz_basis,
        "Kz": Kz, "kz_info": kz_info,
        "grf_type": grf_type, "GRF": GRF, "grf_info": grf_info,
        "qz_pa": qz_pa, "qz_kpa": qz_kpa,
        "cf": cf_used, "p_kpa": p_kpa,
        "A_x": A_x, "A_y": A_y, "b_x": b_x, "b_y": b_y,
        "w_x": w_x, "w_y": w_y, "Fx": Fx, "Fy": Fy,
        "zbar_m": zbar_m, "governing": governing,
        "draw_w_m": draw_w, "extra": extra,
    }
    res["steps"] = _element_steps(res, g)
    return res


# ===========================================================================
# Main entry point
# ===========================================================================
def calculate(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Run the full stacked MOP 113 Eq. 3-1 (SI) calculation.

    ``inputs`` shape::

        {
          "V_kph": 310, "IFW": 1.15, "exposure": "C",
          "elements": [ {element 1}, {element 2}, ... ]   # top-to-bottom or any order
        }

    Each element dict carries ``label``, ``kind`` and the kind-specific fields
    documented in ``_compute_element``.  The foundation carries no wind and is
    simply not included in the stack.
    """
    V_kph = float(inputs["V_kph"])
    V_ms = V_kph * KPH_TO_MS
    V_mph = V_kph * KPH_TO_MPH
    IFW = float(inputs["IFW"])
    exposure = str(inputs.get("exposure", "C")).upper()

    g = {"V_kph": V_kph, "V_ms": V_ms, "V_mph": V_mph, "IFW": IFW,
         "exposure": exposure, "Q": Q_SI}

    elements = [_compute_element(el, g) for el in inputs.get("elements", [])]

    # --- assembly aggregate -------------------------------------------------
    # MOP 113 wind is directional: FX and FY are applied to the model SEPARATELY
    # (one principal direction at a time).  We do NOT combine them into a vector
    # resultant -- that is not an Eq. 3-1 step.
    FX = sum(e["Fx"] for e in elements)
    FY = sum(e["Fy"] for e in elements)

    # Base shear = total wind force per direction (all elements act above base).
    base_shear_x, base_shear_y = FX, FY
    # Overturning about the base: M = sum(F_i * zbar_i) per direction.
    Mx = sum(e["Fx"] * e["zbar_m"] for e in elements)
    My = sum(e["Fy"] * e["zbar_m"] for e in elements)
    M_gov = max(Mx, My)

    governing = "X = Y (symmetric)" if abs(FX - FY) < 1e-9 else \
        ("X" if FX >= FY else "Y")

    assembly_steps = _assembly_steps(elements, FX, FY,
                                     base_shear_x, base_shear_y, Mx, My, governing)

    figures = _build_figures(elements, exposure, FX, FY, governing)

    return {
        "globals": g,
        "note": DUAL_CONSTANT_NOTE,
        "elements": elements,
        "summary": {
            "FX_kN": FX, "FY_kN": FY,
            "governing": governing,
            "base_shear_x_kN": base_shear_x, "base_shear_y_kN": base_shear_y,
            "Mx_kNm": Mx, "My_kNm": My, "M_gov_kNm": M_gov,
            "per_element": [
                {"label": e["label"], "qz_kpa": e["qz_kpa"], "p_kpa": e["p_kpa"],
                 "w_x": e["w_x"], "w_y": e["w_y"], "Fx": e["Fx"], "Fy": e["Fy"],
                 "Kz": e["Kz"], "governing": e["governing"]}
                for e in elements
            ],
        },
        "assembly_steps": assembly_steps,
        "figures": figures,
    }


# ---------------------------------------------------------------------------
# LaTeX builders
# ---------------------------------------------------------------------------
def _kz_lines(e: Dict[str, Any], exposure: str) -> List[str]:
    ki = e["kz_info"]
    basis = {"tip": "tip / top of element", "centroid": "centroid",
             "custom": "custom reference height"}.get(e["kz_basis"], "tip")
    head = (r"\text{Effective height (" + basis + r", §3.1.5.2.2): } z = "
            + _f(e["z_eff_m"], 3) + r"\ \text{m} = " + _f(e["h_ft"], 3)
            + r"\ \text{ft}")
    if ki["method"] == "interp":
        return [
            head,
            r"K_z = K_{z,lo} + \dfrac{h - h_{lo}}{h_{hi} - h_{lo}}\,"
            r"(K_{z,hi} - K_{z,lo})\quad(\text{Table 3-1, Exp. " + exposure + r"})",
            r"K_z = " + _f(ki["kz_lo"], 3) + r" + \dfrac{" + _f(e["h_ft"], 3)
            + r" - " + _f(ki["h_lo"], 0) + r"}{" + _f(ki["h_hi"], 0) + r" - "
            + _f(ki["h_lo"], 0) + r"}\,(" + _f(ki["kz_hi"], 3) + r" - "
            + _f(ki["kz_lo"], 3) + r") = " + _f(e["Kz"], 4),
        ]
    if ki["method"] == "floor":
        return [head, r"h \leq 15\ \text{ft} \Rightarrow K_z = "
                + r"\text{(0--15 row, Exp. " + exposure + r")} = " + _f(e["Kz"], 4)]
    return [head,
            r"h > 100\ \text{ft} \Rightarrow K_z = 2.01\left(\dfrac{z}{z_g}"
            r"\right)^{2/\alpha} = 2.01\left(\dfrac{" + _f(e["h_ft"], 3) + r"}{"
            + _f(ki["zg"], 0) + r"}\right)^{2/" + _f(ki["alpha"], 1) + r"} = "
            + _f(e["Kz"], 4)]


def _element_steps(e: Dict[str, Any], g: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the per-element LaTeX report (numbers already substituted)."""
    steps: List[Dict[str, Any]] = []
    exposure = g["exposure"]

    # 1 -- height & Kz
    steps.append({"title": "Effective height &amp; $K_z$ (Table 3-1)",
                  "lines": _kz_lines(e, exposure)})

    # 2 -- velocity pressure
    steps.append({"title": "Velocity pressure $q_z$ (SI)", "lines": [
        r"q_z = Q \cdot K_z \cdot V_{ms}^2 \cdot I_{FW}",
        r"q_z = " + _f(g["Q"], 3) + r" \times " + _f(e["Kz"], 4) + r" \times "
        + _f(g["V_ms"], 3) + r"^2 \times " + _f(g["IFW"], 2) + r" = "
        + _f(e["qz_pa"], 1) + r"\ \text{Pa} = " + _f(e["qz_kpa"], 4)
        + r"\ \text{kPa}",
    ]})

    # 3 -- design pressure & force, kind-specific
    GRF = e["GRF"]
    ex = e["extra"]
    if e["kind"] in ("equipment_circular", "equipment_rectangular", "pedestal_plinth"):
        cf = e["cf"]
        lines = [
            r"p = q_z \cdot GRF \cdot C_f = " + _f(e["qz_kpa"], 4) + r" \times "
            + _f(GRF, 3) + r" \times " + _f(cf, 2) + r" = " + _f(e["p_kpa"], 4)
            + r"\ \text{kPa}",
        ]
        if e["kind"] == "equipment_circular":
            D = ex["D_m"]
            lines += [
                r"A = D \cdot L = " + _f(D, 4) + r" \times " + _f(e["L_m"], 3)
                + r" = " + _f(e["A_x"], 4) + r"\ \text{m}^2\quad(b = D = "
                + _f(D, 4) + r"\ \text{m})",
                r"w = p \cdot b = " + _f(e["p_kpa"], 4) + r" \times " + _f(D, 4)
                + r" = " + _f(e["w_x"], 4) + r"\ \text{kN/m}",
                r"F = p \cdot A = " + _f(e["p_kpa"], 4) + r" \times "
                + _f(e["A_x"], 4) + r" = " + _f(e["Fx"], 3)
                + r"\ \text{kN}\quad(F_X = F_Y,\ \text{circular})",
            ]
        elif e["kind"] == "equipment_rectangular":
            lines += [
                r"A_X = W_X \cdot L = " + _f(ex["WX_m"], 4) + r" \times "
                + _f(e["L_m"], 3) + r" = " + _f(e["A_x"], 4) + r"\ \text{m}^2,"
                r"\quad A_Y = W_Y \cdot L = " + _f(ex["WY_m"], 4) + r" \times "
                + _f(e["L_m"], 3) + r" = " + _f(e["A_y"], 4) + r"\ \text{m}^2",
                r"F_X = p \cdot A_X = " + _f(e["Fx"], 3) + r"\ \text{kN},\quad "
                r"F_Y = p \cdot A_Y = " + _f(e["Fy"], 3) + r"\ \text{kN}\quad("
                + e["governing"] + r"\ \text{governs})",
                r"w_X = p \cdot W_X = " + _f(e["w_x"], 4) + r"\ \text{kN/m},\quad "
                r"w_Y = p \cdot W_Y = " + _f(e["w_y"], 4) + r"\ \text{kN/m}",
            ]
        else:  # plinth
            lines += [
                r"A = w_p \cdot h_p = " + _f(ex["width_m"], 3) + r" \times "
                + _f(ex["height_m"], 3) + r" = " + _f(e["A_x"], 4)
                + r"\ \text{m}^2",
                r"w = p \cdot b = " + _f(e["w_x"], 4) + r"\ \text{kN/m},\quad "
                r"F = p \cdot A = " + _f(e["Fx"], 3) + r"\ \text{kN}",
            ]
        steps.append({"title": "Design pressure, area &amp; force", "lines": lines})

    elif e["kind"] == "lattice_truss" and ex["route"] == "A":
        ci = ex["cf_info"]
        lines = [
            r"\text{Gross face area } A_g = b_f \cdot h_f = " + _f(ex["face_w_m"], 3)
            + r" \times " + _f(ex["face_h_m"], 3) + r" = " + _f(ex["Ag"], 4)
            + r"\ \text{m}^2",
        ]
        # Show the solidity-ratio take-off when Phi was computed from members.
        if ex.get("phi_mode") == "takeoff" and ex.get("takeoff"):
            terms = " + ".join(
                _f(t["b_m"], 3) + r"\times" + _f(t["L_m"], 3) + r"\times" + _f(t["n"], 0)
                for t in ex["takeoff"])
            lines.append(
                r"\Phi = \dfrac{\sum b_i L_i n_i}{A_g} = \dfrac{" + terms + r"}{"
                + _f(ex["Ag"], 4) + r"} = \dfrac{" + _f(ex["solid_area"], 4) + r"}{"
                + _f(ex["Ag"], 4) + r"} = " + _f(ex["phi"], 3))
        else:
            lines.append(r"\Phi = " + _f(ex["phi"], 3) + r"\quad(\text{entered directly})")
        lines.append(
            r"C_f\ (\text{Table 3-8, " + ci["cross_section"] + r", "
            + ci["branch"] + r"}) = " + _f(ci["cf_flat"], 3))
        if ci["cc"] is not None:
            lines.append(r"\text{Round members: } C_f = C_f \cdot C_c = "
                         + _f(ci["cf_flat"], 3) + r" \times " + _f(ci["cc"], 3)
                         + r" = " + _f(ci["cf"], 3) + r"\quad(\text{Table 3-10})")
        if ex["yawed"]:
            lines.append(r"\text{Yawed wind (square, §3.1.5.6): } C_f \times 1.15 = "
                         + _f(e["cf"], 3))
        lines += [
            r"A_{solid} = \Phi \cdot A_g = " + _f(ex["phi"], 3) + r" \times "
            + _f(ex["Ag"], 4) + r" = " + _f(ex["A_solid"], 4) + r"\ \text{m}^2",
            r"p = q_z \cdot GRF \cdot C_f = " + _f(e["qz_kpa"], 4) + r" \times "
            + _f(GRF, 3) + r" \times " + _f(e["cf"], 3) + r" = " + _f(e["p_kpa"], 4)
            + r"\ \text{kPa}",
            r"F = p \cdot A_{solid} = " + _f(e["Fx"], 3) + r"\ \text{kN},\quad "
            r"w = p \cdot b = " + _f(e["w_x"], 4) + r"\ \text{kN/m}\ (b = \Phi b_f)",
        ]
        steps.append({"title": "Lattice support — Route A (solidity, Table 3-8)",
                      "lines": lines})

    elif e["kind"] == "lattice_truss" and ex["route"] == "B":
        lines = [r"\text{Per member: } AR = L/b,\ c\ (\text{Table 3-7}),\ "
                 r"C_f = c \cdot 1.6\ (\text{Table 3-9}),\ F = q_z\,GRF\,C_f\,A"]
        for i, r in enumerate(ex["rows"], 1):
            lines.append(
                r"\text{m}_{" + str(i) + r"}:\ AR = " + _f(r["ar"], 2)
                + r",\ c = " + _f(r["c"], 2) + r",\ C_f = " + _f(r["cf_m"], 3)
                + r",\ A = " + _f(r["A_m"], 4) + r"\ \text{m}^2,\ F = "
                + _f(r["F_m"], 3) + r"\ \text{kN}")
        lines.append(r"\sum F = " + _f(ex["F_sum"], 3) + r"\ \text{kN}\quad"
                     r"(\text{no shielding credit; conservative})")
        steps.append({"title": "Lattice support — Route B (member-by-member)",
                      "lines": lines})

    return steps


def _assembly_steps(elements, FX, FY,
                    bsx, bsy, Mx, My, governing) -> List[Dict[str, Any]]:
    fx_terms = " + ".join(_f(e["Fx"], 3) for e in elements) or "0"
    fy_terms = " + ".join(_f(e["Fy"], 3) for e in elements) or "0"
    steps = [
        {"title": "Totals", "lines": [
            r"\text{Total } F_X = \sum F_{X,i} = " + fx_terms + r" = " + _f(FX, 3)
            + r"\ \text{kN}",
            r"\text{Total } F_Y = \sum F_{Y,i} = " + fy_terms + r" = " + _f(FY, 3)
            + r"\ \text{kN}",
            r"\text{(} F_X \text{ and } F_Y \text{ are applied to the model "
            r"separately — directional wind, no vector resultant. } "
            + governing + r"\text{ is the larger.)}",
        ]},
        {"title": "Base shear &amp; overturning", "lines": [
            r"V_{base,X} = \sum F_{X,i} = " + _f(bsx, 3) + r"\ \text{kN},\quad "
            r"V_{base,Y} = " + _f(bsy, 3) + r"\ \text{kN}",
            r"M = \sum F_i\, \bar z_i \quad\Rightarrow\quad M_X = " + _f(Mx, 3)
            + r"\ \text{kN·m},\quad M_Y = " + _f(My, 3) + r"\ \text{kN·m}",
        ]},
    ]
    return steps


# ---------------------------------------------------------------------------
# Plotly figure data builder
# ---------------------------------------------------------------------------
def _build_figures(elements, exposure, FX, FY, governing) -> Dict[str, Any]:
    # Stack elevation schematic.
    stack = []
    for e in elements:
        stack.append({
            "label": e["label"], "kind": e["kind"],
            "x0": -e["draw_w_m"] / 2.0, "x1": e["draw_w_m"] / 2.0,
            "y0": round(e["z_base_m"], 4), "y1": round(e["z_top_m"], 4),
            "Kz": round(e["Kz"], 4),
            "F": round(max(e["Fx"], e["Fy"]), 3),
            "z_base": round(e["z_base_m"], 3), "z_top": round(e["z_top_m"], 3),
            "width": round(e["draw_w_m"], 3),
        })
    z_max = max((e["z_top_m"] for e in elements), default=1.0)
    w_max = max((e["draw_w_m"] for e in elements), default=0.5)

    # Force breakdown per element.
    force_breakdown = {
        "labels": [e["label"] for e in elements],
        "Fx": [round(e["Fx"], 3) for e in elements],
        "Fy": [round(e["Fy"], 3) for e in elements],
        "FX_total": round(FX, 3), "FY_total": round(FY, 3),
        "governing": governing,
    }

    # Kz interpolation curve with each element highlighted.
    kz_curve = {
        "heights": list(TABLE_3_1_HEIGHTS),
        "kz": [TABLE_3_1[h][exposure] for h in TABLE_3_1_HEIGHTS],
        "exposure": exposure,
        "points": [{"label": e["label"], "h_ft": round(e["h_ft"], 3),
                    "kz": round(e["Kz"], 4)} for e in elements],
    }

    return {"stack": {"elements": stack, "z_max": round(z_max, 3),
                      "w_max": round(w_max, 3)},
            "force_breakdown": force_breakdown, "kz_curve": kz_curve}


# ---------------------------------------------------------------------------
# Illustrative sample presets (also surfaced in the UI). These are sample
# inputs for demonstration only -- not validated project data.
# ---------------------------------------------------------------------------
def _plinth(kz_height_mm, label="Plinth"):
    # Sample plinth: Kz evaluated at a custom reference height (here the
    # governing equipment height) via the custom-height basis.
    return {"label": label, "kind": "pedestal_plinth", "z_base_mm": 0,
            "width_mm": 700, "height_mm": 200, "cf": 2.0,
            "kz_basis": "custom", "kz_height_mm": kz_height_mm,
            "grf_type": "rigid"}


PRESETS = {
    "PI": {
        "label": "Preset 1 — Post Insulator (PI), circular + plinth",
        "V_kph": 310, "IFW": 1.15, "exposure": "C",
        "elements": [
            {"label": "PI", "kind": "equipment_circular", "z_base_mm": 0,
             "z_tip_mm": 9189, "D_mm": 345, "cf": 0.9,
             "kz_basis": "tip", "grf_type": "rigid"},
            _plinth(9189),
        ],
    },
    "CT": {
        "label": "Preset 2 — Current Transformer (CT), circular + plinth",
        "V_kph": 310, "IFW": 1.15, "exposure": "C",
        "elements": [
            {"label": "CT", "kind": "equipment_circular", "z_base_mm": 0,
             "z_tip_mm": 10265, "D_mm": 440, "cf": 0.9,
             "kz_basis": "tip", "grf_type": "rigid"},
            _plinth(10265),
        ],
    },
    "CB": {
        "label": "Preset 3 — Circuit Breaker (CB), rectangular + plinth",
        "V_kph": 310, "IFW": 1.15, "exposure": "C",
        "elements": [
            {"label": "CB", "kind": "equipment_rectangular", "z_base_mm": 0,
             "z_tip_mm": 9336, "WX_mm": 2853.8, "WY_mm": 2353.1, "cf": 2.0,
             "kz_basis": "tip", "grf_type": "rigid"},
            _plinth(9336),
        ],
    },
    "PI_STACK": {
        "label": "Preset 4 — PI on a 2 m lattice support (stacked)",
        "V_kph": 310, "IFW": 1.15, "exposure": "C",
        "elements": [
            {"label": "PI", "kind": "equipment_circular", "z_base_mm": 2000,
             "z_tip_mm": 9189, "D_mm": 345, "cf": 0.9,
             "kz_basis": "tip", "grf_type": "rigid"},
            {"label": "Steel support", "kind": "lattice_truss", "route": "A",
             "z_base_mm": 0, "face_width_mm": 500, "face_height_mm": 2000,
             "L_mm": 2000, "phi": 0.20, "phi_mode": "direct",
             "phi_members": [{"b_mm": 90, "L_mm": 2000, "n": 4}],
             "cross_section": "square", "member_type": "flat",
             "yawed_wind": False, "kz_basis": "tip", "grf_type": "rigid"},
        ],
    },
}
