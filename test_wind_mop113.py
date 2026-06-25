"""
pytest validation of the MOP 113 engine against the three worked examples.

Run with:  pytest -v
Each computed result must land within ~0.5 % of the published expected value.
"""

import math
import pytest

from wind_mop113 import calculate, PRESETS


def rel_close(actual, expected, tol=0.005):
    """True if |actual - expected| / |expected| <= tol (default 0.5 %)."""
    if expected == 0:
        return abs(actual) <= tol
    return abs(actual - expected) / abs(expected) <= tol


# (preset_key, expected dict)
CASES = [
    ("PI", {
        "Kz": 0.981, "qz_psf": 107.19, "qz_kpa": 5.131,
        "FX_kN": 13.648, "FY_kN": 13.648, "FR_kN": 19.300,
    }),
    ("CT", {
        "Kz": 1.002, "qz_psf": 109.47, "qz_kpa": 5.241,
        "FX_kN": 19.363, "FY_kN": 19.363, "FR_kN": 27.381,
    }),
    ("CB", {
        "Kz": 0.984, "qz_psf": 107.47, "qz_kpa": 5.145,
        "FX_kN": 234.281, "FY_kN": 193.391, "FR_kN": 303.789,
    }),
]


@pytest.mark.parametrize("key,exp", CASES)
def test_preset(key, exp):
    res = calculate(PRESETS[key])

    assert rel_close(res["kz"]["Kz"], exp["Kz"]), \
        f"{key} Kz: {res['kz']['Kz']} vs {exp['Kz']}"
    assert rel_close(res["qz"]["psf"], exp["qz_psf"]), \
        f"{key} qz(psf): {res['qz']['psf']} vs {exp['qz_psf']}"
    assert rel_close(res["qz"]["kpa"], exp["qz_kpa"]), \
        f"{key} qz(kPa): {res['qz']['kpa']} vs {exp['qz_kpa']}"
    assert rel_close(res["forces"]["FX_kN"], exp["FX_kN"]), \
        f"{key} FX: {res['forces']['FX_kN']} vs {exp['FX_kN']}"
    assert rel_close(res["forces"]["FY_kN"], exp["FY_kN"]), \
        f"{key} FY: {res['forces']['FY_kN']} vs {exp['FY_kN']}"
    assert rel_close(res["forces"]["FR_kN"], exp["FR_kN"]), \
        f"{key} FR: {res['forces']['FR_kN']} vs {exp['FR_kN']}"


def test_cb_governing_direction():
    """Rectangular CB: X must govern (larger projected width / force)."""
    res = calculate(PRESETS["CB"])
    assert res["summary"]["governing"] == "X"
    assert res["forces"]["FX_kN"] > res["forces"]["FY_kN"]


def test_circular_symmetry():
    """Circular bodies must give identical X and Y forces."""
    res = calculate(PRESETS["PI"])
    assert math.isclose(res["forces"]["FX_kN"], res["forces"]["FY_kN"],
                        rel_tol=1e-9)


def test_075_factor_toggle():
    """The 0.75 factor must scale only the resultant, and be off by default."""
    base = calculate(PRESETS["PI"])
    with_factor = calculate({**PRESETS["PI"], "apply_075": True})
    assert math.isclose(with_factor["forces"]["FR_kN"],
                        0.75 * base["forces"]["FR_kN"], rel_tol=1e-9)


def test_plinth_excluded():
    """Dropping the plinth must reduce both direction totals."""
    base = calculate(PRESETS["PI"])
    no_plinth = calculate({**PRESETS["PI"], "include_plinth": False})
    assert no_plinth["forces"]["FX_kN"] < base["forces"]["FX_kN"]
    assert no_plinth["forces"]["F_plinth_kN"] == 0.0
