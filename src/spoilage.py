"""Transparent spoilage-risk score (a heuristic, NOT a lab-validated model).

Risk grows with 'degree-hours' spent above the chamber's upper limit (and, more weakly, below its lower limit,
because chilling/freezing injury also matters). score = 100 * (1 - exp(-degree_hours / K)); K is an assumption in
config.SPOIL_K that should be calibrated with cold-storage owners / agronomists for each crop.
"""
from __future__ import annotations
import math

MAX_GAP_S = 600      # a gap longer than this between readings is treated as 10 minutes (sensor outage is reported separately)


def degree_hours(ts, temps, temp_max, temp_min, cold_weight=0.5) -> float:
    total = 0.0
    for i in range(1, len(ts)):
        dt_h = min(max(ts[i] - ts[i - 1], 0.0), MAX_GAP_S) / 3600.0
        t = temps[i]
        if t > temp_max:
            total += (t - temp_max) * dt_h
        elif t < temp_min:
            total += cold_weight * (temp_min - t) * dt_h
    return total


def spoilage_risk(ts, temps, temp_max, temp_min, k=6.0) -> dict:
    dh = degree_hours(ts, temps, temp_max, temp_min)
    score = 100.0 * (1.0 - math.exp(-dh / k)) if k > 0 else 0.0
    level = "LOW" if score < 20 else ("MODERATE" if score < 50 else "HIGH")
    return {"score": round(score, 1), "level": level, "degree_hours": round(dh, 2)}
