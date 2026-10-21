"""Decree No. 43 of 2013 rent-increase caps, as plain code so the maths is never left to a model."""

from __future__ import annotations

from typing import Any


BANDS = [  # (lower bound of "% below average", upper bound, maximum increase %)
    (0, 10, 0),
    (11, 20, 5),
    (21, 30, 10),
    (31, 40, 15),
]


def allowed_increase(current_rent: float, average_rent: float, requested_increase_percent: float | None = None) -> dict[str, Any]:
    """Return the maximum allowed increase for a renewal, given the RERA index average for similar units."""
    if current_rent <= 0 or average_rent <= 0:
        raise ValueError("Rents must be positive numbers.")
    below = (average_rent - current_rent) / average_rent * 100
    if below <= 10:
        cap = 0
    elif below > 40:
        cap = 20
    else:
        # Decree bands use whole percentages ("11% to 20%"); round so 20.4% sits in the 11-20 band.
        rounded = round(below)
        cap = next(rate for low, high, rate in BANDS if low <= rounded <= high)
    result = {
        "percent_below_average": round(below, 1),
        "maximum_increase_percent": cap,
        "maximum_increase_aed": round(current_rent * cap / 100, 2),
        "maximum_new_rent_aed": round(current_rent * (1 + cap / 100), 2),
        "rule": "Decree No. 43 of 2013, Article 1 (01_rent_increase_rules.pdf)",
    }
    if requested_increase_percent is not None:
        result["requested_increase_percent"] = requested_increase_percent
        result["requested_within_cap"] = requested_increase_percent <= cap
    return result
