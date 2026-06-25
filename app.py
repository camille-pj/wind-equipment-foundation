"""
app.py -- Flask front controller for the MOP 113 wind-load calculator.

Serves the single-page Vue app and exposes the calculation API. All real work
is delegated to ``wind_mop113.calculate`` -- this file only validates input and
marshals JSON.
"""

from flask import Flask, render_template, request, jsonify

from wind_mop113 import (
    calculate, PRESETS, TABLE_3_1, TABLE_3_1_HEIGHTS, TABLE_3_2,
    TABLE_3_3, TABLE_3_4A, TABLE_3_4B, TABLE_3_9, Q_CONST,
)

app = Flask(__name__)


@app.route("/")
def index():
    """Render the SPA, injecting the embedded MOP 113 tables and presets."""
    tables = {
        "t31": {"heights": TABLE_3_1_HEIGHTS,
                "rows": {str(h): TABLE_3_1[h] for h in TABLE_3_1_HEIGHTS}},
        "t32": TABLE_3_2,
        "t33": TABLE_3_3,
        "t34a": [{"upper": u, **r} for u, r in TABLE_3_4A],
        "t34b": [{"upper": u, **r} for u, r in TABLE_3_4B],
        "t39": TABLE_3_9,
        "q_const": Q_CONST,
    }
    return render_template("index.html", tables=tables, presets=PRESETS)


@app.route("/api/calculate", methods=["POST"])
def api_calculate():
    """Validate the posted inputs and return the full calculation result."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    # Required numeric fields shared by both body shapes.
    required = ["V_kph", "H_mm", "IFW", "cf_body", "cf_plinth"]
    shape = str(data.get("shape", "circular")).lower()
    if shape == "circular":
        required.append("D_mm")
    else:
        required += ["WX_mm", "WY_mm"]

    missing, bad = [], []
    for key in required:
        val = data.get(key)
        if val is None or val == "":
            missing.append(key)
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            bad.append(key)
            continue
        if f <= 0:
            bad.append(key)

    if missing:
        return jsonify({"error": f"Missing required input(s): "
                                 f"{', '.join(missing)}"}), 400
    if bad:
        return jsonify({"error": f"Input(s) must be positive numbers: "
                                 f"{', '.join(bad)}"}), 400

    try:
        result = calculate(data)
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify({"error": f"Calculation failed: {exc}"}), 400

    return jsonify(result)


if __name__ == "__main__":
    print("=" * 70)
    print("  MOP 113 wind-load calculator running at  http://127.0.0.1:5000")
    print("=" * 70)
    app.run(debug=True, host="127.0.0.1", port=5000)
