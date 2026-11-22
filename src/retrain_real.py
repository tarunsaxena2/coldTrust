"""Retrain the IsolationForest on REAL readings stored by the API (run after 2-3 weeks of normal operation per chamber).

    python -m src.retrain_real --db coldtrust.db --out models/iforest_real.joblib [--chambers 1 2] [--min-hours 48]

Windows near a Warning/Critical/offline alert (60 min before to 120 min after) are left out so the model learns
NORMAL behaviour only. The default model file is not overwritten unless you pass --out with that path.
"""
from __future__ import annotations
import argparse
import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from src.anomaly import featurize, WIN, DOOR_RECENT
from src.db import Store

PRE_S, POST_S = 60 * 60, 120 * 60


def collect_features(store: Store, chamber_ids=None, min_hours: float = 48.0):
    X, used = [], {}
    for ch in store.query("SELECT id FROM chambers ORDER BY id"):
        cid = ch["id"]
        if chamber_ids and cid not in chamber_ids:
            continue
        rows = store.query("SELECT ts,temp,humidity,door,comp FROM readings WHERE chamber_id=? ORDER BY id", (cid,))
        if len(rows) < min_hours * 60 * 0.8:                       # allow some gaps
            used[cid] = f"skipped: only {len(rows)} readings (< {min_hours:g} h)"
            continue
        bad = [(a["ts"] - PRE_S, a["ts"] + POST_S) for a in store.query(
            "SELECT ts FROM alerts WHERE chamber_id=? AND level IN ('WARNING','CRITICAL')", (cid,))]
        warm = [r["temp"] for r in rows[:120]]
        median, hum_mean = float(np.median(warm)), float(np.mean([r["humidity"] for r in rows]))
        has_comp = sum(r["comp"] is not None for r in rows) > len(rows) / 2
        T, H, D, C, since_door, n = [], [], [], [], 999, 0
        for i, r in enumerate(rows):
            T.append(r["temp"]); H.append(r["humidity"]); D.append(bool(r["door"]))
            C.append((int(r["comp"]) if r["comp"] is not None else (C[-1] if C else 0)) if has_comp else None)
            since_door = 0 if r["door"] else since_door + 1
            if i < 120 or len(T) < WIN or any(lo <= r["ts"] <= hi for lo, hi in bad):
                continue
            X.append(featurize(T, H, D, C, median, hum_mean, since_door <= DOOR_RECENT)); n += 1
        used[cid] = f"{n} windows"
    return np.array(X), used


def retrain(store: Store, out_path: str, chamber_ids=None, min_hours: float = 48.0):
    X, used = collect_features(store, chamber_ids, min_hours)
    if len(X) < 500:
        raise SystemExit(f"Not enough normal data to train ({len(X)} windows). Details: {used}")
    clf = IsolationForest(n_estimators=100, contamination=0.003, random_state=42).fit(X)
    model = {"clf": clf, "threshold": float(np.percentile(clf.decision_function(X), 0.3))}
    joblib.dump(model, out_path)
    return model, X.shape, used


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default="models/iforest_real.joblib")
    ap.add_argument("--chambers", type=int, nargs="*")
    ap.add_argument("--min-hours", type=float, default=48.0)
    a = ap.parse_args()
    import config
    m, shape, used = retrain(Store(a.db or config.DB_PATH), a.out, a.chambers, a.min_hours)
    print(f"trained on {shape[0]} windows x {shape[1]} features; per chamber: {used}; saved to {a.out}")
    print("Use it with:  COLDTRUST_MODEL=" + a.out + " uvicorn api:app")
