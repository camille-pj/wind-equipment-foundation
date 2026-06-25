"""
seismic_mop113.py
=================

Pure-Python seismic engine implementing ASCE MOP 113 (2007) Section 3.1.7 --
the simplified NEHRP / FEMA 450 equivalent-lateral-force method (the ASCE 7
spectral framework).  It complements IEEE 693 (2005) for equipment
qualification.

Governing chain (Eqs. 3-6 .. 3-10):

    S_DS = (2/3) * Fa * Ss                     (Eq. 3-6)
    S_D1 = (2/3) * Fv * S1                      (Eq. 3-7)
    S_a  = S_DS                  if T <= T0     (Eq. 3-8)
    S_a  = S_D1 / T              if T  > T0     (Eq. 3-9)     with T0 = S_D1 / S_DS
    F_E  = (S_a / R) * W_eff * I_FE * I_MV      (Eq. 3-10)

Units / conventions (strict):
    * Ss, S1, S_DS, S_D1, S_a, a_vert are in *g* (dimensionless multiples).
    * S_a is used as a seismic *coefficient* (fraction of g), so with W_eff in
      kN, F_E comes out directly in kN -- we never convert S_a to m/s^2.
    * All forces in kN.
    * W_eff = W + 0.5 * (attached wire weight)   (Eq. 3-10 definition).

Faithful manual rules surfaced in the report:
    * Vertical ground acceleration to combine with the horizontal base shear is
      0.8 * the design horizontal (Sec 3.1.7):  a_vert = 0.8 * S_a (g).
    * If a vertical *force* is shown:  F_E,vert = 0.8 * S_a * W_eff * I_FE
      -- WITHOUT the R reduction and WITHOUT I_MV (R is a lateral-ductility
      factor that does not apply to vertical response).  We deliberately do NOT
      use 0.8 * F_E, which would wrongly carry the R reduction into the vertical.
    * Gravity friction is not counted as resistance to seismic forces.
    * Earthquake is not combined with extreme wind or ice.

No Flask / Plotly imports -- importable and unit-testable on its own.
"""

from __future__ import annotations

from typing import Dict, List, Any, Optional


# ---------------------------------------------------------------------------
# Unit conversion (weight) -- W is reported/used in kN
# ---------------------------------------------------------------------------
KG_TO_KN = 9.80665 / 1000.0     # kN = kg * 9.80665 / 1000


# ---------------------------------------------------------------------------
# EMBEDDED MOP 113 SEISMIC TABLES (hard-coded exactly)
# ---------------------------------------------------------------------------

# Table 3-12 -- Site Coefficient Fa, interpolated on Ss.
# Column anchors (Ss): 0.25, 0.50, 0.75, 1.00, 1.25 ; clamp outside the range.
FA_SS_ANCHORS = [0.25, 0.50, 0.75, 1.00, 1.25]
TABLE_3_12 = {
    "A": [0.8, 0.8, 0.8, 0.8, 0.8],
    "B": [1.0, 1.0, 1.0, 1.0, 1.0],
    "C": [1.2, 1.2, 1.1, 1.0, 1.0],
    "D": [1.6, 1.4, 1.2, 1.1, 1.0],
    "E": [2.5, 1.7, 1.2, 0.9, 0.9],
    "F": None,   # site-specific required
}

# Table 3-13 -- Site Coefficient Fv, interpolated on S1.
# Column anchors (S1): 0.1, 0.2, 0.3, 0.4, 0.5 ; clamp outside the range.
FV_S1_ANCHORS = [0.1, 0.2, 0.3, 0.4, 0.5]
TABLE_3_13 = {
    "A": [0.8, 0.8, 0.8, 0.8, 0.8],
    "B": [1.0, 1.0, 1.0, 1.0, 1.0],
    "C": [1.7, 1.6, 1.5, 1.4, 1.3],
    "D": [2.4, 2.0, 1.8, 1.6, 1.5],
    "E": [3.5, 3.2, 2.8, 2.4, 2.4],
    "F": None,
}

