"""
nscp2015_seismic.py
===================

NSCP 2015 (National Structural Code of the Philippines) seismic engine for the
Seismic tab.  The calculation classes are ported from APEC's ``apecseismicpy``
library (github.com/albertp16/seismicpy, MIT) -- specifically the nscp2015
submodules: site_coefficients, response_spectrum, baseshear, period, redundancy.

Chain (matches the seismicpy module flow):

    site coefficients (Na, Nv, Ca, Cv)
        -> structural period  T = Ct * hn^0.75
        -> design response spectrum  (sa_max = 2.5*Ca, Ts = Cv/sa_max)
        -> ADRS  (Sd = Sa*g*T^2/(4*pi^2)), optional ATC-40 reduction
        -> base shear  (Eq. 208-8 .. 208-11)
        -> redundancy factor  (rho, Sec. 208.5.6)

Pure Python -- no Flask / Plotly imports.  ``calculate_nscp(inputs) -> dict``
returns every intermediate, LaTeX report strings and Plotly figure data.

NOTE on governing base shear: seismicpy's ``governingShear`` only covered Zone 4
and treated Eq. 208-11 as an upper cap.  Per NSCP 208.5.2.1, 208-9 is the upper
limit and 208-10 / 208-11 are lower limits, so this port computes
    V_gov = max( min(V_208_8, V_208_9), V_208_10 [, V_208_11 if Zone 4] )
and reports all four equation values for audit.
"""

from __future__ import annotations

import math
from typing import Dict, Any, List, Optional


G = 9.81  # m/s^2


# ===========================================================================
# Site coefficients  (NSCP Tables 208-4 .. 208-8) -- ported from seismicpy
# ===========================================================================
NEAR_SOURCE = {
    "na": {
        2: {"A": 1.5, "B": 1.3, "C": 1.0},
        5: {"A": 1.2, "B": 1.0, "C": 1.0},
        10: {"A": 1.0, "B": 1.0, "C": 1.0},
    },
    "nv": {
        2: {"A": 2.0, "B": 1.6, "C": 1.0},
        5: {"A": 1.6, "B": 1.2, "C": 1.0},
        10: {"A": 1.2, "B": 1.0, "C": 1.0},
        15: {"A": 1.0, "B": 1.0, "C": 1.0},
    },
}

# Seismic coefficient tables (Ca uses the "ca" rows, Cv uses the "nv" rows).
SITE_COEFFICIENT = {
    "ca": {
        "sa": {2: 0.16, 4: 0.32},
        "sb": {2: 0.20, 4: 0.40},
        "sc": {2: 0.24, 4: 0.40},
        "sd": {2: 0.28, 4: 0.44},
        "se": {2: 0.34, 4: 0.44},
    },
    "nv": {
        "sa": {2: 0.16, 4: 0.32},
        "sb": {2: 0.20, 4: 0.40},
        "sc": {2: 0.32, 4: 0.56},
        "sd": {2: 0.40, 4: 0.64},
        "se": {2: 0.64, 4: 0.96},
    },
}

SOIL_LABELS = {
    "sa": "S_A — Hard rock", "sb": "S_B — Rock",
    "sc": "S_C — Very dense soil / soft rock",
    "sd": "S_D — Stiff soil", "se": "S_E — Soft soil",
}
SOURCE_LABELS = {"A": "Type A (M ≥ 7.0, high rate)",
                 "B": "Type B (intermediate)", "C": "Type C (M < 6.5, low rate)"}


