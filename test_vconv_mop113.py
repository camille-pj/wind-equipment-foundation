"""
pytest validation of the NSCP -> MOP 113 basic-wind-speed conversion.

The headline check reproduces the attached project report's NSCP -> TIA-222-G
figure (75.83 m/s -> 64.27 m/s with LF = 1.6, I = 0.87), confirming the engine
implements the same equate-the-velocity-pressures method.
"""

import math
import pytest

from vconv_mop113 import convert_v, PRESETS


def rel_close(a, e, tol=0.005):
    return abs(a - e) / abs(e) <= tol


def test_reproduces_attached_report():
    # V = 273 kph = 75.83 m/s; LF = 1.6; I = 0.87  ->  64.27 m/s  (report value)
    r = convert_v(PRESETS["PDF_CHECK"])
    assert rel_close(r["inputs_echo"]["V_nscp_ms"], 75.833, tol=0.002)
    assert rel_close(r["result"]["V_mop_ms"], 64.27, tol=0.002)


def test_mop_50yr():
    # IFW = 1.0, LF = 1.6  ->  V_MOP = V_NSCP / sqrt(1.6)
    r = convert_v(PRESETS["MOP_50YR"])
    expected_ms = (273.0 / 3.6) / math.sqrt(1.6)
    assert rel_close(r["result"]["V_mop_ms"], expected_ms)
    assert rel_close(r["result"]["V_mop_kph"], expected_ms * 3.6)


def test_mop_100yr_lower_than_50yr():
    # Higher IFW -> lower equivalent speed (IFW sits inside the MOP pressure).
    v50 = convert_v(PRESETS["MOP_50YR"])["result"]["V_mop_kph"]
    v100 = convert_v(PRESETS["MOP_100YR"])["result"]["V_mop_kph"]
    assert v100 < v50
    # exact ratio = sqrt(1.00 / 1.15)
    assert rel_close(v100 / v50, math.sqrt(1.00 / 1.15))


def test_pressure_equivalence():
    # By construction the service-level NSCP pressure equals the MOP pressure.
    r = convert_v(PRESETS["MOP_100YR"])
    assert rel_close(r["result"]["q_nscp_service"], r["result"]["q_mop"], tol=1e-6)


def test_kzt_kd_carry_through():
    # Carrying Kd < 1 lowers the equivalent speed by sqrt(Kd).
    base = convert_v(PRESETS["MOP_50YR"])["result"]["V_mop_ms"]
    withkd = convert_v({**PRESETS["MOP_50YR"], "Kd": 0.95})["result"]["V_mop_ms"]
    assert rel_close(withkd / base, math.sqrt(0.95))
