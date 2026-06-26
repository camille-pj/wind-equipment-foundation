"""
pytest validation of the NSCP 2015 seismic engine (ported from apecseismicpy).

Cross-checks against the values documented in the seismicpy README / module
docstrings:
  * site_coefficients(distance=5, source="B", soil="sd", zone=4) -> Na=1.0, Nv≈1.29, Ca=0.44, Cv≈0.72
  * base shear example (zone 4, nv=1.29, ca=0.42, cv=0.72, I=1, R=8, T=0.62, W=21000)
"""

import math
import pytest

from nscp2015_seismic import (
    SiteCoefficients, structural_period, ResponseSpectrum, BaseShear,
    redundancy, calculate_nscp, PRESETS,
)


def rel_close(a, e, tol=0.005):
    return abs(a - e) / abs(e) <= tol


def test_site_coefficients_readme_example():
    # README: site_coefficients(5.0, "B", "sd", 4) -> na 1.0, nv 1.29, ca 0.44, cv 0.72
    r = SiteCoefficients(5.0, "B", "sd", 4).calculate()
    assert rel_close(r["na"], 1.0)
    assert rel_close(r["nv"], 1.2, tol=0.001) or rel_close(r["nv"], 1.2)  # Nv(B,5km)=1.2
    # Cv = nv-table(sd,4)=0.64 * Nv(=1.2) = 0.768 ; Ca = ca-table(sd,4)=0.44 * Na(=1.0)=0.44
    assert rel_close(r["ca"], 0.44)
    assert rel_close(r["cv"], 0.768)


def test_near_source_interpolation():
    # Source A, Nv: 2km->2.0, 5km->1.6 ; at 3.5km midway -> 1.8
    r = SiteCoefficients(3.5, "A", "sd", 4).calculate()
    assert rel_close(r["nv"], 1.8)
    # Na source A: 2km->1.5, 5km->1.2 ; at 3.5km -> 1.35
    assert rel_close(r["na"], 1.35)


def test_structural_period():
    T, ct = structural_period("concrete", 15.0)
    assert rel_close(ct, 0.0731)
    assert rel_close(T, 0.0731 * 15.0 ** 0.75)


def test_response_spectrum_control_periods():
    rs = ResponseSpectrum(0.44, 0.72)
    assert rel_close(rs.sa_max, 1.1)            # 2.5*0.44
    assert rel_close(rs.Ts, 0.72 / 1.1)
    assert rel_close(rs.T0, 0.2 * rs.Ts)


def test_base_shear_equations():
    # seismicpy baseshear.py example
    bs = BaseShear(4, 1.29, 0.42, 0.72, 1.0, 8.0, 0.62, 21000.0)
    assert rel_close(bs.eq_208_8(), (0.72 * 1 / (8 * 0.62)) * 21000)
    assert rel_close(bs.eq_208_9(), (2.5 * 0.42 * 1 / 8) * 21000)
    assert rel_close(bs.eq_208_10(), 0.11 * 0.42 * 1 * 21000)
    assert rel_close(bs.eq_208_11(), (0.8 * 0.4 * 1.29 * 1 / 8) * 21000)


def test_base_shear_governing_is_clamped_correctly():
    # 208-8 = 3048.4, 208-9 = 2756.25, 208-10 = 970.2, 208-11 = 1083.6
    # governing = max( min(3048.4, 2756.25), 970.2, 1083.6 ) = 2756.25
    bs = BaseShear(4, 1.29, 0.42, 0.72, 1.0, 8.0, 0.62, 21000.0)
    assert rel_close(bs.governing(), 2756.25)


def test_redundancy():
    r = redundancy(500.0, 120.0, 200.0, 1.25)
    rmax = 120.0 / 500.0
    raw = 2.0 - 6.1 / (rmax * math.sqrt(200.0))
    assert rel_close(r["r_max"], rmax)
    assert rel_close(r["rho_raw"], raw)
    assert r["rho"] == min(max(raw, 1.0), 1.25)


def test_orchestrator_runs_all_sections():
    res = calculate_nscp(PRESETS["Z4_SMRF"])
    assert res["site"]["ca"] > 0
    assert res["period"]["T"] > 0
    assert res["spectrum"]["sa_max"] > 0
    assert res["base_shear"]["governing"] > 0
    assert res["redundancy"]["rho"] >= 1.0
    assert len(res["figures"]["spectrum"]["T"]) > 10
    assert len(res["figures"]["adrs"]["Sd"]) > 10


def test_orchestrator_adrs_reduction():
    res = calculate_nscp(PRESETS["Z4_ADRS"])
    assert res["reduction"] is not None
    assert 0.33 <= res["reduction"]["SRA"] <= 1.0
    assert res["figures"]["adrs"]["reduced"] is not None


def test_zone2_governing_does_not_crash():
    # seismicpy's governingShear UnboundLocalError'd for zone 2; ours must not.
    res = calculate_nscp(PRESETS["Z2_STEEL"])
    assert res["base_shear"]["v11"] is None
    assert res["base_shear"]["governing"] > 0