class SiteCoefficients:
    """Na, Nv, Ca, Cv with near-source distance interpolation (ported)."""

    def __init__(self, distance, source_type, soil_type, zone):
        self.distance = float(distance)
        self.source_type = str(source_type).upper()
        self.soil_type = str(soil_type).lower()
        self.zone = int(zone)

    def interpolate(self, v1, v2, d1, d2):
        return v1 + (self.distance - d1) * (v2 - v1) / (d2 - d1)

    def get_near_source(self, factor):
        distances = sorted(NEAR_SOURCE[factor].keys())
        for i in range(len(distances) - 1):
            d1, d2 = distances[i], distances[i + 1]
            if self.distance <= d1:
                return NEAR_SOURCE[factor][d1][self.source_type]
            if d1 <= self.distance <= d2:
                v1 = NEAR_SOURCE[factor][d1][self.source_type]
                v2 = NEAR_SOURCE[factor][d2][self.source_type]
                return self.interpolate(v1, v2, d1, d2)
        return NEAR_SOURCE[factor][distances[-1]][self.source_type]

    def get_coefficient(self, factor):
        coef = SITE_COEFFICIENT[factor][self.soil_type][self.zone]
        if self.zone == 2:
            return coef, 1.0, coef
        ns = self.get_near_source("na" if factor == "ca" else "nv")
        return coef * ns, ns, coef

    def calculate(self):
        if self.distance < 0:
            raise ValueError("Distance must be non-negative.")
        na = self.get_near_source("na")
        nv = self.get_near_source("nv")
        ca, _, ca_base = self.get_coefficient("ca")
        cv, _, cv_base = self.get_coefficient("nv")
        return {"na": na, "nv": nv, "ca": ca, "cv": cv,
                "ca_base": ca_base, "cv_base": cv_base}


# ===========================================================================
# Structural period (NSCP 208.5.2.2, Method A) -- ported
# ===========================================================================
CT = {"concrete": 0.0731, "steel": 0.0853, "other": 0.0488}


def structural_period(structure_type, hn):
    ct = CT.get(str(structure_type).lower(), 0.0488)
    return ct * (hn ** 0.75), ct


def period_with_limit(structure_type, hn, zone):
    period, ct = structural_period(structure_type, hn)
    factor = 1.70 if int(zone) == 4 else 1.40
    return {"period": period, "ct": ct, "limit": period * factor,
            "limit_factor": factor}


# ===========================================================================
# Design response spectrum (NSCP Fig. 208-3) -- ported
# ===========================================================================
class ResponseSpectrum:
    def __init__(self, ca, cv):
        if ca <= 0 or cv <= 0:
            raise ValueError("Ca and Cv must be positive.")
        self.ca = ca
        self.cv = cv
        self.sa_max = 2.5 * ca
        self.Ts = cv / self.sa_max
        self.T0 = 0.2 * self.Ts

    def calculate(self, x_max=5.0, n_points=400):
        step = x_max / (n_points - 1)
        x = [i * step for i in range(n_points)]
        Sa = []
        for xi in x:
            if xi <= 0.2:
                Sa.append(self.ca + (self.sa_max - self.ca) * (xi / 0.2))
            elif xi <= 1.0:
                Sa.append(self.sa_max)
            else:
                Sa.append(self.sa_max / xi)
        return x, Sa

    def generate_adrs(self, x_max=5.0, n_points=400):
        x, Sa = self.calculate(x_max, n_points)
        T_actual = [xi * self.Ts for xi in x]
        Sd = [sa * G * t ** 2 / (4 * math.pi ** 2) for sa, t in zip(Sa, T_actual)]
        return Sd, Sa, T_actual

    def generate_reduced_adrs(self, SRA, SRV, x_max=5.0, n_points=400):
        step = x_max / (n_points - 1)
        x_vals = [i * step for i in range(n_points)]
        Ts_r = (SRV / SRA) * self.Ts
        sa_max_r = SRA * self.sa_max
        ca_r = SRA * self.ca
        Sa_r = []
        for xi in x_vals:
            T = xi * self.Ts
            if Ts_r > 0 and T <= 0.2 * Ts_r:
                Sa_r.append(ca_r + (sa_max_r - ca_r) * (T / (0.2 * Ts_r)))
            elif T <= Ts_r:
                Sa_r.append(sa_max_r)
            else:
                Sa_r.append(sa_max_r * Ts_r / T if T > 0 else 0)
        T_actual = [xi * self.Ts for xi in x_vals]
        Sd_r = [sa * G * t ** 2 / (4 * math.pi ** 2) for sa, t in zip(Sa_r, T_actual)]
        return Sd_r, Sa_r, T_actual


