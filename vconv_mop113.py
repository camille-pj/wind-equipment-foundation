"""
vconv_mop113.py
===============

Pure-Python engine that converts an **NSCP 2015 basic wind speed** to the
equivalent **ASCE MOP 113 (2007)** basic wind speed, using the same
"equate-the-velocity-pressures" method shown in the attached project report
(which did NSCP -> TIA-222-G).

Why a conversion is needed
--------------------------
* NSCP 2015 (= ASCE 7-10/-16 framework) tabulates a **strength-level
  (ultimate) 3-s gust** basic wind speed.  Its velocity pressure is

        q_z = 0.613 * K_z * K_zt * K_d * V_NSCP^2          (NSCP 207B.3-1)

* ASCE MOP 113 (2007) (= ASCE 7-05 era) uses a **nominal / service-level**
  3-s gust speed and carries the importance factor inside the pressure, with
  no topographic (K_zt) or directionality (K_d) factor:

        q_z = Q * K_z * V_MOP^2 * I_FW       (Q = 0.613, MOP Eq. 3-1 term)

Equate the two design velocity pressures.  The NSCP pressure is at strength
level, so divide it by the LRFD wind load factor (LF = 1.6) to reach the
service level MOP works at:

        Q * K_z * V_MOP^2 * I_FW = (0.613 * K_z * K_zt * K_d * V_NSCP^2) / LF

The common 0.613 and site K_z cancel, giving

        V_MOP = V_NSCP * sqrt( K_zt * K_d / (LF * I_FW) )

With the MOP defaults K_zt = K_d = 1.0 this reduces to the same shape as the
attached report:

        V_MOP = V_NSCP / sqrt( LF * I_FW )

Faithfulness check: feeding LF = 1.6 and I = 0.87 reproduces the report's
NSCP->TIA result (75.83 m/s -> 64.27 m/s); see test_vconv_mop113.py.

Every factor is an explicit, editable input so the conversion can be audited
and adjusted against the governing project reference.  No Flask/Plotly imports.
"""

from __future__ import annotations

import math
from typing import Dict, Any, List


KPH_TO_MS = 1.0 / 3.6
MS_TO_KPH = 3.6
MPH_PER_MS = 2.2369362921  # secondary US display only

# Importance factor I_FW (MOP 113 Table 3-3) -- reused for the dropdown.
IFW_TABLE = [
    {"label": "50-year MRI", "ifw": 1.00},
    {"label": "100-year MRI (critical facility)", "ifw": 1.15},
]

DEFAULT_LOAD_FACTOR = 1.6   # LRFD wind load factor (NSCP ultimate -> service)

METHOD_NOTE = (
    "NSCP 2015 gives a strength-level (ultimate) 3-s gust speed; ASCE MOP 113 "
    "(ASCE 7-05 era) uses a nominal/service speed with the importance factor "
    "inside its velocity pressure and no K_zt or K_d. Equating the two design "
    "velocity pressures (NSCP pressure /1.6 to reach service level) and "
    "cancelling the common 0.613·K_z gives "
    "V_MOP = V_NSCP·sqrt(K_zt·K_d/(LF·I_FW)). Verify the load factor, "
    "importance factor and any K_zt/K_d against the governing project reference."
)


def _f(x: float, n: int = 3) -> str:
    return f"{x:,.{n}f}"


def convert_v(inputs: Dict[str, Any]) -> Dict[str, Any]:
    """Convert an NSCP 2015 basic wind speed to the MOP 113 equivalent.

    ``inputs`` keys::

        tag           : str   (free label)
        V_nscp_kph    : float  NSCP 2015 strength-level basic wind speed (kph)
        load_factor   : float  LRFD wind load factor (default 1.6)
        IFW           : float  MOP importance factor (Table 3-3; default 1.0)
        Kzt           : float  topographic factor carried into the equate
                               (default 1.0 -- MOP Eq. 3-1 has none)
        Kd            : float  directionality factor carried in (default 1.0)
    """
    tag = str(inputs.get("tag", "Conversion"))
    V_nscp_kph = float(inputs["V_nscp_kph"])
    LF = float(inputs.get("load_factor", DEFAULT_LOAD_FACTOR))
    IFW = float(inputs.get("IFW", 1.0))
    Kzt = float(inputs.get("Kzt", 1.0))
    Kd = float(inputs.get("Kd", 1.0))

    V_nscp_ms = V_nscp_kph * KPH_TO_MS

    # V_MOP = V_NSCP * sqrt( Kzt * Kd / (LF * IFW) )
    ratio = math.sqrt((Kzt * Kd) / (LF * IFW))
    V_mop_ms = V_nscp_ms * ratio
    V_mop_kph = V_mop_ms * MS_TO_KPH

    # Velocity-pressure check (per unit Kz, flat-terrain, US/SI Q = 0.613).
    # NSCP strength pressure coefficient and the matched MOP pressure should be
    # equal once the /LF and IFW are applied -- shown for transparency.
    q_nscp_service = 0.613 * Kzt * Kd * V_nscp_ms ** 2 / LF   # per unit Kz
    q_mop = 0.613 * V_mop_ms ** 2 * IFW                       # per unit Kz

    steps = _build_steps(tag, V_nscp_kph, V_nscp_ms, LF, IFW, Kzt, Kd,
                         ratio, V_mop_ms, V_mop_kph, q_nscp_service, q_mop)

    figures = {
        "speed_bar": {
            "labels": ["NSCP 2015 (input)", "MOP 113 (equivalent)"],
            "kph": [round(V_nscp_kph, 2), round(V_mop_kph, 2)],
            "ms": [round(V_nscp_ms, 3), round(V_mop_ms, 3)],
        }
    }

    return {
        "inputs_echo": {
            "tag": tag, "V_nscp_kph": V_nscp_kph, "V_nscp_ms": V_nscp_ms,
            "load_factor": LF, "IFW": IFW, "Kzt": Kzt, "Kd": Kd,
        },
        "result": {
            "ratio": ratio, "V_mop_ms": V_mop_ms, "V_mop_kph": V_mop_kph,
            "V_mop_mph": V_mop_ms * MPH_PER_MS,
            "q_nscp_service": q_nscp_service, "q_mop": q_mop,
        },
        "summary": {
            "V_nscp_kph": V_nscp_kph, "V_nscp_ms": V_nscp_ms,
            "V_mop_kph": V_mop_kph, "V_mop_ms": V_mop_ms, "ratio": ratio,
        },
        "note": METHOD_NOTE,
        "steps": steps,
        "figures": figures,
    }


