"""Anomaly engine: learns each chamber's normal pattern and raises Watch / Warning / Critical.

Pure Python (no DB), so it can be unit-tested and evaluated offline.

Signals used
  * learned baseline band (median and upper band from the chamber's own normal readings)
  * sustained excess over the band, with door-open context (a door opening is not a fault)
  * rate of change -> estimated minutes until the temperature limit is crossed (ETA)
  * door open too long, compressor duty cycle (optional current sensor)
  * IsolationForest score over short-window features (trained in src/train_model.py)
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass, field
import numpy as np

LEVELS = ["OK", "WATCH", "WARNING", "CRITICAL"]
RANK = {l: i for i, l in enumerate(LEVELS)}

WIN = 10          # slope window (minutes)
DUTY_WIN = 30
DOOR_RECENT = 8   # minutes after closing during which temperature is allowed to be higher
FEATURES = ["dev", "slope", "std", "hum_dev", "door", "door_recent", "duty"]


@dataclass
class Assessment:
    level: str = "OK"
    reasons: list = field(default_factory=list)     # [(code, params dict)]
    score: float = 0.0          # IsolationForest decision_function (negative = unusual)
    slope: float = 0.0          # degC / min
    eta_min: float | None = None
    baseline: float | None = None
    upper: float | None = None
    learned: bool = False


def featurize(temps, hums, doors, comps, median, hum_mean, door_recent):
    t = np.asarray(temps[-WIN:], dtype=float)
    x = np.arange(len(t))
    slope = float(np.polyfit(x, t, 1)[0]) if len(t) >= 3 else 0.0
    duty = float(np.mean(comps[-DUTY_WIN:])) if comps and comps[-1] is not None else 0.5
    return [float(t[-1] - median), slope, float(np.std(t)), float(hums[-1] - hum_mean),
            float(bool(doors[-1])), float(door_recent), duty]


class ChamberMonitor:
    def __init__(self, chamber: dict, model=None, lazy_ml: bool = False):
        self.chamber = chamber
        self.model = model                      # {"clf": IsolationForest, "threshold": float} or None
        # lazy_ml: only run the model when its score can change the decision (same alerts, faster
        # offline evaluation). The live system scores every reading so the dashboard can show it.
        self.lazy_ml = lazy_ml
        self.temps = deque(maxlen=240)
        self.hums = deque(maxlen=240)
        self.doors = deque(maxlen=240)
        self.comps = deque(maxlen=240)
        self.n = 0
        self.median = chamber["setpoint"]
        self.upper = chamber["setpoint"] + 0.6
        self.hum_mean = 90.0
        self.learned = False
        self._learn_buf = []
        self.door_streak = 0
        self.since_door = 999
        self.watch_run = 0
        self.warn_run = 0
        self.ml_run = 0
        self.calm = 0
        self.level = "OK"
        self.persist = 0
        self.comp_off_run = 0

    # ---- baseline learning ---------------------------------------------
    def _learn(self, temp, hum, door_ctx):
        if not door_ctx:
            self._learn_buf.append((temp, hum))
        if len(self._learn_buf) >= 120 and not self.learned:
            t = np.array([a for a, _ in self._learn_buf])
            h = np.array([b for _, b in self._learn_buf])
            self.median = float(np.median(t))
            self.upper = float(np.percentile(t, 98)) + 0.2
            self.hum_mean = float(np.mean(h))
            self.learned = True

    # ---- main update -----------------------------------------------------
    def update(self, r: dict) -> Assessment:
        temp, hum = r["temp_c"], r.get("humidity_pct", 90.0)
        door, comp = bool(r.get("door_open", False)), r.get("compressor_on")
        c = self.chamber
        self.n += 1

        self.door_streak = self.door_streak + 1 if door else 0
        self.since_door = 0 if door else self.since_door + 1
        door_ctx = door or self.since_door <= DOOR_RECENT
        self.temps.append(temp); self.hums.append(hum); self.doors.append(door)
        self.comps.append(None if comp is None else int(bool(comp)))
        self.comp_off_run = 0 if comp in (None, True) else self.comp_off_run + 1

        if not self.learned:
            self._learn(temp, hum, door_ctx)

        temps = list(self.temps)
        n = len(temps)
        slope = float(np.polyfit(range(min(WIN, n)), temps[-WIN:], 1)[0]) if n >= 5 else 0.0
        allowance = 0.7 if door_ctx else 0.0
        excess = temp - (self.upper + allowance)

        reasons, level = [], "OK"

        def raise_to(lv, code, **kw):
            nonlocal level
            if RANK[lv] > RANK[level]:
                level = lv
            reasons.append((code, kw))

        # sustained excess over the learned normal band
        # during a door event the counters are held (not reset) so a fault that starts right then is not forgotten
        if excess > 0.3:
            self.watch_run += 1
        elif not door_ctx:
            self.watch_run = 0
        if excess > 0.9:
            self.warn_run += 1
        elif not door_ctx:
            self.warn_run = 0
        if self.watch_run >= 3:
            raise_to("WATCH", "drift", temp=temp, slope=round(slope * 10, 2))
        # still rising after several minutes outside the normal band -> Warning
        if self.warn_run >= 2 or (self.watch_run >= 6 and slope >= 0.02) or self.watch_run >= 12:
            raise_to("WARNING", "high", temp=temp)

        # time-to-limit estimate
        eta = None
        if slope > 0.005 and temp < c["temp_max"]:
            eta = (c["temp_max"] - temp) / slope
        if temp >= c["temp_max"]:
            raise_to("CRITICAL", "limit", temp=temp, limit=c["temp_max"])
        elif eta is not None and eta <= 12 and slope >= 0.02 and self.watch_run >= 3:
            raise_to("CRITICAL", "eta", temp=temp, eta=int(max(eta, 1)))

        # door left open
        # (normal door events last 2-5 minutes, so alerts start only after that)
        if self.door_streak >= 18:
            raise_to("CRITICAL", "door", minutes=self.door_streak)
        elif self.door_streak >= 12:
            raise_to("WARNING", "door", minutes=self.door_streak)
        elif self.door_streak >= 8:
            raise_to("WATCH", "door", minutes=self.door_streak)

        # compressor-aware (optional current sensor): compressor idle although the chamber is already above
        # its normal band means the thermostat is not getting it to run - a strong fault signal even right
        # after a door event
        if comp is not None and self.comp_off_run >= 6 and temp > self.upper + 0.3:
            raise_to("WATCH", "drift", temp=temp, slope=round(slope * 10, 2))
            if self.comp_off_run >= 12 and temp > self.upper + 0.8:
                raise_to("WARNING", "high", temp=temp)

        # too cold
        if temp <= c["temp_min"]:
            raise_to("WARNING", "cold", temp=temp)

        # compressor-aware context (only when the optional current sensor is present)
        if level != "OK" and self.comp_off_run >= 15 and excess > 0:
            reasons.append(("comp_off", {"minutes": self.comp_off_run}))

        # IsolationForest on short-window features
        score = 0.0
        gate_open = (not door_ctx) and (temp - self.median) > 0.3
        if self.model is not None and n >= WIN and self.learned and (gate_open or not self.lazy_ml):
            f = featurize(temps, list(self.hums), list(self.doors), list(self.comps),
                          self.median, self.hum_mean, self.since_door <= DOOR_RECENT)
            score = float(self.model["clf"].decision_function([f])[0])
            unusual = score < self.model["threshold"] and (temp - self.median) > 0.3 and not door_ctx
            self.ml_run = self.ml_run + 1 if unusual else 0
            if self.ml_run >= 3:
                raise_to("WATCH", "ml", score=round(score, 3))

        # hysteresis: only step down after 5 calm readings in a row
        if RANK[level] >= RANK[self.level]:
            self.level, self.calm = level, 0
        else:
            self.calm += 1
            if self.calm >= 5:
                self.level, self.calm = level, 0
            else:
                level = self.level
        return Assessment(level=level, reasons=reasons, score=score, slope=slope, eta_min=eta,
                          baseline=self.median, upper=self.upper, learned=self.learned)
