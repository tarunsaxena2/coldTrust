"""Offline evaluation: false alarms in normal operation and early-warning lead time vs a fixed-threshold alarm.

Run:  python evaluate.py        (writes outputs/evaluation.json and outputs/evaluation.png)
"""
from __future__ import annotations
import json
import numpy as np
import config
from src.simulator import ChamberSim, SCENARIOS
from src.anomaly import ChamberMonitor, RANK
from src.train_model import load_model

CH = {"id": 1, "name": "Chamber 1", "crop": "Potato", "setpoint": 3.0, "temp_max": 5.0, "temp_min": 1.0}


def warm_up(seed, model, chamber=CH, minutes=600):
    sim = ChamberSim(chamber["id"], chamber["setpoint"], seed=seed)
    mon = ChamberMonitor(chamber, model, lazy_ml=True)
    for m in range(minutes):
        mon.update(sim.step(m))
    return sim, mon, minutes


def false_alarms(model, days=7, seeds=range(6)):
    """Alerts raised (level >= WATCH, counted as new episodes) during normal operation."""
    episodes, chamber_days = 0, 0
    for seed in seeds:
        sim = ChamberSim(CH["id"], CH["setpoint"], seed=100 + seed)
        mon = ChamberMonitor(CH, model, lazy_ml=True)
        prev = "OK"
        for m in range(days * 1440):
            a = mon.update(sim.step(m))
            if m > 400:
                if prev == "OK" and a.level != "OK":
                    episodes += 1
                prev = a.level
        chamber_days += days
    return episodes, chamber_days


def lead_times(model, runs=12):
    out = {}
    for sc in SCENARIOS:
        rows = []
        for seed in range(runs):
            sim, mon, m0 = warm_up(200 + seed, model)
            sim.set_scenario(sc)
            t_fixed = t_watch = t_warn = t_crit = None
            for k in range(1, 400):
                r = sim.step(m0 + k)
                a = mon.update(r)
                if t_fixed is None and r["temp_c"] >= CH["temp_max"]:
                    t_fixed = k
                if t_watch is None and RANK[a.level] >= 1: t_watch = k
                if t_warn is None and RANK[a.level] >= 2: t_warn = k
                if t_crit is None and RANK[a.level] >= 3: t_crit = k
                if t_fixed is not None and t_crit is not None:
                    break
            rows.append({"fixed": t_fixed, "watch": t_watch, "warning": t_warn, "critical": t_crit})
        ok = [r for r in rows if r["fixed"] is not None and r["watch"] is not None and r["critical"] is not None]
        lead = lambda key: [r["fixed"] - r[key] for r in ok]
        out[sc] = {
            "runs": runs,
            "detected": sum(1 for r in rows if r["watch"] is not None),
            "fixed_threshold_minute": float(np.mean([r["fixed"] for r in ok])) if ok else None,
            "watch_lead_min": float(np.mean(lead("watch"))) if ok else None,
            "warning_lead_min": float(np.mean(lead("warning"))) if ok else None,
            "critical_lead_min": float(np.mean(lead("critical"))) if ok else None,
            "critical_lead_min_worst": float(np.min(lead("critical"))) if ok else None,
        }
    return out


def main():
    model = load_model()
    eps, cd = false_alarms(model)
    lt = lead_times(model)
    result = {"false_alarm_episodes": eps, "chamber_days": cd,
              "false_alarms_per_chamber_week": round(eps / cd * 7, 3), "scenarios": lt}
    config.OUTPUT_DIR.mkdir(exist_ok=True)
    (config.OUTPUT_DIR / "evaluation.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        names = list(lt)
        x = np.arange(len(names)); w = 0.27
        fig, ax = plt.subplots(figsize=(8, 4))
        for i, (key, lab, col) in enumerate([("watch_lead_min", "Watch", "#f4b400"),
                                             ("warning_lead_min", "Warning", "#f57c00"),
                                             ("critical_lead_min", "Critical", "#d32f2f")]):
            ax.bar(x + (i - 1) * w, [lt[n][key] or 0 for n in names], w, label=lab, color=col)
        ax.set_xticks(x); ax.set_xticklabels([n.replace("_", "\n") for n in names])
        ax.set_ylabel("Minutes earlier than fixed 5.0 C alarm"); ax.set_title("ColdTrust early-warning lead time (simulated)")
        ax.legend(); fig.tight_layout(); fig.savefig(config.OUTPUT_DIR / "evaluation.png", dpi=130)
    except Exception as e:  # plotting is optional
        print("plot skipped:", e)


if __name__ == "__main__":
    main()