def _build_steps(tag, V_nscp_kph, V_nscp_ms, LF, IFW, Kzt, Kd, ratio,
                 V_mop_ms, V_mop_kph, q_nscp_service, q_mop) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []

    # Step 1 -- inputs
    steps.append({"n": 1, "title": "Inputs", "lines": [
        r"V_{NSCP} = " + _f(V_nscp_kph, 2) + r"\ \text{kph} \times \tfrac{1}{3.6} = "
        + _f(V_nscp_ms, 3) + r"\ \text{m/s}\quad(\text{NSCP 2015 strength-level 3-s gust})",
        r"\text{Load factor } LF = " + _f(LF, 2)
        + r"\ (\text{LRFD wind, NSCP ultimate}\to\text{service}),\quad I_{FW} = "
        + _f(IFW, 2) + r"\ (\text{Table 3-3})",
        r"K_{zt} = " + _f(Kzt, 2) + r",\quad K_d = " + _f(Kd, 2)
        + r"\quad(\text{MOP 113 Eq. 3-1 carries neither; defaults } 1.0)",
    ]})

    # Step 2 -- equate velocity pressures
    steps.append({"n": 2, "title": "Equate the design velocity pressures", "lines": [
        r"\text{NSCP 2015: } q_z = 0.613\,K_z K_{zt} K_d\,V_{NSCP}^2",
        r"\text{MOP 113: } q_z = 0.613\,K_z\,V_{MOP}^2\,I_{FW}",
        r"\text{Strength}\to\text{service: divide the NSCP pressure by } LF, "
        r"\text{ then equate and cancel } 0.613\,K_z:",
        r"V_{MOP}^2\,I_{FW} = \dfrac{K_{zt} K_d\,V_{NSCP}^2}{LF}",
    ]})

    # Step 3 -- solve for V_MOP
    steps.append({"n": 3, "title": "Equivalent MOP 113 basic wind speed", "lines": [
        r"V_{MOP} = V_{NSCP}\sqrt{\dfrac{K_{zt} K_d}{LF\,I_{FW}}}",
        r"V_{MOP} = " + _f(V_nscp_ms, 3) + r"\sqrt{\dfrac{" + _f(Kzt, 2)
        + r"\times " + _f(Kd, 2) + r"}{" + _f(LF, 2) + r"\times " + _f(IFW, 2)
        + r"}} = " + _f(V_nscp_ms, 3) + r"\times " + _f(ratio, 4) + r" = "
        + _f(V_mop_ms, 3) + r"\ \text{m/s} = " + _f(V_mop_kph, 2) + r"\ \text{kph}",
    ]})

    # Step 4 -- pressure check
    steps.append({"n": 4, "title": "Check (velocity pressure per unit $K_z$)", "lines": [
        r"\tfrac{1}{LF}\,0.613\,K_{zt}K_d\,V_{NSCP}^2 = " + _f(q_nscp_service, 2)
        + r"\ \text{Pa},\quad 0.613\,V_{MOP}^2\,I_{FW} = " + _f(q_mop, 2)
        + r"\ \text{Pa}\quad(\text{equal, as constructed})",
    ]})

    return steps


# ---------------------------------------------------------------------------
# Presets (also surfaced in the UI). Illustrative sample inputs only.
# ---------------------------------------------------------------------------
PRESETS = {
    "PDF_CHECK": {
        "label": "Report check — reproduces the attached NSCP→TIA 64.27 m/s",
        "tag": "PDF check (I=0.87)", "V_nscp_kph": 273.0, "load_factor": 1.6,
        "IFW": 0.87, "Kzt": 1.0, "Kd": 1.0,
    },
    "MOP_50YR": {
        "label": "NSCP→MOP, 50-year MRI (IFW = 1.00)",
        "tag": "MOP 50-yr", "V_nscp_kph": 273.0, "load_factor": 1.6,
        "IFW": 1.00, "Kzt": 1.0, "Kd": 1.0,
    },
    "MOP_100YR": {
        "label": "NSCP→MOP, 100-year MRI (IFW = 1.15)",
        "tag": "MOP 100-yr", "V_nscp_kph": 273.0, "load_factor": 1.6,
        "IFW": 1.15, "Kzt": 1.0, "Kd": 1.0,
    },
}