# Response Modification Factor R (Sec 3.1.7.3) -- USD vs ASD columns.
R_TABLE = [
    {"type": "Moment-resisting steel frame",            "usd": 3.0, "asd": 4.0},
    {"type": "Trussed tower",                           "usd": 3.0, "asd": 4.0},
    {"type": "Cantilever support structures",           "usd": 2.0, "asd": 2.7},
    {"type": "Tubular pole",                            "usd": 1.5, "asd": 2.0},
    {"type": "Steel and aluminum bus supports",         "usd": 2.0, "asd": 2.7},
    {"type": "Station post insulators",                 "usd": 1.0, "asd": 1.3},
    {"type": "Rigid bus (aluminum and copper)",         "usd": 2.0, "asd": 2.7},
    {"type": "Structures with natural frequency > 25 Hz", "usd": 1.3, "asd": 1.7},
]

# Importance Factor IFE (Sec 3.1.7.2).
IFE_TABLE = [
    {"category": "Essential structures & equipment",                 "ife": 1.25},
    {"category": "Anchorage for essential structures & equipment",   "ife": 2.0},
    {"category": "All other structures & equipment",                 "ife": 1.0},
    {"category": "All other anchorages",                             "ife": 1.5},
]

# Multi-mode factor IMV (Eq. 3-10).
IMV_TABLE = [
    {"label": "Single dominant mode", "imv": 1.0},
    {"label": "Multiple modes considered", "imv": 1.5},
]

SITE_F_WARNING = (
    "Site Class F requires a site-specific geotechnical investigation and "
    "dynamic site response analysis (FEMA 450). Fa and Fv must be entered "
    "manually."
)
R_GT3_NOTE = (
    "R > 3 (USD) implies inelastic energy-dissipation mechanisms are relied "
    "upon; the structure must be detailed to develop them before buckling / "
    "non-ductile failure (Sec 3.1.7.3)."
)
HAZARD_NOTE = (
    "MOP 113 §3.1.7 uses the ASCE 7 / FEMA 450 spectral framework and needs "
    "the 0.2-s (Ss) and 1.0-s (S1) spectral accelerations from a site-specific "
    "PSHA / seismic hazard study (e.g. PHIVOLCS Hazard Hunter PH). NSCP 2015 "
    "§208 is UBC-97-based (Z, Ca, Cv, Na, Nv) and does NOT produce Ss/S1, so "
    "its coefficients cannot be substituted into these fields."
)


# ---------------------------------------------------------------------------
# Formatting helper for LaTeX
# ---------------------------------------------------------------------------
def _f(x: float, n: int = 3) -> str:
    return f"{x:,.{n}f}"


# ---------------------------------------------------------------------------
# Straight-line interpolation with end-column clamping (Tables 3-12 / 3-13)
# ---------------------------------------------------------------------------
def interp_clamped(anchors: List[float], values: List[float],
                   x: float) -> Dict[str, Any]:
    """Linear-interpolate ``values`` (defined at ``anchors``) at ``x``.

    Clamps to the end columns when ``x`` is below the first or above the last
    anchor.  Returns the value plus the bracketing anchors so the report can
    show the interpolation that was used.
    """
    if x <= anchors[0]:
        return {"value": values[0], "clamped": "low",
                "x_lo": anchors[0], "x_hi": anchors[0],
                "y_lo": values[0], "y_hi": values[0]}
    if x >= anchors[-1]:
        return {"value": values[-1], "clamped": "high",
                "x_lo": anchors[-1], "x_hi": anchors[-1],
                "y_lo": values[-1], "y_hi": values[-1]}
    for i in range(len(anchors) - 1):
        x_lo, x_hi = anchors[i], anchors[i + 1]
        if x_lo <= x <= x_hi:
            y_lo, y_hi = values[i], values[i + 1]
            v = y_lo + (x - x_lo) / (x_hi - x_lo) * (y_hi - y_lo)
            return {"value": v, "clamped": None,
                    "x_lo": x_lo, "x_hi": x_hi, "y_lo": y_lo, "y_hi": y_hi}
    # Unreachable, but keep mypy/readers happy.
    return {"value": values[-1], "clamped": "high",
            "x_lo": anchors[-1], "x_hi": anchors[-1],
            "y_lo": values[-1], "y_hi": values[-1]}


