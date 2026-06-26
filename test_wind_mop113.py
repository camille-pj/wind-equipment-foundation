"""
pytest validation of the SI-native stacked MOP 113 engine.

Run with:  pytest -v

Presets 1-3 are single-equipment + plinth stacks taken from the project sheets;
their published targets were derived in US units, so the SI-native engine lands
~0.1 % off -- still well inside the 0.5 % tolerance.  Preset 4 is a two-element
stacking sanity check (PI on a 2 m lattice support).
"""

import math
import pytest

from wind_mop113 import calculate, PRESETS, truss_cf_solidity, aspect_ratio_c


def rel_close(actual, expected, tol=0.005):
    if expected == 0:
        return abs(actual) <= tol
    return abs(actual - expected) / abs(expected) <= tol


# ---- single-element regression presets (1-3) ----------------------------
SINGLE_CASES = [
    ("PI", {"Kz": 0.981, "qz_kpa": 5.13, "FX_kN": 13.65, "FY_kN": 13.65, "FR_kN": 19.30}),
    ("CT", {"Kz": 1.002, "qz_kpa": 5.24, "FX_kN": 19.36, "FY_kN": 19.36, "FR_kN": 27.38}),
    ("CB", {"Kz": 0.984, "qz_kpa": 5.14, "FX_kN": 234.3, "FY_kN": 193.4, "FR_kN": 303.8}),
]


@pytest.mark.parametrize("key,exp", SINGLE_CASES)
def test_single_presets(key, exp):
    res = calculate(PRESETS[key])
    eq = res["elements"][0]            # the equipment element

    assert rel_close(eq["Kz"], exp["Kz"]), f"{key} Kz {eq['Kz']} vs {exp['Kz']}"
    assert rel_close(eq["qz_kpa"], exp["qz_kpa"]), \
        f"{key} qz {eq['qz_kpa']} vs {exp['qz_kpa']}"
    assert rel_close(res["summary"]["FX_kN"], exp["FX_kN"]), \
        f"{key} FX {res['summary']['FX_kN']} vs {exp['FX_kN']}"
    assert rel_close(res["summary"]["FY_kN"], exp["FY_kN"]), \
        f"{key} FY {res['summary']['FY_kN']} vs {exp['FY_kN']}"
    assert rel_close(res["summary"]["FR_kN"], exp["FR_kN"]), \
        f"{key} FR {res['summary']['FR_kN']} vs {exp['FR_kN']}"


def test_cb_governing():
    res = calculate(PRESETS["CB"])
    assert res["summary"]["governing"] == "X"
    assert res["summary"]["FX_kN"] > res["summary"]["FY_kN"]


# ---- two-element stacked sanity preset (4) ------------------------------
def test_stacked_preset():
    res = calculate(PRESETS["PI_STACK"])
    eq, sup = res["elements"][0], res["elements"][1]

    # equipment seated at 2 m, tip at 9.189 m -> L = 7.189 m
    assert rel_close(eq["L_m"], 7.189)
    assert rel_close(eq["Kz"], 0.981)
    assert rel_close(eq["qz_kpa"], 5.13)
    assert rel_close(eq["Fx"], 9.73, tol=0.02)

    # support: short -> Kz on the 0-15 floor; Cf = 4.1 - 5.2*0.20 = 3.06
    assert rel_close(sup["Kz"], 0.85)
    assert rel_close(sup["qz_kpa"], 4.44, tol=0.01)
    assert rel_close(sup["cf"], 3.06)
    assert rel_close(sup["extra"]["A_solid"], 0.20)
    assert rel_close(sup["Fx"], 2.31, tol=0.02)

    # assembly
    assert rel_close(res["summary"]["FX_kN"], 12.0, tol=0.02)
    assert rel_close(res["summary"]["FR_kN"], 17.0, tol=0.02)
    assert rel_close(res["summary"]["M_gov_kNm"], 57.0, tol=0.03)


# ---- table-logic unit tests ---------------------------------------------
def test_table_3_8_branches():
    # low-solidity square -> 4.1 - 5.2*phi branch
    assert math.isclose(truss_cf_solidity(0.20, "square", "flat")["cf"],
                        4.1 - 5.2 * 0.20, rel_tol=1e-9)
    # round members apply Cc (Table 3-10)
    r = truss_cf_solidity(0.20, "square", "round")
    assert r["cc"] == 0.67 and math.isclose(r["cf"], (4.1 - 5.2 * 0.20) * 0.67)
    # high-solidity square -> 1.3 - 0.7*phi branch
    assert math.isclose(truss_cf_solidity(0.90, "square", "flat")["cf"],
                        1.3 - 0.7 * 0.90, rel_tol=1e-9)


def test_table_3_7_aspect_ratio():
    assert aspect_ratio_c(3)["c"] == 0.6
    assert aspect_ratio_c(6)["c"] == 0.7
    assert aspect_ratio_c(20)["c"] == 0.8
    assert aspect_ratio_c(50)["c"] == 1.0


def test_075_toggle():
    base = calculate(PRESETS["PI"])
    factored = calculate({**PRESETS["PI"], "apply_075": True})
    assert math.isclose(factored["summary"]["FR_kN"],
                        0.75 * base["summary"]["FR_kN"], rel_tol=1e-9)


def test_solidity_takeoff_matches_direct():
    """Φ computed from a member take-off must equal the direct-Φ result when the
    take-off solid area divided by Ag gives the same Φ."""
    # Ag = 0.5 x 2.0 = 1.0 m^2; take-off solid = 0.1*2.0*1 = 0.2 m^2 -> Φ = 0.20
    takeoff = {
        **PRESETS["PI_STACK"],
        "elements": [
            PRESETS["PI_STACK"]["elements"][0],
            {**PRESETS["PI_STACK"]["elements"][1],
             "phi_mode": "takeoff",
             "phi_members": [{"b_mm": 100, "L_mm": 2000, "n": 1}]},
        ],
    }
    direct = calculate(PRESETS["PI_STACK"])["elements"][1]
    to = calculate(takeoff)["elements"][1]
    assert abs(to["extra"]["phi"] - 0.20) < 1e-9
    assert abs(to["extra"]["solid_area"] - 0.20) < 1e-9
    assert math.isclose(to["Fx"], direct["Fx"], rel_tol=1e-9)


def test_centroid_basis_lowers_kz():
    """Centroid basis evaluates Kz lower in the profile -> Kz <= tip basis."""
    tip = calculate(PRESETS["PI"])["elements"][0]["Kz"]
    centroid_inputs = {**PRESETS["PI"]}
    centroid_inputs["elements"] = [
        {**PRESETS["PI"]["elements"][0], "kz_basis": "centroid"},
        PRESETS["PI"]["elements"][1],
    ]
    centroid = calculate(centroid_inputs)["elements"][0]["Kz"]
    assert centroid <= tip
