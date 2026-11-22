"""Physics-style cold chamber simulator (stands in for the ESP32 sensor node).

One tick = one simulated minute. The compressor cycles with a thermostat (hysteresis),
the door opens now and then, and faults can be injected for the demo.
"""
from __future__ import annotations
import math
import random
from dataclasses import dataclass, field

SCENARIOS = ("compressor_trip", "power_cut", "door_left_open", "cooling_degradation")


@dataclass
class ChamberSim:
    chamber_id: int
    setpoint: float = 3.0
    band: float = 0.4
    seed: int = 0
    temp: float = field(init=False)
    comp_on: bool = field(init=False, default=False)
    door_left: int = field(init=False, default=0)       # minutes the door stays open (random events)
    scenario: str | None = field(init=False, default=None)
    scen_age: int = field(init=False, default=0)
    efficiency: float = field(init=False, default=1.0)
    humidity: float = field(init=False, default=90.0)

    def __post_init__(self):
        self.rng = random.Random(self.seed * 7919 + self.chamber_id)
        self.crng = random.Random(self.seed * 104729 + self.chamber_id)      # separate stream: current readings must not change the temperature randomness
        self.temp = self.setpoint + self.rng.uniform(-0.2, 0.2)

    # ---- demo controls -------------------------------------------------
    def set_scenario(self, name: str | None):
        if name in (None, "reset"):
            self.scenario, self.scen_age = None, 0
            self.efficiency, self.door_left = 1.0, 0
            return
        if name not in SCENARIOS:
            raise ValueError(f"unknown scenario {name}")
        self.scenario, self.scen_age = name, 0

    # ---- one simulated minute -----------------------------------------
    def step(self, minute_of_day: int) -> dict:
        hour = (minute_of_day / 60.0) % 24
        day_factor = max(0.0, math.sin(math.pi * (hour - 6) / 14)) if 6 <= hour <= 20 else 0.0
        leak = 0.035 + 0.02 * day_factor

        # random door events (more during the day)
        if self.scenario != "door_left_open" and self.door_left == 0:
            p = 1 / 100 if 6 <= hour <= 20 else 1 / 500
            if self.rng.random() < p:
                self.door_left = self.rng.randint(2, 5)
        door_open = self.door_left > 0
        if self.door_left > 0:
            self.door_left -= 1

        forced_off = False
        if self.scenario:
            self.scen_age += 1
            if self.scenario == "compressor_trip":
                forced_off = True
            elif self.scenario == "power_cut":
                forced_off = True
                leak += 0.03
            elif self.scenario == "door_left_open":
                door_open = True
                if self.scen_age > 45:          # someone finally closes it
                    self.set_scenario(None)
                    door_open = False
            elif self.scenario == "cooling_degradation":
                self.efficiency = max(0.10, 1.0 - 0.02 * self.scen_age)

        gain = leak + (0.14 if door_open else 0.0)
        if forced_off:
            self.comp_on = False
        else:
            if self.temp > self.setpoint + self.band:
                self.comp_on = True
            elif self.temp < self.setpoint - self.band:
                self.comp_on = False
        cool = 0.11 * self.efficiency if self.comp_on else 0.0

        self.temp += gain - cool + self.rng.gauss(0, 0.02)
        hum_target = 90.0 + (3.0 if not self.comp_on else 0.0) - (4.0 if door_open else 0.0)
        self.humidity += 0.3 * (hum_target - self.humidity) + self.rng.gauss(0, 0.25)
        return {
            "temp_c": round(self.temp + self.rng.gauss(0, 0.03), 2),   # sensor noise
            "humidity_pct": round(self.humidity, 1),
            "door_open": bool(door_open),
            "compressor_on": bool(self.comp_on),
            "compressor_current_a": round((6.5 + self.crng.gauss(0, 0.25)) if self.comp_on else abs(self.crng.gauss(0.1, 0.03)), 2),
        }