def atc40_reduction(dy, ay, dpi, api_val, structure_type):
    """ATC-40 effective damping + spectral reduction factors (ported)."""
    if api_val <= 0 or dpi <= 0:
        raise ValueError("dpi and api must be positive.")
    area_ratio = (ay * dpi - dy * api_val) / (api_val * dpi)
    beta_0 = 63.7 * area_ratio
    st = str(structure_type).upper()
    if st == "A":
        kappa = 1.0 if beta_0 <= 16.25 else 1.13 - 0.51 * area_ratio
    elif st == "B":
        kappa = 0.67 if beta_0 <= 16.25 else 0.845 - 0.446 * area_ratio
    elif st == "C":
        kappa = 0.33
    else:
        raise ValueError(f"Unknown structure type: {structure_type}")
    beta_eff = kappa * beta_0 + 5
    SRA = max((3.21 - 0.68 * math.log(beta_eff)) / 2.12, 0.33)
    SRV = max((2.31 - 0.41 * math.log(beta_eff)) / 1.65, 0.50)
    return {"beta_0": beta_0, "kappa": kappa, "beta_eff": beta_eff,
            "SRA": SRA, "SRV": SRV}


# ===========================================================================
# Base shear (NSCP Eq. 208-8 .. 208-11) -- ported (governing corrected)
# ===========================================================================
class BaseShear:
    def __init__(self, zone, nv, ca, cv, I, R, period, weight):
        self.zone = int(zone)
        self.nv = nv
        self.ca = ca
        self.cv = cv
        self.I = I
        self.R = R
        self.period = period
        self.weight = weight

    def eq_208_8(self):   # design value
        return (self.cv * self.I / (self.R * self.period)) * self.weight

    def eq_208_9(self):   # upper limit
        return (2.5 * self.ca * self.I / self.R) * self.weight

    def eq_208_10(self):  # lower limit
        return 0.11 * self.ca * self.I * self.weight

    def eq_208_11(self):  # Zone-4 additional lower limit (Z = 0.4)
        return (0.8 * 0.4 * self.nv * self.I / self.R) * self.weight

    def governing(self):
        v = min(self.eq_208_8(), self.eq_208_9())     # apply upper cap
        v = max(v, self.eq_208_10())                  # apply lower floor
        if self.zone == 4:
            v = max(v, self.eq_208_11())              # Zone-4 floor
        return v


# ===========================================================================
# Redundancy factor (NSCP Sec. 208.5.6) -- ported
# ===========================================================================
def redundancy(v_struc, v_element, ab, factor=1.25):
    if v_struc <= 0:
        raise ValueError("Structure story shear must be positive.")
    if ab <= 0:
        raise ValueError("Floor area must be positive.")
    r_max = v_element / v_struc
    rho_raw = 2.0 - (6.1 / (r_max * math.sqrt(ab)))
    rho = min(max(rho_raw, 1.0), factor)
    return {"r_max": r_max, "rho_raw": rho_raw, "rho": rho, "factor": factor}


# ---------------------------------------------------------------------------
# LaTeX formatting helper
# ---------------------------------------------------------------------------
def _f(x: float, n: int = 3) -> str:
    return f"{x:,.{n}f}"


