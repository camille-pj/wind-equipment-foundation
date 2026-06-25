"""
app.py -- Flask front controller for the stacked MOP 113 wind-load calculator.

Serves the single-page Vue app and exposes the calculation API. All real work
is delegated to ``wind_mop113.calculate`` (SI-native) -- this file only
validates input and marshals JSON.
"""

from flask import Flask, render_template, request, jsonify

from wind_mop113 import (
    calculate, PRESETS, DUAL_CONSTANT_NOTE, Q_SI, Q_US,
    TABLE_3_1, TABLE_3_1_HEIGHTS, TABLE_3_2, TABLE_3_3,
    TABLE_3_4A, TABLE_3_4B, TABLE_3_7, TABLE_3_8_ROWS, TABLE_3_9,
    TABLE_3_10_ROWS,
)

app = Flask(__name__)


@app.route("/")
def index():
    """Render the SPA, injecting every embedded MOP 113 table + the presets."""
    tables = {
        "t31": {"heights": TABLE_3_1_HEIGHTS,
                "rows": {str(h): TABLE_3_1[h] for h in TABLE_3_1_HEIGHTS}},
        "t32": TABLE_3_2,
        "t33": TABLE_3_3,
        "t34a": [{"upper": u, **r} for u, r in TABLE_3_4A],
        "t34b": [{"upper": u, **r} for u, r in TABLE_3_4B],
        "t37": [{"upper": u, "c": c} for u, c in TABLE_3_7],
        "t38": TABLE_3_8_ROWS,
        "t39": TABLE_3_9,
        "t310": TABLE_3_10_ROWS,
        "q_si": Q_SI, "q_us": Q_US, "note": DUAL_CONSTANT_NOTE,
    }
    return render_template("index.html", tables=tables, presets=PRESETS)


def _num(val):
    """Coerce to float; return None if not a positive-ish number."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _validate(data):
    """Return an error string, or None if the payload is usable."""
    if not isinstance(data, dict):
        return "Request body must be a JSON object."
    for key in ("V_kph", "IFW"):
        if _num(data.get(key)) is None or _num(data.get(key)) <= 0:
            return f"Global '{key}' must be a positive number."
    elements = data.get("elements")
    if not isinstance(elements, list) or not elements:
        return "At least one stacked element is required."

    for i, el in enumerate(elements, 1):
        kind = el.get("kind")
        tag = el.get("label", f"element {i}")
        if kind == "equipment_circular":
            if _num(el.get("D_mm")) in (None,) or _num(el.get("D_mm")) <= 0:
                return f"{tag}: diameter D (mm) must be positive."
        elif kind == "equipment_rectangular":
            for k in ("WX_mm", "WY_mm"):
                if _num(el.get(k)) in (None,) or _num(el.get(k)) <= 0:
                    return f"{tag}: {k} must be positive."
        elif kind == "pedestal_plinth":
            for k in ("width_mm", "height_mm"):
                if _num(el.get(k)) in (None,) or _num(el.get(k)) <= 0:
                    return f"{tag}: plinth {k} must be positive."
        elif kind == "lattice_truss":
            if el.get("route", "A") == "A":
                for k in ("face_width_mm", "face_height_mm", "phi"):
                    if _num(el.get(k)) is None or _num(el.get(k)) <= 0:
                        return f"{tag}: Route A needs positive {k}."
                if _num(el.get("phi")) > 1.0:
                    return f"{tag}: solidity Φ must be ≤ 1.0."
            else:
                members = el.get("members", [])
                if not members:
                    return f"{tag}: Route B needs at least one member."
                for m in members:
                    for k in ("b_mm", "L_mm"):
                        if _num(m.get(k)) is None or _num(m.get(k)) <= 0:
                            return f"{tag}: member {k} must be positive."
        else:
            return f"{tag}: unknown element kind '{kind}'."
        # height model: need either z_tip_mm or L_mm (or kind-specific height)
    return None


@app.route("/api/calculate", methods=["POST"])
def api_calculate():
    """Validate the posted stacked inputs and return the full result."""
    data = request.get_json(silent=True)
    err = _validate(data)
    if err:
        return jsonify({"error": err}), 400
    try:
        result = calculate(data)
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({"error": f"Calculation failed: {exc}"}), 400
    return jsonify(result)


if __name__ == "__main__":
    print("=" * 74)
    print("  MOP 113 STACKED wind-load calculator (SI) -> http://127.0.0.1:5000")
    print("=" * 74)
    app.run(debug=True, host="127.0.0.1", port=5000)
