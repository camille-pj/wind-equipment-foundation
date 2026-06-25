"""
pytest validation of the MOP 113 §3.1.7 seismic engine.

Run with:  pytest test_seismic_mop113.py -v

The three presets are illustrative sanity checks (not from a specific project)
that lock the interpolation, the Sa-branch selection and Eq. 3-10.

NOTE on the vertical force: the methodology defines
    F_E,vert = 0.8 * Sa * W_eff * I_FE      (no R reduction, no I_MV)
which for Preset S1 gives 0.8*1.0*10*1.25 = 10.0 kN.  The prompt's preset text
listed 6.667 kN, but that equals 0.8*F_E (i.e. it carries the R reduction) --
the exact thing the methodology says NOT to do.  We follow the methodology and
assert 10.0 kN here; the horizontal F_E values are the primary validation.
"""

import math
import pytest

from seismic_mop113 import (
    calculate_seismic, PRESETS, lookup_fa, lookup_fv, lookup_r, interp_clamped,
)


def rel_close(a, e, tol=0.005):
    if e == 0:
        return abs(a) <= tol
    return abs(a - e) / abs(e) <= tol


def test_preset_S1_plateau():
    r = calculate_seismic(PRESETS["S1"])
    assert rel_close(r["fa"]["value"], 1.0)
    assert rel_close(r["fv"]["value"], 1.5)
    assert rel_close(r["spectral"]["SDS"], 1.000)
    assert rel_close(r["spectral"]["SD1"], 0.600)
    assert rel_close(r["spectral"]["T0"], 0.600)
    assert r["spectral"]["branch"] == "plateau"
    assert rel_close(r["spectral"]["Sa"], 1.000)
    assert rel_close(r["forces"]["FE_kN"], 8.333)
    assert rel_close(r["forces"]["a_vert_g"], 0.800)
    # methodology: 0.8 * Sa * W_eff * IFE  (no R) = 0.8*1.0*10*1.25 = 10.0
    assert rel_close(r["forces"]["FE_vert_kN"], 10.0)


def test_preset_S2_descending():
    r = calculate_seismic(PRESETS["S2"])
    assert r["spectral"]["branch"] == "descending"
    assert rel_close(r["spectral"]["Sa"], 0.600)
    assert rel_close(r["forces"]["FE_kN"], 5.000)
    assert rel_close(r["forces"]["a_vert_g"], 0.480)


def test_preset_S3_interpolation():
    r = calculate_seismic(PRESETS["S3"])
    assert rel_close(r["fa"]["value"], 1.30)      # interp at Ss = 0.625
    assert rel_close(r["fv"]["value"], 1.90)      # interp at S1 = 0.25
    assert rel_close(r["spectral"]["SDS"], 0.5417)
    assert rel_close(r["spectral"]["SD1"], 0.3167)
    assert rel_close(r["spectral"]["T0"], 0.5846)
    assert r["spectral"]["branch"] == "plateau"
    assert rel_close(r["forces"]["FE_kN"], 2.708)


# ---- table-logic unit tests ---------------------------------------------
def test_fa_clamping():
    # Ss above 1.25 clamps to the end column.
    assert lookup_fa("D", 1.5)["value"] == 1.0
    assert lookup_fa("D", 1.5)["clamped"] == "high"
    # Ss below 0.25 clamps low.
    assert lookup_fa("E", 0.1)["value"] == 2.5
    assert lookup_fa("E", 0.1)["clamped"] == "low"


def test_fv_interpolation_midpoint():
    # Site D, S1 = 0.25 -> midway between 2.0 (0.2) and 1.8 (0.3) = 1.9
    assert math.isclose(lookup_fv("D", 0.25)["value"], 1.9, rel_tol=1e-9)


def test_r_usd_vs_asd():
    assert lookup_r("Trussed tower", "USD") == 3.0
    assert lookup_r("Trussed tower", "ASD") == 4.0
    assert lookup_r("Station post insulators", "USD") == 1.0


def test_wire_weight_adds_half():
    base = calculate_seismic(PRESETS["S1"])
    withwire = calculate_seismic({**PRESETS["S1"], "wire_kN": 4.0})
    # W_eff goes 10 -> 12, so FE scales by 12/10.
    assert rel_close(withwire["inputs_echo"]["W_eff"], 12.0)
    assert rel_close(withwire["forces"]["FE_kN"], base["forces"]["FE_kN"] * 1.2)


def test_site_F_requires_manual():
    with pytest.raises(ValueError):
        calculate_seismic({**PRESETS["S1"], "site_class": "F"})
    # supplying manual Fa/Fv works
    r = calculate_seismic({**PRESETS["S1"], "site_class": "F",
                           "Fa_manual": 1.2, "Fv_manual": 1.8})
    assert rel_close(r["fa"]["value"], 1.2)
    assert rel_close(r["fv"]["value"], 1.8)