def lookup_fa(site_class: str, Ss: float) -> Dict[str, Any]:
    """Fa from Table 3-12 (interpolate on Ss)."""
    row = TABLE_3_12[site_class]
    if row is None:
        return {"value": None, "site_specific": True}
    r = interp_clamped(FA_SS_ANCHORS, row, Ss)
    r["site_specific"] = False
    return r


def lookup_fv(site_class: str, S1: float) -> Dict[str, Any]:
    """Fv from Table 3-13 (interpolate on S1)."""
    row = TABLE_3_13[site_class]
    if row is None:
        return {"value": None, "site_specific": True}
    r = interp_clamped(FV_S1_ANCHORS, row, S1)
    r["site_specific"] = False
    return r


def lookup_r(r_type: str, basis: str) -> float:
    """R from the Sec 3.1.7.3 table for the given type and USD/ASD basis."""
    key = "usd" if basis.upper() == "USD" else "asd"
    for row in R_TABLE:
        if row["type"] == r_type:
            return row[key]
    raise ValueError(f"Unknown R structure type: {r_type!r}")


# ===========================================================================
# Main entry point
# ===========================================================================
def calculate_seismic(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Run the MOP 113 §3.1.7 equivalent-lateral-force calculation.

    ``inputs`` keys::

        tag           : str
        Ss, S1        : float (g)
        T             : float (s)
        W_kN          : float (kN)    dead load incl. rigidly attached equipment
        wire_kN       : float (kN)    attached wire weight (50% added to W)
        site_class    : "A".."F"
        design_basis  : "USD" | "ASD"
        r_type        : str (key into R_TABLE)
        IFE, IMV      : float
        Fa_manual,
        Fv_manual     : float (required only when site_class == "F")
    """
    tag = str(inputs.get("tag", "Structure"))
    Ss = float(inputs["Ss"])
    S1 = float(inputs["S1"])
    T = float(inputs["T"])
    W_kN = float(inputs["W_kN"])
    wire_kN = float(inputs.get("wire_kN", 0.0) or 0.0)
    site_class = str(inputs.get("site_class", "D")).upper()
    basis = str(inputs.get("design_basis", "USD")).upper()
    r_type = str(inputs.get("r_type", "Tubular pole"))
    IFE = float(inputs["IFE"])
    IMV = float(inputs.get("IMV", 1.0))

    # --- effective weight ---------------------------------------------------
    W_eff = W_kN + 0.5 * wire_kN

    # --- R lookup -----------------------------------------------------------
    R = lookup_r(r_type, basis)

    # --- site coefficients --------------------------------------------------
    if site_class == "F":
        # Site-specific: Fa/Fv must be supplied by the user.
        Fa = inputs.get("Fa_manual")
        Fv = inputs.get("Fv_manual")
        Fa = float(Fa) if Fa not in (None, "") else None
        Fv = float(Fv) if Fv not in (None, "") else None
        fa_info = {"value": Fa, "site_specific": True}
        fv_info = {"value": Fv, "site_specific": True}
        if Fa is None or Fv is None:
            raise ValueError("Site Class F requires manual Fa and Fv values.")
    else:
        fa_info = lookup_fa(site_class, Ss)
        fv_info = lookup_fv(site_class, S1)
        Fa = fa_info["value"]
        Fv = fv_info["value"]

    # --- spectral accelerations (g) ----------------------------------------
    SDS = (2.0 / 3.0) * Fa * Ss        # Eq. 3-6
    SD1 = (2.0 / 3.0) * Fv * S1        # Eq. 3-7
    T0 = SD1 / SDS if SDS > 0 else 0.0

    # --- design spectral acceleration Sa (g) -------------------------------
    if T <= T0:
        Sa = SDS
        branch = "plateau"          # Eq. 3-8
        branch_eq = "3-8"
    else:
        Sa = SD1 / T
        branch = "descending"       # Eq. 3-9
        branch_eq = "3-9"

    # --- seismic design force (kN) -----------------------------------------
    FE = (Sa / R) * W_eff * IFE * IMV          # Eq. 3-10 (horizontal)

    # --- vertical component -------------------------------------------------
    a_vert = 0.8 * Sa                          # g (faithful output)
    # Vertical force estimate WITHOUT the R reduction and WITHOUT IMV.
    FE_vert = 0.8 * Sa * W_eff * IFE

    # --- LaTeX report -------------------------------------------------------
    steps = _build_steps(
        tag=tag, Ss=Ss, S1=S1, T=T, W_kN=W_kN, wire_kN=wire_kN, W_eff=W_eff,
        site_class=site_class, basis=basis, r_type=r_type, R=R, IFE=IFE, IMV=IMV,
        fa_info=fa_info, fv_info=fv_info, Fa=Fa, Fv=Fv,
        SDS=SDS, SD1=SD1, T0=T0, Sa=Sa, branch=branch, branch_eq=branch_eq,
        FE=FE, a_vert=a_vert, FE_vert=FE_vert,
    )

    # --- figure data --------------------------------------------------------
    figures = _build_figures(SDS, SD1, T0, T, Sa, FE, FE_vert)

    return {
        "inputs_echo": {
            "tag": tag, "Ss": Ss, "S1": S1, "T": T, "W_kN": W_kN,
            "wire_kN": wire_kN, "W_eff": W_eff, "site_class": site_class,
            "design_basis": basis, "r_type": r_type, "R": R, "IFE": IFE,
            "IMV": IMV,
        },
        "fa": fa_info, "fv": fv_info,
        "spectral": {"SDS": SDS, "SD1": SD1, "T0": T0, "Sa": Sa,
                     "branch": branch, "branch_eq": branch_eq},
        "forces": {"FE_kN": FE, "a_vert_g": a_vert, "FE_vert_kN": FE_vert},
        "summary": {
            "SDS_g": SDS, "SD1_g": SD1, "Sa_g": Sa, "branch": branch,
            "FE_kN": FE, "FE_vert_kN": FE_vert, "a_vert_g": a_vert,
        },
        "notes": {
            "gravity_friction": "Gravity friction is not counted as resistance "
                                "to seismic forces.",
            "no_combination": "Earthquake is not combined with extreme wind or "
                              "ice.",
            "vertical": "Engineer to confirm vertical-force treatment (R does "
                        "not apply to vertical response).",
            "r_gt3": R_GT3_NOTE,
        },
        "steps": steps,
        "figures": figures,
    }


# ---------------------------------------------------------------------------
# LaTeX report builder
# ---------------------------------------------------------------------------
def _interp_line(label: str, info: Dict[str, Any], x_sym: str,
                 x_val: float) -> str:
    """One LaTeX line describing a Table 3-12 / 3-13 interpolation or clamp."""
    if info.get("clamped") == "low":
        return (label + r" = " + _f(info["value"], 3) + r"\quad(" + x_sym
                + r" = " + _f(x_val, 3) + r" \leq " + _f(info["x_lo"], 2)
                + r"\text{, clamped to end column})")
    if info.get("clamped") == "high":
        return (label + r" = " + _f(info["value"], 3) + r"\quad(" + x_sym
                + r" = " + _f(x_val, 3) + r" \geq " + _f(info["x_hi"], 2)
                + r"\text{, clamped to end column})")
    return (label + r" = " + _f(info["y_lo"], 3) + r" + \dfrac{" + _f(x_val, 3)
            + r" - " + _f(info["x_lo"], 2) + r"}{" + _f(info["x_hi"], 2)
            + r" - " + _f(info["x_lo"], 2) + r"}\,(" + _f(info["y_hi"], 3)
            + r" - " + _f(info["y_lo"], 3) + r") = " + _f(info["value"], 3))


def _build_steps(**v) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []

    # Step 1 -- inputs & lookups
    wire_line = (r"W_{eff} = W + 0.5\,W_{wire} = " + _f(v["W_kN"], 3) + r" + 0.5 \times "
                 + _f(v["wire_kN"], 3) + r" = " + _f(v["W_eff"], 3) + r"\ \text{kN}")
    steps.append({"n": 1, "title": "Inputs &amp; lookups", "lines": [
        r"S_s = " + _f(v["Ss"], 3) + r"\ g,\quad S_1 = " + _f(v["S1"], 3)
        + r"\ g,\quad T = " + _f(v["T"], 3) + r"\ \text{s}",
        r"\text{Site Class " + v["site_class"] + r";\quad R = " + _f(v["R"], 2)
        + r"\ (" + v["basis"] + r"\text{, " + v["r_type"] + r"})}",
        r"I_{FE} = " + _f(v["IFE"], 2) + r",\quad I_{MV} = " + _f(v["IMV"], 2),
        wire_line,
    ]})

    # Step 2 -- site coefficients
    if v["fa_info"].get("site_specific"):
        fa_line = r"F_a = " + _f(v["Fa"], 3) + r"\quad(\text{site-specific, Class F})"
        fv_line = r"F_v = " + _f(v["Fv"], 3) + r"\quad(\text{site-specific, Class F})"
    else:
        fa_line = _interp_line(r"F_a\ (\text{Table 3-12})", v["fa_info"], r"S_s", v["Ss"])
        fv_line = _interp_line(r"F_v\ (\text{Table 3-13})", v["fv_info"], r"S_1", v["S1"])
    steps.append({"n": 2, "title": "Site coefficients $F_a$, $F_v$",
                  "lines": [fa_line, fv_line]})

    # Step 3 -- spectral accelerations
    steps.append({"n": 3, "title": "Spectral accelerations $S_{DS}$, $S_{D1}$", "lines": [
        r"S_{DS} = \tfrac{2}{3}\,F_a\,S_s = \tfrac{2}{3}\times " + _f(v["Fa"], 3)
        + r" \times " + _f(v["Ss"], 3) + r" = " + _f(v["SDS"], 4) + r"\ g\quad(\text{Eq. 3-6})",
        r"S_{D1} = \tfrac{2}{3}\,F_v\,S_1 = \tfrac{2}{3}\times " + _f(v["Fv"], 3)
        + r" \times " + _f(v["S1"], 3) + r" = " + _f(v["SD1"], 4) + r"\ g\quad(\text{Eq. 3-7})",
    ]})

    # Step 4 -- design spectral acceleration
    if v["branch"] == "plateau":
        sa_line = (r"T = " + _f(v["T"], 3) + r" \leq T_0 = " + _f(v["T0"], 3)
                   + r" \Rightarrow S_a = S_{DS} = " + _f(v["Sa"], 4)
                   + r"\ g\quad(\text{Eq. 3-8})")
    else:
        sa_line = (r"T = " + _f(v["T"], 3) + r" > T_0 = " + _f(v["T0"], 3)
                   + r" \Rightarrow S_a = \dfrac{S_{D1}}{T} = \dfrac{" + _f(v["SD1"], 4)
                   + r"}{" + _f(v["T"], 3) + r"} = " + _f(v["Sa"], 4)
                   + r"\ g\quad(\text{Eq. 3-9})")
    steps.append({"n": 4, "title": "Design spectral acceleration $S_a$", "lines": [
        r"T_0 = \dfrac{S_{D1}}{S_{DS}} = \dfrac{" + _f(v["SD1"], 4) + r"}{"
        + _f(v["SDS"], 4) + r"} = " + _f(v["T0"], 4) + r"\ \text{s}",
        sa_line,
    ]})

    # Step 5 -- seismic design force
    steps.append({"n": 5, "title": "Seismic design force $F_E$ (Eq. 3-10)", "lines": [
        r"F_E = \dfrac{S_a}{R}\,W_{eff}\,I_{FE}\,I_{MV}",
        r"F_E = \dfrac{" + _f(v["Sa"], 4) + r"}{" + _f(v["R"], 2) + r"} \times "
        + _f(v["W_eff"], 3) + r" \times " + _f(v["IFE"], 2) + r" \times "
        + _f(v["IMV"], 2) + r" = " + _f(v["FE"], 3) + r"\ \text{kN}",
    ]})

    # Step 6 -- vertical component (informational)
    steps.append({"n": 6, "title": "Vertical component (informational)", "lines": [
        r"a_{vert} = 0.8\,S_a = 0.8 \times " + _f(v["Sa"], 4) + r" = "
        + _f(v["a_vert"], 4) + r"\ g\quad(\text{Sec 3.1.7})",
        r"F_{E,vert} = 0.8\,S_a\,W_{eff}\,I_{FE} = 0.8 \times " + _f(v["Sa"], 4)
        + r" \times " + _f(v["W_eff"], 3) + r" \times " + _f(v["IFE"], 2) + r" = "
        + _f(v["FE_vert"], 3) + r"\ \text{kN}",
        r"\text{(no } R \text{ reduction, no } I_{MV}\text{; engineer to confirm "
        r"vertical-force treatment)}",
    ]})

    return steps


# ---------------------------------------------------------------------------
# Plotly figure data builder
# ---------------------------------------------------------------------------
def _build_figures(SDS, SD1, T0, T, Sa, FE, FE_vert) -> Dict[str, Any]:
    # Design response spectrum: plateau to T0, then SD1/T.
    T_max = max(2.0, 2.0 * T0, 1.5 * T, 0.5)
    Ts: List[float] = []
    Sas: List[float] = []
    # plateau
    n_flat = 20
    for i in range(n_flat + 1):
        t = T0 * i / n_flat
        Ts.append(round(t, 4))
        Sas.append(round(SDS, 5))
    # descending branch
    n_desc = 80
    for i in range(1, n_desc + 1):
        t = T0 + (T_max - T0) * i / n_desc
        if t <= 0:
            continue
        Ts.append(round(t, 4))
        Sas.append(round(SD1 / t, 5))

    spectrum = {
        "T": Ts, "Sa": Sas, "T0": round(T0, 4), "SDS": round(SDS, 5),
        "SD1": round(SD1, 5), "struct_T": round(T, 4), "struct_Sa": round(Sa, 5),
    }
    force_bar = {
        "labels": ["Horizontal F_E", "Vertical est. F_E,vert"],
        "values": [round(FE, 3), round(FE_vert, 3)],
    }
    return {"spectrum": spectrum, "force_bar": force_bar}


# ---------------------------------------------------------------------------
# Worked-example presets (also surfaced in the UI)
# ---------------------------------------------------------------------------
PRESETS = {
    "S1": {
        "label": "Preset S1 — plateau branch (T ≤ T0)",
        "tag": "S1 plateau", "Ss": 1.5, "S1": 0.6, "T": 0.5, "W_kN": 10.0,
        "wire_kN": 0.0, "site_class": "D", "design_basis": "USD",
        "r_type": "Tubular pole", "IFE": 1.25, "IMV": 1.0,
    },
    "S2": {
        "label": "Preset S2 — descending branch (T > T0)",
        "tag": "S2 descending", "Ss": 1.5, "S1": 0.6, "T": 1.0, "W_kN": 10.0,
        "wire_kN": 0.0, "site_class": "D", "design_basis": "USD",
        "r_type": "Tubular pole", "IFE": 1.25, "IMV": 1.0,
    },
    "S3": {
        "label": "Preset S3 — interpolation check",
        "tag": "S3 interp", "Ss": 0.625, "S1": 0.25, "T": 0.3, "W_kN": 20.0,
        "wire_kN": 0.0, "site_class": "D", "design_basis": "ASD",
        "r_type": "Trussed tower", "IFE": 1.0, "IMV": 1.0,
    },
}
