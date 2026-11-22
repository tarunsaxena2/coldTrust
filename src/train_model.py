"""Train the IsolationForest on simulated NORMAL operation (no faults) for several chambers.

Run:  python -m src.train_model
"""
from __future__ import annotations
import joblib
import numpy as np
from sklearn.ensemble import IsolationForest
from src.simulator import ChamberSim
from src.anomaly import featurize, WIN, DOOR_RECENT
import config


def build_training_matrix(days: int = 6, seeds=(11, 12, 13), setpoints=(3.0, 3.5, 5.0)):
    X = []
    for seed, sp in zip(seeds, setpoints):
        sim = ChamberSim(chamber_id=seed, setpoint=sp, seed=seed)
        T, H, D, C = [], [], [], []
        since_door = 999
        warm = []
        for minute in range(days * 1440):
            r = sim.step(minute)
            T.append(r["temp_c"]); H.append(r["humidity_pct"]); D.append(r["door_open"]); C.append(int(r["compressor_on"]))
            since_door = 0 if r["door_open"] else since_door + 1
            if minute < 120:
                warm.append(r["temp_c"]); continue
            median = float(np.median(warm)); hum_mean = 90.0
            if len(T) >= WIN:
                X.append(featurize(T, H, D, C, median, hum_mean, since_door <= DOOR_RECENT))
    return np.array(X)


def train(save_path: str | None = None):
    X = build_training_matrix()
    clf = IsolationForest(n_estimators=50, contamination=0.003, random_state=42).fit(X)
    scores = clf.decision_function(X)
    model = {"clf": clf, "threshold": float(np.percentile(scores, 0.3))}
    joblib.dump(model, save_path or config.MODEL_PATH)
    return model, X.shape


def load_model(path: str | None = None):
    try:
        return joblib.load(path or config.MODEL_PATH)
    except Exception:
        return None


if __name__ == "__main__":
    m, shape = train()
    print(f"trained on {shape[0]} windows x {shape[1]} features; threshold={m['threshold']:.4f}; saved to {config.MODEL_PATH}")