# ===========================================================================
# Orchestrator
# ===========================================================================
def calculate_nscp(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Run the full NSCP 2015 seismic chain from a unified input set."""
    # -- site --
    distance = float(inputs.get("distance", 5.0))
    source_type = str(inputs.get("source_type", "B")).upper()
    soil_type = str(inputs.get("soil_type", "sd")).lower()
    zone = int(inputs.get("zone", 4))
    # -- period --
    structure_type = str(inputs.get("structure_type", "concrete")).lower()
    hn = float(inputs.get("hn", 15.0))
    # -- base shear --
    I = float(inputs.get("importance_factor", 1.0))
    R = float(inputs.get("response_modification", 8.0))
    weight = float(inputs.get("weight", 21000.0))
    # -- redundancy --
    v_struc = float(inputs.get("v_struc", 0) or 0)
    v_element = float(inputs.get("v_element", 0) or 0)
    ab = float(inputs.get("ab", 0) or 0)
    rho_factor = float(inputs.get("redundancy_factor", 1.25))
    # -- ADRS performance point (optional) --
    adrs_type = str(inputs.get("adrs_structure_type", "") or "").upper()
    dy = float(inputs.get("dy", 0) or 0)
    ay = float(inputs.get("ay", 0) or 0)
    dpi = float(inputs.get("dpi", 0) or 0)
    api_val = float(inputs.get("api_val", 0) or 0)

    # 1) Site coefficients
    site = SiteCoefficients(distance, source_type, soil_type, zone).calculate()
    ca, cv, na, nv = site["ca"], site["cv"], site["na"], site["nv"]

    # 2) Period
    per = period_with_limit(structure_type, hn, zone)
    T = per["period"]

    # 3) Response spectrum
    rs = ResponseSpectrum(ca, cv)
    x, Sa = rs.calculate(x_max=5.0)
    T_actual = [xi * rs.Ts for xi in x]
    Sa_14 = [1.4 * s for s in Sa]
    # spectral acceleration at the structure's period T
    if T <= rs.T0:
        Sa_T = ca + (rs.sa_max - ca) * (T / rs.T0) if rs.T0 > 0 else ca
    elif T <= rs.Ts:
        Sa_T = rs.sa_max
    else:
        Sa_T = rs.sa_max * rs.Ts / T

    # 4) ADRS
    Sd, Sa_adrs, _ = rs.generate_adrs(x_max=5.0)
    radial = _radial_lines(rs.Ts, Sd, Sa_adrs)
    reduction = None
    if adrs_type and dpi > 0 and api_val > 0:
        red = atc40_reduction(dy, ay, dpi, api_val, adrs_type)
        Sd_r, Sa_r, _ = rs.generate_reduced_adrs(red["SRA"], red["SRV"])
        # interpolate the reduced-spectrum acceleration at dpi
        apn = None
        for i in range(1, len(Sd_r)):
            if Sd_r[i] >= dpi:
                frac = ((dpi - Sd_r[i - 1]) / (Sd_r[i] - Sd_r[i - 1])
                        if Sd_r[i] != Sd_r[i - 1] else 0)
                apn = Sa_r[i - 1] + frac * (Sa_r[i] - Sa_r[i - 1])
                break
        reduction = {**red, "Sd_r": Sd_r, "Sa_r": Sa_r,
                     "dpi": dpi, "api": api_val, "apn": apn}

    # 5) Base shear
    bs = BaseShear(zone, nv, ca, cv, I, R, T, weight)
    base = {
        "v8": bs.eq_208_8(), "v9": bs.eq_208_9(),
        "v10": bs.eq_208_10(), "v11": bs.eq_208_11() if zone == 4 else None,
        "governing": bs.governing(),
    }

    # 6) Redundancy (only if inputs supplied)
    rho = None
    if v_struc > 0 and v_element > 0 and ab > 0:
        rho = redundancy(v_struc, v_element, ab, rho_factor)

    steps = _build_steps(distance, source_type, soil_type, zone, site,
                         structure_type, hn, per, rs, Sa_T, T, I, R, weight,
                         base, rho, reduction)

    figures = {
        "spectrum": {"T": [round(t, 4) for t in T_actual],
                     "Sa": [round(s, 5) for s in Sa],
                     "Sa_14": [round(s, 5) for s in Sa_14],
                     "T0": round(rs.T0, 4), "Ts": round(rs.Ts, 4),
                     "sa_max": round(rs.sa_max, 5), "ca": round(ca, 5),
                     "struct_T": round(T, 4), "struct_Sa": round(Sa_T, 5)},
        "adrs": {"Sd": [round(d, 6) for d in Sd],
                 "Sa": [round(s, 5) for s in Sa_adrs],
                 "radial": radial,
                 "reduced": ({"Sd": [round(d, 6) for d in reduction["Sd_r"]],
                              "Sa": [round(s, 5) for s in reduction["Sa_r"]],
                              "dpi": dpi, "api": api_val,
                              "apn": round(reduction["apn"], 5)
                              if reduction["apn"] is not None else None}
                             if reduction else None)},
    }

    return {
        "inputs_echo": {
            "distance": distance, "source_type": source_type,
            "soil_type": soil_type, "zone": zone,
            "structure_type": structure_type, "hn": hn,
            "importance_factor": I, "response_modification": R,
            "weight": weight,
        },
        "site": site,
        "period": {**per, "T": T},
        "spectrum": {"sa_max": rs.sa_max, "Ts": rs.Ts, "T0": rs.T0, "Sa_T": Sa_T},
        "base_shear": base,
        "redundancy": rho,
        "reduction": ({k: reduction[k] for k in
                       ("beta_0", "kappa", "beta_eff", "SRA", "SRV", "apn")}
                      if reduction else None),
        "summary": {
            "na": na, "nv": nv, "ca": ca, "cv": cv,
            "T": T, "sa_max": rs.sa_max, "Ts": rs.Ts, "Sa_T": Sa_T,
            "V_governing": base["governing"], "zone": zone,
            "rho": rho["rho"] if rho else None,
        },
        "steps": steps,
        "figures": figures,
    }


def _radial_lines(Ts, Sd, Sa):
    Sd_max = max(Sd) if Sd else 1.0
    Sa_max_chart = (max(Sa) if Sa else 1.0) * 1.1
    periods = sorted(set([round(Ts, 3)] + [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]))
    lines = []
    for t in periods:
        if t <= 0:
            continue
        slope = 4 * math.pi ** 2 / (G * t ** 2)
        sd_end = min(Sd_max, Sa_max_chart / slope)
        sa_end = slope * sd_end
        lines.append({"T": round(t, 3),
                      "x": [0, round(sd_end, 6)], "y": [0, round(sa_end, 6)]})
    return lines


# ---------------------------------------------------------------------------
# LaTeX report builder
# ---------------------------------------------------------------------------
def _build_steps(distance, source_type, soil_type, zone, site, structure_type,
                 hn, per, rs, Sa_T, T, I, R, weight, base, rho,
                 reduction) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []

    # 1 -- Site coefficients
    z4 = zone == 4
    site_lines = [
        r"\text{Zone } " + str(zone) + r",\ \text{soil } " + soil_type.upper()
        + r",\ \text{source } " + source_type + r",\ \text{distance } "
        + _f(distance, 1) + r"\ \text{km}",
    ]
    if z4:
        site_lines += [
            r"N_a = " + _f(site["na"], 3) + r",\quad N_v = " + _f(site["nv"], 3)
            + r"\quad(\text{near-source, Tables 208-5/208-6})",
            r"C_a = C_{a,table}\,N_a = " + _f(site["ca_base"], 3) + r" \times "
            + _f(site["na"], 3) + r" = " + _f(site["ca"], 4),
            r"C_v = C_{v,table}\,N_v = " + _f(site["cv_base"], 3) + r" \times "
            + _f(site["nv"], 3) + r" = " + _f(site["cv"], 4),
        ]
    else:
        site_lines += [
            r"C_a = " + _f(site["ca"], 4) + r",\quad C_v = " + _f(site["cv"], 4)
            + r"\quad(\text{Zone 2 — no near-source factor})",
        ]
    steps.append({"title": "Site coefficients (Tables 208-4 … 208-8)",
                  "lines": site_lines})

    # 2 -- Period
    steps.append({"title": "Structural period (Eq. 208-12, Method A)", "lines": [
        r"T = C_t\,h_n^{3/4} = " + _f(per["ct"], 4) + r" \times " + _f(hn, 2)
        + r"^{0.75} = " + _f(per["period"], 4) + r"\ \text{s}",
        r"\text{Upper limit } = " + _f(per["limit_factor"], 2) + r"\,T = "
        + _f(per["limit"], 4) + r"\ \text{s}\quad(\text{Zone " + str(zone)
        + r"; analytical } T \text{ not to exceed this})",
    ]})

    # 3 -- Response spectrum
    steps.append({"title": "Design response spectrum (Fig. 208-3)", "lines": [
        r"S_{a,max} = 2.5\,C_a = 2.5 \times " + _f(rs.ca, 4) + r" = "
        + _f(rs.sa_max, 4) + r"\ g",
        r"T_s = \dfrac{C_v}{2.5\,C_a} = \dfrac{" + _f(rs.cv, 4) + r"}{"
        + _f(rs.sa_max, 4) + r"} = " + _f(rs.Ts, 4) + r"\ \text{s},\quad "
        r"T_0 = 0.2\,T_s = " + _f(rs.T0, 4) + r"\ \text{s}",
        r"\text{At } T = " + _f(T, 4) + r"\ \text{s}: S_a = " + _f(Sa_T, 4)
        + r"\ g",
    ]})

    # 4 -- ADRS / ATC-40 (only if a reduction was run)
    if reduction:
        steps.append({"title": "ADRS — ATC-40 spectral reduction", "lines": [
            r"\beta_0 = " + _f(reduction["beta_0"], 2) + r"\%,\quad \kappa = "
            + _f(reduction["kappa"], 3) + r",\quad \beta_{eff} = "
            + _f(reduction["beta_eff"], 2) + r"\%",
            r"SR_A = " + _f(reduction["SRA"], 3) + r",\quad SR_V = "
            + _f(reduction["SRV"], 3) + (
                r",\quad a_{pn}(@d_{pi}) = " + _f(reduction["apn"], 4) + r"\ g"
                if reduction["apn"] is not None else ""),
        ]})

    # 5 -- Base shear
    bl = [
        r"V = \dfrac{C_v I}{R\,T}\,W = \dfrac{" + _f(rs.cv, 4) + r" \times "
        + _f(I, 2) + r"}{" + _f(R, 2) + r" \times " + _f(T, 4) + r"}\,W = "
        + _f(base["v8"], 2) + r"\ \text{kN}\quad(\text{208-8})",
        r"V_{max} = \dfrac{2.5\,C_a I}{R}\,W = " + _f(base["v9"], 2)
        + r"\ \text{kN}\quad(\text{208-9, upper})",
        r"V_{min} = 0.11\,C_a I\,W = " + _f(base["v10"], 2)
        + r"\ \text{kN}\quad(\text{208-10, lower})",
    ]
    if base["v11"] is not None:
        bl.append(r"V_{min,Z4} = \dfrac{0.8\,Z N_v I}{R}\,W = " + _f(base["v11"], 2)
                  + r"\ \text{kN}\quad(\text{208-11, Zone-4 lower, } Z=0.4)")
    bl.append(r"\Rightarrow V_{governing} = " + _f(base["governing"], 2)
              + r"\ \text{kN}\quad(W = " + _f(weight, 1) + r"\ \text{kN})")
    steps.append({"title": "Seismic base shear (Eq. 208-8 … 208-11)", "lines": bl})

    # 6 -- Redundancy
    if rho:
        steps.append({"title": "Redundancy factor ρ (Sec. 208.5.6)", "lines": [
            r"r_{max} = \dfrac{V_{element}}{V_{structure}} = " + _f(rho["r_max"], 4),
            r"\rho = 2 - \dfrac{6.1}{r_{max}\sqrt{A_B}} = " + _f(rho["rho_raw"], 4)
            + r"\;\Rightarrow\;\rho = " + _f(rho["rho"], 3)
            + r"\quad(\text{clamped to } [1.0,\ " + _f(rho["factor"], 2) + r"])",
        ]})

    return steps


# ---------------------------------------------------------------------------
# Presets (illustrative sample inputs only)
# ---------------------------------------------------------------------------
PRESETS = {
    "Z4_SMRF": {
        "label": "Zone 4 — concrete SMRF (sample)",
        "distance": 5.0, "source_type": "A", "soil_type": "sd", "zone": 4,
        "structure_type": "concrete", "hn": 15.0,
        "importance_factor": 1.0, "response_modification": 8.0, "weight": 21000.0,
        "v_struc": 500.0, "v_element": 120.0, "ab": 200.0, "redundancy_factor": 1.25,
        "adrs_structure_type": "", "dy": 0.0, "ay": 0.0, "dpi": 0.0, "api_val": 0.0,
    },
    "Z2_STEEL": {
        "label": "Zone 2 — steel frame (sample)",
        "distance": 10.0, "source_type": "B", "soil_type": "sc", "zone": 2,
        "structure_type": "steel", "hn": 24.0,
        "importance_factor": 1.0, "response_modification": 8.0, "weight": 30000.0,
        "v_struc": 600.0, "v_element": 150.0, "ab": 300.0, "redundancy_factor": 1.25,
        "adrs_structure_type": "", "dy": 0.0, "ay": 0.0, "dpi": 0.0, "api_val": 0.0,
    },
    "Z4_ADRS": {
        "label": "Zone 4 — with ATC-40 ADRS performance point (sample)",
        "distance": 2.0, "source_type": "A", "soil_type": "sd", "zone": 4,
        "structure_type": "concrete", "hn": 12.0,
        "importance_factor": 1.0, "response_modification": 8.0, "weight": 18000.0,
        "v_struc": 500.0, "v_element": 120.0, "ab": 200.0, "redundancy_factor": 1.25,
        "adrs_structure_type": "B", "dy": 0.05, "ay": 0.15, "dpi": 0.12, "api_val": 0.30,
    },
}
