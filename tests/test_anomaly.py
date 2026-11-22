from src.simulator import ChamberSim
from src.anomaly import ChamberMonitor, RANK

CH = {"id": 1, "name": "Chamber 1", "crop": "Potato", "setpoint": 3.0, "temp_max": 5.0, "temp_min": 1.0}


def warmed(seed, minutes=600):
    sim = ChamberSim(1, 3.0, seed=seed)
    mon = ChamberMonitor(CH, lazy_ml=True)
    for m in range(minutes):
        mon.update(sim.step(m))
    return sim, mon, minutes


def test_baseline_is_learned():
    _, mon, _ = warmed(1)
    assert mon.learned and 2.7 < mon.median < 3.3 and mon.upper > mon.median


def test_normal_operation_has_no_alert_for_two_days():
    sim = ChamberSim(1, 3.0, seed=3)
    mon = ChamberMonitor(CH, lazy_ml=True)
    for m in range(2 * 1440):
        a = mon.update(sim.step(m))
        if m > 400:
            assert a.level == "OK", (m, a.reasons)


def test_short_door_opening_is_not_an_alert():
    sim, mon, m0 = warmed(2)
    for k in range(5):
        a = mon.update({"temp_c": 3.4 + 0.2 * k, "humidity_pct": 88, "door_open": True, "compressor_on": True})
    assert a.level == "OK"


def test_compressor_trip_is_flagged_before_fixed_limit_with_stages():
    for seed in range(4):
        sim, mon, m0 = warmed(10 + seed)
        sim.set_scenario("compressor_trip")
        first = {}
        t_fixed = None
        for k in range(1, 200):
            r = sim.step(m0 + k)
            a = mon.update(r)
            for lv in ("WATCH", "WARNING", "CRITICAL"):
                if RANK[a.level] >= RANK[lv]:
                    first.setdefault(lv, k)
            if r["temp_c"] >= CH["temp_max"]:
                t_fixed = k
                break
        assert t_fixed and first["WATCH"] < t_fixed
        assert first["WATCH"] <= first["WARNING"] <= first["CRITICAL"]


def test_door_left_open_escalates():
    sim, mon, m0 = warmed(5)
    sim.set_scenario("door_left_open")
    levels = set()
    for k in range(1, 40):
        levels.add(mon.update(sim.step(m0 + k)).level)
    assert "CRITICAL" in levels


def test_limit_crossing_is_always_critical():
    _, mon, _ = warmed(6)
    a = mon.update({"temp_c": 5.2, "humidity_pct": 90, "door_open": False})
    assert a.level == "CRITICAL"
